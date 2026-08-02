"""Netzbezugs-Sperre.

Uebersetzt aus test/grid-guard.test.ts des EcoFlow-Monitors. Die Testnamen sind
wortgleich uebernommen - sie sind die eigentliche Spezifikation der Regelung.
"""

from __future__ import annotations

from dataclasses import replace

from custom_components.pv_surplus.control.grid_guard import (
    IMPORT_THRESHOLD_W,
    RAISE_INTERVAL_S,
    REACT_AFTER_S,
    GuardInput,
    GuardState,
    apply_cap,
    empty_guard_state,
    update_guard,
)

T0 = 1_000_000.0


def basis(**over) -> GuardInput:
    """Ein unauffaelliger Takt: kein Bezug, Wallbox laedt mit 16 A dreiphasig."""
    werte = {
        "grid_power_w": 0.0,
        "charging": True,
        "current_limit_a": 16,
        "phases": 3,
        "min_current_a": 6,
        "max_current_a": 16,
        "now": T0,
    }
    werte.update(over)
    return GuardInput(**werte)


def takte(state: GuardState, eingaben: list[GuardInput]) -> GuardState:
    """Mehrere Takte hintereinander, damit Zeitschwellen realistisch reifen."""
    for e in eingaben:
        state = update_guard(state, e)
    return state


class TestNetzbezugsSperre:
    def test_greift_nicht_ein_solange_kein_bezug_anliegt(self) -> None:
        s = update_guard(empty_guard_state(), basis(grid_power_w=-2000.0))
        assert s.cap_a is None

    def test_ignoriert_messrauschen_unterhalb_der_schwelle(self) -> None:
        s = takte(
            empty_guard_state(),
            [
                basis(grid_power_w=IMPORT_THRESHOLD_W - 1, now=T0),
                basis(grid_power_w=IMPORT_THRESHOLD_W - 1, now=T0 + REACT_AFTER_S + 1),
            ],
        )
        assert s.cap_a is None

    def test_wartet_die_reaktionszeit_ab_statt_sofort_zu_drosseln(self) -> None:
        s = takte(
            empty_guard_state(),
            [
                basis(grid_power_w=2000.0, now=T0),
                basis(grid_power_w=2000.0, now=T0 + REACT_AFTER_S - 0.001),
            ],
        )
        assert s.cap_a is None

    def test_deckelt_wenn_der_bezug_anhaelt(self) -> None:
        s = takte(
            empty_guard_state(),
            [
                basis(grid_power_w=2000.0, now=T0),
                basis(grid_power_w=2000.0, now=T0 + REACT_AFTER_S + 1),
            ],
        )
        # 2000 W dreiphasig sind rund 2,9 A -> aufgerundet 3 A weniger als 16
        assert s.cap_a == 13
        assert s.reason is not None and "2000 W" in s.reason

    def test_pausiert_ganz_wenn_der_rest_unter_den_mindeststrom_faellt(self) -> None:
        s = takte(
            empty_guard_state(),
            [
                basis(grid_power_w=8000.0, now=T0),
                basis(grid_power_w=8000.0, now=T0 + REACT_AFTER_S + 1),
            ],
        )
        # 8000 W sind rund 12 A; 16 - 12 = 4 A liegt unter dem Mindeststrom 6 A
        assert s.cap_a == 0

    def test_greift_nicht_wenn_die_wallbox_gar_nicht_laedt(self) -> None:
        # Bezug durch Herd oder Waermepumpe ist nicht Sache der Ladesteuerung
        s = takte(
            empty_guard_state(),
            [
                basis(grid_power_w=3000.0, charging=False, now=T0),
                basis(grid_power_w=3000.0, charging=False, now=T0 + REACT_AFTER_S + 1),
            ],
        )
        assert s.cap_a is None

    def test_senkt_bei_anhaltendem_bezug_weiter_nach(self) -> None:
        s = takte(
            empty_guard_state(),
            [
                basis(grid_power_w=1000.0, now=T0),
                basis(grid_power_w=1000.0, now=T0 + REACT_AFTER_S + 1),
            ],
        )
        assert s.cap_a == 14  # 1000 W ~ 1,4 A -> 2 A weniger
        s = update_guard(s, basis(grid_power_w=1000.0, now=T0 + 2 * REACT_AFTER_S + 2))
        assert s.cap_a == 12

    def test_gibt_nach_entspannung_schrittweise_wieder_frei(self) -> None:
        s = takte(
            empty_guard_state(),
            [
                basis(grid_power_w=2000.0, now=T0),
                basis(grid_power_w=2000.0, now=T0 + REACT_AFTER_S + 1),
            ],
        )
        assert s.cap_a == 13

        # Kein Bezug mehr: nach einem Anhebe-Intervall ein Ampere mehr
        t = T0 + REACT_AFTER_S + 1
        s = update_guard(s, basis(grid_power_w=-500.0, now=t))
        assert s.cap_a == 13  # erst der Zeitpunkt wird gemerkt
        t += RAISE_INTERVAL_S + 1
        s = update_guard(s, basis(grid_power_w=-500.0, now=t))
        assert s.cap_a == 14

    def test_hebt_aus_der_pause_direkt_auf_den_mindeststrom(self) -> None:
        s = GuardState(cap_a=0, import_since=None, last_raise=T0, reason="test")
        s = update_guard(s, basis(grid_power_w=-3000.0, now=T0 + RAISE_INTERVAL_S + 1))
        # Werte zwischen 1 und 5 A kann die Wallbox nicht fahren
        assert s.cap_a == 6

    def test_loest_den_deckel_ganz_auf_sobald_das_maximum_erreicht_ist(self) -> None:
        s = GuardState(cap_a=15, import_since=None, last_raise=T0, reason="test")
        s = update_guard(s, basis(grid_power_w=-3000.0, now=T0 + RAISE_INTERVAL_S + 1))
        assert s.cap_a is None
        assert s.reason is None

    def test_aendert_nichts_solange_kein_netzwert_vorliegt(self) -> None:
        vorher = GuardState(cap_a=10, import_since=5.0, last_raise=5.0, reason="test")
        s = update_guard(vorher, basis(grid_power_w=None))
        assert s.cap_a == 10

    def test_laesst_den_ausgangszustand_unveraendert(self) -> None:
        vorher = empty_guard_state()
        update_guard(vorher, basis(grid_power_w=5000.0, now=2_000_000.0))
        assert vorher.import_since is None


class TestApplyCap:
    def test_laesst_ohne_deckel_alles_durch(self) -> None:
        assert apply_cap(16, empty_guard_state()) == 16

    def test_begrenzt_auf_den_deckel(self) -> None:
        assert apply_cap(16, replace(empty_guard_state(), cap_a=8)) == 8

    def test_hebt_einen_niedrigeren_wunsch_nicht_an(self) -> None:
        assert apply_cap(6, replace(empty_guard_state(), cap_a=12)) == 6
