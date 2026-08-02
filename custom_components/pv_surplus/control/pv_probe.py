"""Tast-Betrieb fuer abgeregelte Anlagen.

Bei Nulleinspeisung darf nichts ins Netz, also drosselt der Wechselrichter das
Dach auf genau den Bedarf herunter. Die gemessene Einspeisung ist dann immer rund
null - und damit auch der "Ueberschuss", aus dem die Regelung ihren Ladestrom
ableitet. Der PV-Modus kann aus dem Stand nie starten, obwohl das Dach mehrere
Kilowatt liefern koennte.

Belegt am 29.07.2026: Bei voller Batterie und stehender Wallbox lief die PV mit
251 W. In der Minute, in der das Auto zu laden begann, sprang sie auf 6874 W.
Diese Reserve steht in keinem Messwert - man sieht sie erst, wenn man Last
dazuschaltet.

Genau das macht dieser Regler: Er gibt schrittweise mehr frei und beobachtet, ob
die Anlage mitzieht. Zwei Signale bedeuten "zu weit gegangen":

  - Der Zaehler meldet Netzbezug.
  - Die Hausbatterie faengt an zu entladen.

Das zweite ist genauso wichtig wie das erste: Ohne diese Bedingung wuerde nachts
munter weitergetastet und das Auto aus dem Hausspeicher geladen.

Portiert aus ``src/ocpp/pv-probe.ts`` des EcoFlow-Monitors, Zeiten in Sekunden.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

#: Ab hier gilt Netzbezug als echt (darunter Messrauschen).
IMPORT_THRESHOLD_W: Final = 50.0

#: Ab hier gilt die Batterie als entladend.
DISCHARGE_THRESHOLD_W: Final = -50.0

#: Unterhalb dieser PV-Leistung lohnt kein Tasten.
MIN_PV_W: Final = 500.0

#: Wartezeit zwischen zwei Schritten.
#:
#: Der Wechselrichter faehrt nicht sofort hoch, und die Wallbox setzt ein neues
#: Limit ebenfalls verzoegert um. Wer schneller tastet, misst noch den alten
#: Zustand und laeuft der Anlage davon.
STEP_INTERVAL_S: Final = 90.0

#: So lange gilt eine erkannte Obergrenze.
#:
#: Danach wird wieder probiert - die Sonne steht in zehn Minuten anders, und eine
#: einmal gefundene Grenze soll den Rest des Tages nicht blockieren.
CEILING_TTL_S: Final = 600.0


@dataclass(frozen=True, slots=True)
class ProbeState:
    """Zustand des Tastreglers. Unveraenderlich."""

    #: Zuletzt als zu hoch erkannter Ladestrom (A); darunter bleiben.
    ceiling_a: int | None = None
    #: Wann diese Grenze erkannt wurde.
    ceiling_at: float | None = None
    #: Wann zuletzt ein Schritt nach oben gegangen wurde.
    last_step_at: float | None = None


@dataclass(frozen=True, slots=True)
class ProbeInput:
    """Messwerte und Randbedingungen eines Regeltakts."""

    now: float
    #: Ladestrom, den die Ueberschussrechnung ergibt.
    desired_a: int
    #: Aktuell an der Wallbox gesetztes Limit.
    current_limit_a: int | None
    #: Netzleistung: positiv = Bezug, negativ = Einspeisung.
    grid_power_w: float | None
    #: Batterieleistung: positiv = laden, negativ = entladen.
    battery_power_w: float | None
    pv_power_w: float | None
    min_current_a: int
    max_current_a: int


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Ergebnis eines Takts: neuer Zustand, Zielstrom, Begruendung."""

    state: ProbeState
    target_a: int
    #: Kurze Begruendung fuers Log, None wenn nichts getastet wurde.
    reason: str | None = None


def empty_probe_state() -> ProbeState:
    """Ausgangszustand: keine Grenze bekannt, noch kein Schritt gegangen."""
    return ProbeState()


def _gueltige_grenze(state: ProbeState, now: float) -> int | None:
    """Obergrenze, sofern sie noch nicht verfallen ist."""
    if state.ceiling_a is None or state.ceiling_at is None:
        return None
    return state.ceiling_a if now - state.ceiling_at < CEILING_TTL_S else None


def probe_target(state: ProbeState, inp: ProbeInput) -> ProbeResult:
    """Ermittelt, ob und wie weit ueber den Ueberschuss hinaus getastet wird."""
    grenze = _gueltige_grenze(state, inp.now)
    next_state = state
    if grenze is None and state.ceiling_a is not None:
        # Verfallene Grenze vergessen, damit wieder probiert wird.
        next_state = replace(next_state, ceiling_a=None, ceiling_at=None)

    # Ohne Messwerte nicht tasten - blind hochfahren waere genau das, was die
    # Sperre hinterher wieder einsammeln muesste.
    if inp.grid_power_w is None or inp.pv_power_w is None:
        return ProbeResult(state=next_state, target_a=inp.desired_a)

    bezug = inp.grid_power_w >= IMPORT_THRESHOLD_W
    entlaedt = (
        inp.battery_power_w is not None and inp.battery_power_w <= DISCHARGE_THRESHOLD_W
    )
    gehalten = max(inp.desired_a, inp.current_limit_a or 0)

    if bezug or entlaedt:
        return _zurueckziehen(next_state, inp, entlaedt)

    # Nachts oder bei dichten Wolken gibt es nichts zu holen.
    if inp.pv_power_w < MIN_PV_W:
        return ProbeResult(state=next_state, target_a=inp.desired_a)

    obergrenze = inp.max_current_a if grenze is None else min(inp.max_current_a, grenze)
    if gehalten >= obergrenze:
        return ProbeResult(state=next_state, target_a=min(gehalten, obergrenze))

    if (
        next_state.last_step_at is not None
        and inp.now - next_state.last_step_at < STEP_INTERVAL_S
    ):
        return ProbeResult(state=next_state, target_a=gehalten)

    # Aus dem Stand direkt auf den Mindeststrom - darunter laedt die Wallbox
    # ohnehin nicht, kleinere Schritte waeren wirkungslos.
    ziel_a = inp.min_current_a if gehalten < inp.min_current_a else gehalten + 1
    ziel_a = min(ziel_a, obergrenze)
    einspeisung = round(-inp.grid_power_w)
    return ProbeResult(
        state=replace(next_state, last_step_at=inp.now),
        target_a=ziel_a,
        reason=(
            f"taste auf {ziel_a} A "
            f"(Einspeisung {einspeisung} W, PV {round(inp.pv_power_w)} W)"
        ),
    )


def _zurueckziehen(state: ProbeState, inp: ProbeInput, entlaedt: bool) -> ProbeResult:
    """Zu weit gegangen: einen Schritt zurueck und die Grenze merken.

    Nur, wenn ueberhaupt getastet wurde. Liegt das Limit auf Hoehe der
    Ueberschussrechnung, stammt der Bezug woanders her (der Backofen), und dann
    ist es nicht Sache dieses Reglers.
    """
    wurde_getastet = (inp.current_limit_a or 0) > inp.desired_a
    if not wurde_getastet:
        return ProbeResult(state=state, target_a=inp.desired_a)

    # Ein Schritt zurueck - und wenn schon der Mindeststrom zu viel ist, ganz
    # aussetzen. Sonst bliebe die Wallbox auf 6 A stehen und zoege die Differenz
    # dauerhaft aus der Hausbatterie; genau das war beim ersten Feldversuch am
    # 29.07.2026 zu sehen.
    naechster = (
        inp.current_limit_a if inp.current_limit_a is not None else inp.min_current_a
    ) - 1
    zurueck = 0 if naechster < inp.min_current_a else naechster
    unveraendert = state.ceiling_a == zurueck

    grund = (
        f"Batterie entlaedt ({round(inp.battery_power_w or 0)} W)"
        if entlaedt
        else f"Netzbezug ({round(inp.grid_power_w or 0)} W)"
    )
    was = "Ladung ausgesetzt" if zurueck == 0 else f"zurueck auf {zurueck} A"

    return ProbeResult(
        state=replace(
            state, ceiling_a=zurueck, ceiling_at=inp.now, last_step_at=inp.now
        ),
        target_a=max(inp.desired_a, zurueck),
        # Bei gleichbleibender Lage nicht in jedem Takt dasselbe melden.
        reason=None if unveraendert else f"{grund} - {was}",
    )
