"""Der Regelkreis: Modus-Matrix, Pause-Hysterese, Schutzmechanismen.

Diese Logik war in der TypeScript-Vorlage mit I/O verwoben und deshalb nie
getestet. Die Faelle hier sind neu geschrieben, nicht uebersetzt - insbesondere
die Pause-Hysterese, die im Alltag am haeufigsten sichtbar wird.
"""

from __future__ import annotations

from dataclasses import replace

from custom_components.pv_surplus.control.grid_guard import REACT_AFTER_S
from custom_components.pv_surplus.control.models import (
    ControlInputs,
    ControllerState,
    ControlSettings,
    Mode,
    StaleAction,
    Status,
)
from custom_components.pv_surplus.control.strategy import decide

T0 = 1_000_000.0


def eingabe(**over) -> ControlInputs:
    """Einphasige Wallbox, 2 kW Einspeisung, Auto laedt gerade nicht."""
    werte = {
        "now": T0,
        "grid_power_w": -2000.0,
        "pv_power_w": 3000.0,
        "battery_power_w": 0.0,
        "ev_power_w": 0.0,
        "current_limit_a": 0,
        "phases": 1,
    }
    werte.update(over)
    return ControlInputs(**werte)


def einstellungen(**over) -> ControlSettings:
    werte = {"min_current_a": 6, "max_current_a": 16, "voltage_v": 230.0}
    werte.update(over)
    return ControlSettings(**werte)


class TestModi:
    def test_haelt_sich_im_modus_aus_vollstaendig_heraus(self) -> None:
        d = decide(ControllerState(), eingabe(), einstellungen(), Mode.OFF)
        assert d.should_apply is False
        assert d.status is Status.OFF

    def test_rechnet_im_pv_modus_den_ueberschuss_in_ampere(self) -> None:
        # 2000 W einphasig sind 8,7 A -> abgerundet 8
        d = decide(ControllerState(), eingabe(), einstellungen(), Mode.PV)
        assert d.target_a == 8
        assert d.status is Status.CHARGING

    def test_gibt_im_max_modus_den_hoechststrom_frei(self) -> None:
        d = decide(ControllerState(), eingabe(), einstellungen(), Mode.MAX)
        assert d.target_a == 16

    def test_haelt_im_manuellen_modus_den_eingestellten_strom(self) -> None:
        d = decide(
            ControllerState(), eingabe(), einstellungen(), Mode.MANUAL, manual_a=10
        )
        assert d.target_a == 10

    def test_begrenzt_den_manuellen_strom_auf_das_maximum(self) -> None:
        d = decide(
            ControllerState(), eingabe(), einstellungen(), Mode.MANUAL, manual_a=32
        )
        assert d.target_a == 16

    def test_ignoriert_im_manuellen_modus_den_ueberschuss(self) -> None:
        d = decide(
            ControllerState(),
            eingabe(grid_power_w=3000.0),  # dicker Netzbezug
            einstellungen(),
            Mode.MANUAL,
            manual_a=10,
        )
        assert d.target_a == 10


class TestNetzsperreImRegelkreis:
    def test_deckelt_auch_im_manuellen_modus(self) -> None:
        # Ein Schutz, den man einschalten muss, schuetzt nicht
        s = ControllerState()
        for t in (T0, T0 + REACT_AFTER_S + 1):
            d = decide(
                s,
                eingabe(
                    now=t, grid_power_w=2000.0, ev_power_w=3000.0, current_limit_a=16
                ),
                einstellungen(),
                Mode.MANUAL,
                manual_a=16,
            )
            s = d.state
        assert d.target_a < 16
        assert d.status is Status.GRID_LIMITED

    def test_greift_im_modus_aus_nicht(self) -> None:
        s = ControllerState()
        for t in (T0, T0 + REACT_AFTER_S + 1):
            d = decide(
                s,
                eingabe(now=t, grid_power_w=5000.0, ev_power_w=3000.0),
                einstellungen(),
                Mode.OFF,
            )
            s = d.state
        assert s.guard.cap_a is None


class TestPauseHysterese:
    def test_laedt_bei_wenig_ueberschuss_erst_einmal_mit_mindeststrom_weiter(
        self,
    ) -> None:
        # Wolkenluecke: Ueberschuss reicht nicht mehr, aber das Auto laedt
        d = decide(
            ControllerState(),
            eingabe(grid_power_w=-500.0, ev_power_w=1400.0),
            einstellungen(),
            Mode.PV,
        )
        assert d.target_a == 6

    def test_pausiert_erst_wenn_die_luecke_lang_genug_dauert(self) -> None:
        s = ControllerState()
        d = decide(
            s,
            eingabe(grid_power_w=-500.0, ev_power_w=1400.0),
            einstellungen(),
            Mode.PV,
        )
        assert d.target_a == 6
        d = decide(
            d.state,
            eingabe(now=T0 + 181.0, grid_power_w=-500.0, ev_power_w=1400.0),
            einstellungen(),
            Mode.PV,
        )
        assert d.target_a == 0
        assert d.status is Status.PAUSED

    def test_pausiert_sofort_wenn_die_wallbox_ohnehin_steht(self) -> None:
        # Keine Anlauf-Hysterese nach oben: erst wenn es wirklich reicht
        d = decide(
            ControllerState(),
            eingabe(grid_power_w=-500.0, ev_power_w=0.0),
            einstellungen(),
            Mode.PV,
        )
        assert d.target_a == 0

    def test_vergisst_die_wartezeit_sobald_der_ueberschuss_zurueckkommt(self) -> None:
        s = ControllerState()
        d = decide(
            s, eingabe(grid_power_w=-500.0, ev_power_w=1400.0), einstellungen(), Mode.PV
        )
        assert d.state.below_min_since is not None
        d = decide(
            d.state,
            eingabe(now=T0 + 60.0, grid_power_w=-3000.0, ev_power_w=1400.0),
            einstellungen(),
            Mode.PV,
        )
        assert d.state.below_min_since is None
        assert d.target_a == 13


class TestFahrzeugSoc:
    def test_laedt_im_minpv_modus_ein_leeres_auto_notfalls_aus_dem_netz(self) -> None:
        d = decide(
            ControllerState(),
            eingabe(grid_power_w=0.0, ev_power_w=0.0, vehicle_soc=10.0),
            einstellungen(min_soc=20.0),
            Mode.MINPV,
        )
        assert d.target_a == 6

    def test_tut_das_im_reinen_pv_modus_nicht(self) -> None:
        d = decide(
            ControllerState(),
            eingabe(grid_power_w=0.0, ev_power_w=0.0, vehicle_soc=10.0),
            einstellungen(min_soc=20.0),
            Mode.PV,
        )
        assert d.target_a == 0

    def test_laedt_nicht_nach_wenn_das_auto_sein_ziel_erreicht_hat(self) -> None:
        d = decide(
            ControllerState(),
            eingabe(
                grid_power_w=0.0,
                ev_power_w=0.0,
                vehicle_soc=80.0,
                vehicle_target_soc=80.0,
            ),
            einstellungen(min_soc=90.0),
            Mode.MINPV,
        )
        assert d.target_a == 0


class TestHausbatterieSchutz:
    def test_laedt_nicht_wenn_die_hausbatterie_unter_der_reserve_liegt(self) -> None:
        d = decide(
            ControllerState(),
            eingabe(battery_soc=15.0),
            einstellungen(battery_reserve_soc=30.0),
            Mode.PV,
        )
        assert d.target_a == 0
        assert any("Hausbatterie" in r for r in d.reasons)

    def test_laedt_oberhalb_der_reserve_normal(self) -> None:
        d = decide(
            ControllerState(),
            eingabe(battery_soc=50.0),
            einstellungen(battery_reserve_soc=30.0),
            Mode.PV,
        )
        assert d.target_a == 8

    def test_ist_bei_reserve_null_abgeschaltet(self) -> None:
        d = decide(
            ControllerState(),
            eingabe(battery_soc=5.0),
            einstellungen(battery_reserve_soc=0.0),
            Mode.PV,
        )
        assert d.target_a == 8


class TestDatenausfall:
    def test_pausiert_wenn_der_zaehler_zu_lange_schweigt(self) -> None:
        d = decide(
            replace(ControllerState(), last_target_a=12),
            eingabe(grid_power_w=None, grid_missing_since=T0 - 121.0),
            einstellungen(stale_action=StaleAction.PAUSE),
            Mode.PV,
        )
        assert d.target_a == 0
        assert d.status is Status.STALE

    def test_haelt_auf_wunsch_das_letzte_limit(self) -> None:
        d = decide(
            replace(ControllerState(), last_target_a=12),
            eingabe(grid_power_w=None, grid_missing_since=T0 - 121.0),
            einstellungen(stale_action=StaleAction.HOLD),
            Mode.PV,
        )
        assert d.target_a == 12
        assert d.should_apply is False

    def test_ueberbrueckt_kurze_luecken_ohne_eingriff(self) -> None:
        d = decide(
            replace(ControllerState(), last_target_a=12),
            eingabe(grid_power_w=None, grid_missing_since=T0 - 10.0),
            einstellungen(),
            Mode.PV,
        )
        assert d.status is not Status.STALE


class TestTastBetriebImRegelkreis:
    def test_tastet_bei_nulleinspeisung_ueberhaupt_erst_eine_ladung_an(self) -> None:
        # Der Kern des Ganzen: ohne Tasten bleibt der Sollstrom hier auf 0
        ohne = decide(
            ControllerState(),
            eingabe(grid_power_w=0.0),
            einstellungen(pv_probe=False),
            Mode.PV,
        )
        mit = decide(
            ControllerState(),
            eingabe(grid_power_w=0.0),
            einstellungen(pv_probe=True),
            Mode.PV,
        )
        assert ohne.target_a == 0
        assert mit.target_a == 6
        assert mit.status is Status.PROBING

    def test_laesst_den_uebergebenen_zustand_unveraendert(self) -> None:
        vorher = ControllerState()
        decide(vorher, eingabe(grid_power_w=0.0), einstellungen(pv_probe=True), Mode.PV)
        assert vorher.probe.last_step_at is None
        assert vorher.last_target_a is None
