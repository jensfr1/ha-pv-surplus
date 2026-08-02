"""Der Regelkreis: aus Messwerten wird ein Ladestrom.

Portiert aus ``tick()`` in ``src/ocpp/controller.ts`` des EcoFlow-Monitors, dort
allerdings mit I/O verwoben und deshalb nie getestet. Hier als reine Funktion,
damit die Modus-Matrix und vor allem die Pause-Hysterese endlich festgeschrieben
sind.

Die Reihenfolge im Takt ist nicht beliebig:

1. Modus OFF - gar nichts, auch keine Netzsperre.
2. Netzsperre fortschreiben. Sie laeuft in allen uebrigen Modi mit, auch in
   MANUAL und MAX: Ein Schutz, den man einschalten muss, schuetzt nicht.
3. Feste Modi liefern ihren Wunschstrom.
4. Ueberschussmodi rechnen, tasten, beruecksichtigen den Fahrzeug-SoC und
   entscheiden ueber die Pause.
5. Der Deckel der Netzsperre steht ueber allem - auch ueber dem Tast-Betrieb.
"""

from __future__ import annotations

from dataclasses import replace

from .grid_guard import GuardInput, apply_cap, update_guard
from .models import (
    ControlInputs,
    ControllerState,
    ControlSettings,
    Decision,
    Mode,
    StaleAction,
    Status,
)
from .pv_probe import ProbeInput, probe_target
from .surplus import compute_surplus_w, current_from_power


def decide(
    state: ControllerState,
    inp: ControlInputs,
    settings: ControlSettings,
    mode: Mode,
    manual_a: int = 0,
) -> Decision:
    """Ein Regeltakt. Gibt neuen Zustand und Sollstrom zurueck."""
    reasons: list[str] = []

    if mode is Mode.OFF:
        # Vollstaendig heraushalten. Kein Deckel, kein Stellbefehl.
        return Decision(
            state=state,
            target_a=0,
            phases=inp.phases,
            surplus_w=None,
            status=Status.OFF,
            should_apply=False,
        )

    surplus = compute_surplus_w(
        inp.grid_power_w,
        inp.ev_power_w,
        settings.surplus_includes_ev,
        settings.surplus_reserve_w,
    )
    charging = (inp.ev_power_w or 0.0) > settings.charging_threshold_w

    # ── Netzsperre ────────────────────────────────────────────────────────────
    guard = update_guard(
        state.guard,
        GuardInput(
            now=inp.now,
            grid_power_w=inp.grid_power_w,
            charging=charging,
            current_limit_a=inp.current_limit_a,
            phases=inp.phases,
            min_current_a=settings.min_current_a,
            max_current_a=settings.max_current_a,
            voltage_v=settings.voltage_v,
        ),
    )
    if guard.cap_a != state.guard.cap_a and guard.reason:
        reasons.append(guard.reason)
    state = replace(state, guard=guard)

    # ── Datenausfall ──────────────────────────────────────────────────────────
    if _daten_fehlen(inp, settings):
        return _bei_ausfall(state, inp, settings, reasons)

    # ── Feste Modi ────────────────────────────────────────────────────────────
    if mode in (Mode.MANUAL, Mode.MAX):
        wunsch = manual_a if mode is Mode.MANUAL else settings.max_current_a
        ziel = apply_cap(max(0, min(wunsch, settings.max_current_a)), guard)
        return _fertig(state, inp, ziel, surplus, guard, reasons)

    if surplus is None:
        # Kein Netzwert, aber noch nicht lange genug fuer den Ausfallpfad.
        return _fertig(state, inp, state.last_target_a or 0, None, guard, reasons)

    # ── Ueberschuss ───────────────────────────────────────────────────────────
    ziel = min(
        current_from_power(surplus, inp.phases, settings.voltage_v),
        settings.max_current_a,
    )
    probing = False

    if settings.pv_probe:
        probe = probe_target(
            state.probe,
            ProbeInput(
                now=inp.now,
                desired_a=ziel,
                current_limit_a=inp.current_limit_a,
                grid_power_w=inp.grid_power_w,
                battery_power_w=inp.battery_power_w,
                pv_power_w=inp.pv_power_w,
                min_current_a=settings.min_current_a,
                max_current_a=settings.max_current_a,
            ),
        )
        state = replace(state, probe=probe.state)
        if probe.reason:
            reasons.append(probe.reason)
        probing = probe.target_a > ziel
        ziel = probe.target_a

    # ── Hausbatterie-Schutz ───────────────────────────────────────────────────
    if (
        settings.battery_reserve_soc > 0
        and inp.battery_soc is not None
        and inp.battery_soc < settings.battery_reserve_soc
    ):
        reasons.append(
            f"Hausbatterie bei {round(inp.battery_soc)} % - "
            f"unter der Reserve von {round(settings.battery_reserve_soc)} %"
        )
        state = replace(state, below_min_since=None)
        return _fertig(state, inp, 0, surplus, guard, reasons, status=Status.PAUSED)

    # ── Fahrzeug-SoC und Pause-Hysterese ──────────────────────────────────────
    state, ziel = _mindestladung(state, inp, settings, mode, ziel, charging)

    ziel = apply_cap(ziel, guard)
    return _fertig(
        state,
        inp,
        ziel,
        surplus,
        guard,
        reasons,
        status=Status.PROBING if probing and ziel > 0 else None,
    )


def _mindestladung(
    state: ControllerState,
    inp: ControlInputs,
    settings: ControlSettings,
    mode: Mode,
    ziel: int,
    charging: bool,
) -> tuple[ControllerState, int]:
    """Netznachladung bei leerem Auto, sonst die Pause-Hysterese."""
    min_a = settings.min_current_a

    below_min_soc = (
        mode is Mode.MINPV
        and inp.vehicle_soc is not None
        and inp.vehicle_soc < settings.min_soc
    )
    ziel_erreicht = (
        inp.vehicle_soc is not None
        and inp.vehicle_target_soc is not None
        and inp.vehicle_soc >= inp.vehicle_target_soc
    )

    if below_min_soc and not ziel_erreicht:
        # Das Auto ist zu leer zum Warten - Mindeststrom, notfalls aus dem Netz.
        return replace(state, below_min_since=None), max(ziel, min_a)

    if ziel >= min_a:
        return replace(state, below_min_since=None), ziel

    # Unter dem Mindeststrom: nicht sofort abschalten, sondern eine Wolkenluecke
    # lang mit Mindeststrom weiterlaufen. Steht die Box ohnehin, sofort 0 -
    # es gibt bewusst keine Anlauf-Hysterese nach oben.
    since = state.below_min_since if state.below_min_since is not None else inp.now
    lange_genug = inp.now - since >= settings.pause_delay_s
    ziel = min_a if charging and not lange_genug else 0
    return replace(state, below_min_since=since), ziel


def _daten_fehlen(inp: ControlInputs, settings: ControlSettings) -> bool:
    """Fehlt der Netzwert laenger, als die Regelung ueberbruecken darf?"""
    if inp.grid_missing_since is None:
        return False
    return inp.now - inp.grid_missing_since >= settings.stale_after_s


def _bei_ausfall(
    state: ControllerState,
    inp: ControlInputs,
    settings: ControlSettings,
    reasons: list[str],
) -> Decision:
    """Der Zaehler liefert nicht mehr.

    Die urspruengliche Node-Regelung hielt das letzte Limit unbegrenzt. Das ist
    die eine Stelle, an der die Portierung der Vorlage bewusst nicht folgt:
    Ohne Zaehler ist Ueberschussladen Raten, und Raten sollte nicht die
    Voreinstellung sein.
    """
    reasons.append("Netzzaehler liefert keine Werte mehr")
    if settings.stale_action is StaleAction.HOLD:
        return Decision(
            state=state,
            target_a=state.last_target_a or 0,
            phases=inp.phases,
            surplus_w=None,
            status=Status.STALE,
            reasons=tuple(reasons),
            should_apply=False,
        )
    ziel = (
        settings.min_current_a
        if settings.stale_action is StaleAction.MIN_CURRENT
        else 0
    )
    ziel = apply_cap(ziel, state.guard)
    return Decision(
        state=replace(state, last_target_a=ziel),
        target_a=ziel,
        phases=inp.phases,
        surplus_w=None,
        status=Status.STALE,
        reasons=tuple(reasons),
    )


def _fertig(
    state: ControllerState,
    inp: ControlInputs,
    ziel: int,
    surplus: float | None,
    guard,
    reasons: list[str],
    status: Status | None = None,
) -> Decision:
    """Baut die Entscheidung zusammen und leitet den Status ab."""
    if status is None:
        if guard.cap_a is not None and guard.cap_a <= ziel:
            status = Status.GRID_LIMITED
        elif ziel > 0:
            status = Status.CHARGING
        elif state.below_min_since is not None:
            status = Status.PAUSED
        else:
            status = Status.IDLE

    return Decision(
        state=replace(state, last_target_a=ziel),
        target_a=ziel,
        phases=inp.phases,
        surplus_w=surplus,
        status=status,
        reasons=tuple(reasons),
    )
