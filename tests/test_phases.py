"""Automatische Phasenumschaltung.

Neuentwicklung ohne Vorlage - hier sind die Tests die Spezifikation, nicht die
Uebersetzung einer. Der teuerste Fehler waere Pendeln: Jede Umschaltung kostet
eine Ladepause, und manche Fahrzeuge laufen danach nur widerwillig wieder an.
"""

from __future__ import annotations

from dataclasses import replace

from custom_components.pv_surplus.control.phases import (
    BUDGET_WINDOW_S,
    DOWN_HOLD_S,
    MIN_DWELL_S,
    START_TIMEOUT_S,
    STOP_TIMEOUT_S,
    SWITCH_BUDGET,
    UP_HOLD_S,
    WAIT_CAR_S,
    PhaseCommand,
    PhaseInput,
    PhaseMode,
    PhaseState,
    plan_phases,
)

T0 = 1_000_000.0


def eingabe(**over) -> PhaseInput:
    """Umschaltfaehige Wallbox, Auto steckt und laedt einphasig."""
    werte = {
        "now": T0,
        "plugged": True,
        "ev_power_w": 1400.0,
        "surplus_w": 1500.0,
        "can_switch": True,
        "switching_enabled": True,
        "min_current_a": 6,
        "max_current_1p_a": 16,
        "voltage_v": 230.0,
    }
    werte.update(over)
    return PhaseInput(**werte)


def bereit(**over) -> PhaseState:
    """Einphasig, Anlaufschonzeit vorbei."""
    werte = {"switch_phases": 1, "target_phases": 1, "charging_since": T0 - 600.0}
    werte.update(over)
    return PhaseState(**werte)


def takte(state: PhaseState, eingaben: list[PhaseInput]) -> PhaseState:
    for e in eingaben:
        state = plan_phases(state, e).state
    return state


class TestSchwellen:
    def test_schaltet_nicht_hoch_solange_einphasig_noch_luft_hat(self) -> None:
        # 3500 W: einphasig noch regelbar, dreiphasig ginge gar nicht
        s = takte(
            bereit(),
            [eingabe(now=T0 + i * 15.0, surplus_w=3500.0) for i in range(40)],
        )
        assert s.switch_phases == 1

    def test_schaltet_in_der_toten_zone_zwischen_den_stufen_nicht_um(self) -> None:
        # 3900 W liegen ueber dem einphasigen Maximum, aber unter dem
        # dreiphasigen Minimum von 4140 W - Umschalten braechte nichts
        s = takte(
            bereit(),
            [eingabe(now=T0 + i * 15.0, surplus_w=3900.0) for i in range(40)],
        )
        assert s.switch_phases == 1

    def test_schaltet_erst_hoch_wenn_die_haltezeit_abgelaufen_ist(self) -> None:
        s = bereit()
        r = plan_phases(s, eingabe(surplus_w=5200.0))
        s = r.state
        assert s.mode is PhaseMode.ARMED_UP
        # Kurz vor Ablauf: noch nichts
        r = plan_phases(s, eingabe(now=T0 + UP_HOLD_S - 1, surplus_w=5200.0))
        assert not r.commands
        r = plan_phases(r.state, eingabe(now=T0 + UP_HOLD_S + 1, surplus_w=5200.0))
        assert r.commands

    def test_vergisst_die_bedingung_wenn_der_ueberschuss_einbricht(self) -> None:
        s = plan_phases(bereit(), eingabe(surplus_w=5200.0)).state
        assert s.pending_since is not None
        s = plan_phases(s, eingabe(now=T0 + 60.0, surplus_w=2000.0)).state
        assert s.pending_since is None

    def test_schaltet_runter_wenn_dreiphasig_nicht_mehr_regelbar_ist(self) -> None:
        s = bereit(switch_phases=3, target_phases=3)
        r = plan_phases(s, eingabe(surplus_w=3000.0, ev_power_w=0.0))
        s = r.state
        r = plan_phases(
            s, eingabe(now=T0 + DOWN_HOLD_S + 1, surplus_w=3000.0, ev_power_w=0.0)
        )
        assert r.state.switch_phases == 1

    def test_reagiert_beim_runterschalten_schneller_als_beim_hochschalten(self) -> None:
        assert DOWN_HOLD_S < UP_HOLD_S

    def test_verschiebt_die_schwellen_bei_einem_zweiphasigen_fahrzeug(self) -> None:
        # Zweiphasig sind 6 A nur 2760 W - da lohnt Umschalten frueher
        zwei = bereit(vehicle_max_phases=2)
        drei = bereit()
        e = eingabe()
        from custom_components.pv_surplus.control.phases import upper_threshold_w

        assert upper_threshold_w(zwei, e) < upper_threshold_w(drei, e)


class TestPendelschutz:
    def test_pendelt_nicht_wenn_der_ueberschuss_um_die_schwelle_schwankt(self) -> None:
        s = bereit()
        werte = [3900.0, 4900.0] * 240  # zwei Stunden im 15-s-Takt
        schaltungen = 0
        for i, w in enumerate(werte):
            r = plan_phases(s, eingabe(now=T0 + i * 15.0, surplus_w=w))
            s = r.state
            schaltungen += sum(1 for c, _ in r.commands if c is PhaseCommand.SET_PHASES)
        assert schaltungen == 0

    def test_bleibt_nach_einer_umschaltung_die_mindestverweildauer_stehen(self) -> None:
        s = bereit(
            switch_phases=3,
            target_phases=3,
            mode=PhaseMode.COOLDOWN,
            last_switch_at=T0,
        )
        s = takte(
            s,
            [
                eingabe(now=T0 + i * 15.0, surplus_w=2000.0, ev_power_w=0.0)
                for i in range(1, 30)
            ],
        )
        assert s.switch_phases == 3

    def test_schaltet_nach_ablauf_der_mindestverweildauer_wieder_um(self) -> None:
        s = bereit(
            switch_phases=3,
            target_phases=3,
            mode=PhaseMode.COOLDOWN,
            last_switch_at=T0,
        )
        t = T0 + MIN_DWELL_S + 1
        s = takte(
            s,
            [
                eingabe(now=t + i * 15.0, surplus_w=2000.0, ev_power_w=0.0)
                for i in range(20)
            ],
        )
        assert s.switch_phases == 1

    def test_friert_die_phasenzahl_ein_wenn_das_budget_erschoepft_ist(self) -> None:
        s = bereit(switch_times=tuple(T0 - i * 60.0 for i in range(SWITCH_BUDGET)))
        r = plan_phases(s, eingabe(surplus_w=5200.0))
        r = plan_phases(r.state, eingabe(now=T0 + UP_HOLD_S + 1, surplus_w=5200.0))
        assert not r.commands
        assert r.reason is not None and "budget" in r.reason.lower()

    def test_gibt_das_budget_frei_sobald_das_fenster_weiterrueckt(self) -> None:
        alt = T0 - BUDGET_WINDOW_S - 100.0
        s = bereit(switch_times=tuple(alt for _ in range(SWITCH_BUDGET)))
        r = plan_phases(s, eingabe(surplus_w=5200.0))
        r = plan_phases(r.state, eingabe(now=T0 + UP_HOLD_S + 1, surplus_w=5200.0))
        assert r.commands

    def test_schaltet_in_den_ersten_minuten_nach_ladebeginn_nicht_um(self) -> None:
        s = PhaseState(switch_phases=1, charging_since=T0)
        s = takte(s, [eingabe(now=T0 + i * 15.0, surplus_w=5200.0) for i in range(10)])
        assert s.mode is not PhaseMode.SEQ_STOP


class TestSequenz:
    def test_schaltet_ohne_laufende_ladung_sofort_und_ohne_stoppbefehl(self) -> None:
        s = bereit()
        r = plan_phases(s, eingabe(surplus_w=5200.0, ev_power_w=0.0))
        r = plan_phases(
            r.state, eingabe(now=T0 + UP_HOLD_S + 1, surplus_w=5200.0, ev_power_w=0.0)
        )
        befehle = [c for c, _ in r.commands]
        assert PhaseCommand.SET_PHASES in befehle
        assert PhaseCommand.STOP_CHARGE not in befehle
        assert r.state.switch_phases == 3

    def test_stoppt_die_ladung_bevor_die_phasen_geschaltet_werden(self) -> None:
        s = bereit()
        r = plan_phases(s, eingabe(surplus_w=5200.0))
        r = plan_phases(r.state, eingabe(now=T0 + UP_HOLD_S + 1, surplus_w=5200.0))
        assert [c for c, _ in r.commands] == [PhaseCommand.STOP_CHARGE]
        assert r.hold_current_a == 0

    def test_schaltet_die_phasen_erst_wenn_kein_ladestrom_mehr_fliesst(self) -> None:
        s = bereit(mode=PhaseMode.SEQ_STOP, target_phases=3, step_since=T0)
        r = plan_phases(s, eingabe(now=T0 + 5.0, ev_power_w=3000.0))
        assert not r.commands
        r = plan_phases(s, eingabe(now=T0 + 5.0, ev_power_w=0.0))
        assert [c for c, _ in r.commands] == [PhaseCommand.SET_PHASES]

    def test_bricht_ab_wenn_die_ladung_nicht_rechtzeitig_endet(self) -> None:
        s = bereit(mode=PhaseMode.SEQ_STOP, target_phases=3, step_since=T0)
        r = plan_phases(s, eingabe(now=T0 + STOP_TIMEOUT_S + 1, ev_power_w=3000.0))
        assert r.state.failures == 1
        assert r.state.switch_phases == 1

    def test_rechnet_im_startschritt_bereits_mit_der_neuen_phasenzahl(self) -> None:
        s = bereit(mode=PhaseMode.SEQ_SET, target_phases=3, step_since=T0)
        r = plan_phases(s, eingabe(now=T0 + 6.0, ev_power_w=0.0, reported_phases=3))
        assert r.phases == 3
        assert r.reset_probe_ceiling is True

    def test_wartet_auf_ein_schlafendes_fahrzeug_und_gibt_dann_auf(self) -> None:
        s = bereit(mode=PhaseMode.SEQ_START, switch_phases=3, step_since=T0)
        r = plan_phases(s, eingabe(now=T0 + START_TIMEOUT_S, ev_power_w=0.0))
        assert r.state.mode is PhaseMode.SEQ_START
        r = plan_phases(s, eingabe(now=T0 + WAIT_CAR_S + 1, ev_power_w=0.0))
        assert r.state.mode is PhaseMode.BLOCKED

    def test_sperrt_nach_drei_fehlgeschlagenen_versuchen(self) -> None:
        s = bereit(mode=PhaseMode.SEQ_STOP, target_phases=3, step_since=T0, failures=2)
        r = plan_phases(s, eingabe(now=T0 + STOP_TIMEOUT_S + 1, ev_power_w=3000.0))
        assert r.state.mode is PhaseMode.BLOCKED

    def test_hebt_die_sperre_beim_naechsten_anstecken_auf(self) -> None:
        s = bereit(mode=PhaseMode.BLOCKED, blocked_reason="test", failures=3)
        r = plan_phases(s, eingabe(plug_epoch=1, surplus_w=2000.0))
        assert r.state.mode is not PhaseMode.BLOCKED
        assert r.state.failures == 0


class TestZusammenspiel:
    def test_schaltet_nicht_hoch_solange_die_netzsperre_deckelt(self) -> None:
        s = bereit()
        s = takte(
            s,
            [
                eingabe(now=T0 + i * 15.0, surplus_w=5200.0, guard_capped=True)
                for i in range(40)
            ],
        )
        assert s.switch_phases == 1

    def test_schaltet_runter_wenn_die_sperre_dreiphasig_bis_auf_null_deckelt(
        self,
    ) -> None:
        # Der Beweis liegt vor, dass dreiphasig nicht traegt - kurze Haltezeit
        s = bereit(switch_phases=3, target_phases=3)
        r = plan_phases(
            s,
            eingabe(surplus_w=2000.0, ev_power_w=0.0, guard_capped=True, guard_cap_a=0),
        )
        r = plan_phases(
            r.state,
            eingabe(
                now=T0 + 61.0,
                surplus_w=2000.0,
                ev_power_w=0.0,
                guard_capped=True,
                guard_cap_a=0,
            ),
        )
        assert r.state.switch_phases == 1

    def test_friert_sperre_und_tastregler_waehrend_der_umschaltung_ein(self) -> None:
        s = bereit(mode=PhaseMode.SEQ_STOP, target_phases=3, step_since=T0)
        r = plan_phases(s, eingabe(now=T0 + 5.0, ev_power_w=3000.0))
        assert r.freeze_guard is True
        assert r.freeze_probe is True

    def test_verwirft_die_getastete_obergrenze_nach_dem_phasenwechsel(self) -> None:
        # 12 A einphasig sind 2760 W, dreiphasig aber 8280 W - eine in Ampere
        # gemerkte Grenze gilt nach dem Wechsel nicht mehr
        s = bereit(mode=PhaseMode.SEQ_SET, target_phases=3, step_since=T0)
        r = plan_phases(s, eingabe(now=T0 + 6.0, ev_power_w=0.0, reported_phases=3))
        assert r.reset_probe_ceiling is True

    def test_leitet_aus_dem_getasteten_strom_niemals_eine_aufschaltung_ab(self) -> None:
        # surplus_w ist der GEMESSENE Ueberschuss; bei Nulleinspeisung 0
        s = takte(
            bereit(),
            [
                eingabe(now=T0 + i * 15.0, surplus_w=0.0, ev_power_w=3600.0)
                for i in range(60)
            ],
        )
        assert s.switch_phases == 1


class TestErkennung:
    def test_erkennt_die_phasenzahl_erst_nach_mehreren_messungen(self) -> None:
        s = bereit()
        e = eingabe(ev_power_w=3000.0, phase_currents=(10.0, 0.0, 0.0))
        s1 = plan_phases(s, e).state
        assert s1.detected_phases is None
        s2 = plan_phases(s1, replace(e, now=T0 + 15.0)).state
        s3 = plan_phases(s2, replace(e, now=T0 + 30.0)).state
        assert s3.detected_phases == 1

    def test_wertet_die_stroeme_in_der_ersten_minute_nicht_aus(self) -> None:
        s = PhaseState(switch_phases=3, charging_since=None)
        e = eingabe(ev_power_w=3000.0, phase_currents=(10.0, 10.0, 10.0))
        s = plan_phases(s, e).state
        s = plan_phases(s, replace(e, now=T0 + 30.0)).state
        assert s.detected_phases is None

    def test_ignoriert_stroeme_unterhalb_der_erkennungsschwelle(self) -> None:
        s = bereit(switch_phases=3)
        e = eingabe(ev_power_w=3000.0, phase_currents=(10.0, 1.5, 1.5))
        for i in range(4):
            s = plan_phases(s, replace(e, now=T0 + i * 15.0)).state
        assert s.detected_phases == 1

    def test_erkennt_ein_nur_einphasig_ladendes_fahrzeug_und_stellt_das_um_ein(
        self,
    ) -> None:
        s = bereit(switch_phases=3, target_phases=3)
        e = eingabe(ev_power_w=3000.0, phase_currents=(14.0, 0.0, 0.0))
        for i in range(4):
            r = plan_phases(s, replace(e, now=T0 + i * 15.0))
            s = r.state
        assert s.vehicle_max_phases == 1
        assert s.mode is PhaseMode.BLOCKED
        # Der eigentliche Ertragsfehler: trotz dreiphasiger Stellung mit 1 rechnen
        assert r.phases == 1

    def test_vergisst_die_fahrzeugerkennung_beim_naechsten_anstecken(self) -> None:
        s = bereit(vehicle_max_phases=1, detected_phases=1)
        r = plan_phases(s, eingabe(plug_epoch=1))
        assert r.state.vehicle_max_phases is None


class TestRandfaelle:
    def test_erzeugt_ohne_umschaltfaehige_wallbox_niemals_ein_kommando(self) -> None:
        s = bereit()
        for i in range(60):
            r = plan_phases(
                s, eingabe(now=T0 + i * 15.0, surplus_w=6000.0, can_switch=False)
            )
            s = r.state
            assert not r.commands

    def test_greift_ohne_freigabe_des_nutzers_nicht_ein(self) -> None:
        s = takte(
            bereit(),
            [
                eingabe(now=T0 + i * 15.0, surplus_w=6000.0, switching_enabled=False)
                for i in range(60)
            ],
        )
        assert s.switch_phases == 1

    def test_greift_in_festen_modi_nicht_ein(self) -> None:
        s = takte(
            bereit(),
            [
                eingabe(
                    now=T0 + i * 15.0, surplus_w=6000.0, mode_allows_switching=False
                )
                for i in range(60)
            ],
        )
        assert s.switch_phases == 1

    def test_uebernimmt_eine_fremde_umschaltung_statt_dagegen_anzuregeln(self) -> None:
        r = plan_phases(bereit(), eingabe(reported_phases=3))
        assert r.state.switch_phases == 3
        assert r.state.mode is PhaseMode.COOLDOWN
        assert r.reset_probe_ceiling is True

    def test_aendert_nichts_ohne_ueberschusswert(self) -> None:
        r = plan_phases(bereit(), eingabe(surplus_w=None))
        assert not r.commands

    def test_laesst_den_uebergebenen_zustand_unveraendert(self) -> None:
        vorher = bereit()
        plan_phases(vorher, eingabe(surplus_w=6000.0))
        assert vorher.pending_since is None
        assert vorher.mode is PhaseMode.STABLE
