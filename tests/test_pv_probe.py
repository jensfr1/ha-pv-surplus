"""Tast-Betrieb bei abgeregelter Anlage.

Uebersetzt aus test/pv-probe.test.ts des EcoFlow-Monitors. Die Testnamen sind
wortgleich uebernommen - sie sind die eigentliche Spezifikation der Regelung.
"""

from __future__ import annotations

from custom_components.pv_surplus.control.pv_probe import (
    CEILING_TTL_S,
    MIN_PV_W,
    STEP_INTERVAL_S,
    ProbeInput,
    ProbeState,
    empty_probe_state,
    probe_target,
)

T0 = 1_000_000.0


def eingabe(**over) -> ProbeInput:
    """Nulleinspeisung bei 3 kW PV - die Lage, fuer die es den Regler gibt."""
    werte = {
        "desired_a": 0,
        "current_limit_a": 0,
        "grid_power_w": 0.0,
        "battery_power_w": 0.0,
        "pv_power_w": 3000.0,
        "min_current_a": 6,
        "max_current_a": 16,
        "now": T0,
    }
    werte.update(over)
    return ProbeInput(**werte)


class TestTastBetrieb:
    def test_startet_aus_dem_stand_mit_dem_mindeststrom(self) -> None:
        # Nulleinspeisung: Ueberschuss 0, obwohl das Dach koennte
        r = probe_target(empty_probe_state(), eingabe())
        assert r.target_a == 6
        assert r.reason is not None and "taste auf 6 A" in r.reason

    def test_wartet_zwischen_zwei_schritten(self) -> None:
        erster = probe_target(empty_probe_state(), eingabe())
        zu_frueh = probe_target(
            erster.state,
            eingabe(current_limit_a=6, now=T0 + STEP_INTERVAL_S - 0.001),
        )
        assert zu_frueh.target_a == 6
        assert zu_frueh.reason is None

    def test_tastet_sich_ampere_fuer_ampere_nach_oben(self) -> None:
        s: ProbeState = empty_probe_state()
        limit = 0
        t = T0
        for _ in range(4):
            r = probe_target(s, eingabe(current_limit_a=limit, now=t))
            s = r.state
            limit = r.target_a
            t += STEP_INTERVAL_S + 1
        # 6 (Start), dann 7, 8, 9
        assert limit == 9

    def test_geht_bei_netzbezug_zurueck_und_merkt_sich_die_grenze(self) -> None:
        r = probe_target(
            empty_probe_state(),
            eingabe(desired_a=0, current_limit_a=12, grid_power_w=800.0),
        )
        assert r.target_a == 11
        assert r.state.ceiling_a == 11
        assert r.reason is not None and "Netzbezug" in r.reason

    def test_geht_auch_zurueck_wenn_die_hausbatterie_einspringt(self) -> None:
        # Ohne diese Bedingung wuerde nachts das Auto aus dem Hausspeicher geladen
        r = probe_target(
            empty_probe_state(),
            eingabe(desired_a=0, current_limit_a=10, battery_power_w=-1500.0),
        )
        assert r.target_a == 9
        assert r.reason is not None and "Batterie entlaedt" in r.reason

    def test_setzt_ganz_aus_wenn_schon_der_mindeststrom_zu_viel_ist(self) -> None:
        # Sonst bliebe die Wallbox auf 6 A stehen und zoege die Differenz
        # dauerhaft aus der Hausbatterie
        r = probe_target(
            empty_probe_state(),
            eingabe(desired_a=0, current_limit_a=6, battery_power_w=-700.0),
        )
        assert r.target_a == 0
        assert r.reason is not None and "ausgesetzt" in r.reason

    def test_meldet_eine_unveraenderte_lage_nicht_in_jedem_takt_erneut(self) -> None:
        erst = probe_target(
            empty_probe_state(),
            eingabe(desired_a=0, current_limit_a=6, battery_power_w=-700.0),
        )
        assert erst.reason is not None
        nochmal = probe_target(
            erst.state,
            eingabe(
                desired_a=0,
                current_limit_a=6,
                battery_power_w=-710.0,
                now=T0 + 15.0,
            ),
        )
        assert nochmal.target_a == 0
        assert nochmal.reason is None

    def test_bleibt_unter_einer_erkannten_grenze(self) -> None:
        zurueck = probe_target(
            empty_probe_state(),
            eingabe(desired_a=0, current_limit_a=12, grid_power_w=800.0),
        )
        danach = probe_target(
            zurueck.state,
            eingabe(current_limit_a=11, now=T0 + STEP_INTERVAL_S + 1),
        )
        assert danach.target_a == 11
        assert danach.reason is None

    def test_probiert_nach_ablauf_der_sperrfrist_erneut(self) -> None:
        zurueck = probe_target(
            empty_probe_state(),
            eingabe(desired_a=0, current_limit_a=12, grid_power_w=800.0),
        )
        spaeter = probe_target(
            zurueck.state,
            eingabe(current_limit_a=11, now=T0 + CEILING_TTL_S + 1),
        )
        assert spaeter.target_a == 12
        assert spaeter.state.ceiling_a is None

    def test_tastet_nicht_ohne_nennenswerte_pv_leistung(self) -> None:
        r = probe_target(empty_probe_state(), eingabe(pv_power_w=MIN_PV_W - 1))
        assert r.target_a == 0
        assert r.reason is None

    def test_tastet_nicht_ohne_messwerte(self) -> None:
        assert (
            probe_target(empty_probe_state(), eingabe(grid_power_w=None)).target_a == 0
        )
        assert probe_target(empty_probe_state(), eingabe(pv_power_w=None)).target_a == 0

    def test_mischt_sich_nicht_ein_wenn_der_bezug_nicht_vom_tasten_kommt(self) -> None:
        # Limit entspricht der Ueberschussrechnung -> der Backofen ist schuld
        r = probe_target(
            empty_probe_state(),
            eingabe(desired_a=10, current_limit_a=10, grid_power_w=2000.0),
        )
        assert r.target_a == 10
        assert r.state.ceiling_a is None

    def test_ueberschreitet_das_maximum_nicht(self) -> None:
        r = probe_target(
            empty_probe_state(),
            eingabe(desired_a=16, current_limit_a=16, now=T0 + STEP_INTERVAL_S + 1),
        )
        assert r.target_a == 16

    def test_haelt_das_erreichte_niveau_wenn_alles_ruhig_bleibt(self) -> None:
        erster = probe_target(empty_probe_state(), eingabe())
        halten = probe_target(
            erster.state, eingabe(desired_a=6, current_limit_a=6, now=T0 + 1.0)
        )
        assert halten.target_a == 6

    def test_laesst_den_ausgangszustand_unveraendert(self) -> None:
        vorher = empty_probe_state()
        probe_target(vorher, eingabe(current_limit_a=12, grid_power_w=900.0))
        assert vorher.ceiling_a is None
