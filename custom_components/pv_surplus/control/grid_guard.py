"""Netzbezugs-Sperre fuer die Wallbox.

Die Ueberschussregelung arbeitet vorausschauend: Sie gibt nur frei, was gerade
eingespeist wird. Das setzt voraus, dass die Rechnung stimmt - eine traege
Messung, ein Verbraucher der ploetzlich anspringt oder ein Konfigurationsfehler,
und es fliesst doch Strom aus dem Netz ins Auto.

Diese Sperre ist die zweite, unabhaengige Ebene: Sie schaut nur auf den Zaehler.
Meldet er laenger als ein paar Sekunden echten Bezug, WAEHREND die Wallbox
laedt, zieht sie einen Deckel ueber das Ladestrom-Limit - notfalls bis auf null.
Sie greift in jedem Modus, auch in "Manuell" und "Max", denn ein Schutz, den man
erst einschalten muss, schuetzt nicht.

Portiert aus ``src/ocpp/grid-guard.ts`` des EcoFlow-Monitors. Einziger
Unterschied: Zeiten in Sekunden statt Millisekunden, weil Python und Home
Assistant durchgehend in Sekunden rechnen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Final

#: Ab hier gilt Netzbezug als echt (unterhalb: Messrauschen des Zaehlers).
IMPORT_THRESHOLD_W: Final = 50.0

#: So lange darf Bezug anliegen, bevor gedeckelt wird.
#:
#: Kurze Spitzen - der Backofen springt an, eine Wolke zieht durch - sollen
#: nicht sofort den Ladevorgang stoeren. Die Hausbatterie faengt so etwas ohnehin
#: ab. Erst wenn der Bezug anhaelt, liegt es plausibel am Auto.
REACT_AFTER_S: Final = 30.0

#: Abstand, in dem der Deckel nach Entspannung wieder um 1 A steigt.
RAISE_INTERVAL_S: Final = 120.0


@dataclass(frozen=True, slots=True)
class GuardState:
    """Zustand der Sperre. Unveraenderlich - Fortschreiben gibt einen neuen."""

    #: Obergrenze in A, die kein Modus ueberschreiten darf. None = keine.
    cap_a: int | None = None
    #: Seit wann durchgehend Netzbezug anliegt.
    import_since: float | None = None
    #: Wann der Deckel zuletzt angehoben wurde.
    last_raise: float | None = None
    #: Letzte Begruendung - fuer Anzeige und Log.
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class GuardInput:
    """Messwerte und Randbedingungen eines Regeltakts."""

    now: float
    #: Netzleistung in W: positiv = Bezug, negativ = Einspeisung.
    grid_power_w: float | None
    #: Zieht die Wallbox gerade nennenswert Strom?
    charging: bool
    #: Aktuell gesetztes Limit in A (Ausgangspunkt fuers Deckeln).
    current_limit_a: int | None
    phases: int
    min_current_a: int
    max_current_a: int
    voltage_v: float = 230.0


def empty_guard_state() -> GuardState:
    """Ausgangszustand: kein Deckel, keine Messung laeuft."""
    return GuardState()


def update_guard(state: GuardState, inp: GuardInput) -> GuardState:
    """Rechnet den Deckel fort.

    Gibt einen neuen Zustand zurueck und veraendert den uebergebenen nicht.
    """
    # Ohne Messwert nichts aendern: Ein fehlender Netzwert ist kein Freibrief,
    # aber auch kein Grund, den Deckel zu lockern.
    if inp.grid_power_w is None:
        return replace(state, import_since=None)

    import_w = max(0.0, inp.grid_power_w)
    bezug_laeuft = import_w >= IMPORT_THRESHOLD_W and inp.charging

    if not bezug_laeuft:
        return _entspannen(state, inp)

    if state.import_since is None:
        return replace(state, import_since=inp.now)
    if inp.now - state.import_since < REACT_AFTER_S:
        return state

    return _deckeln(state, inp, import_w)


def _entspannen(state: GuardState, inp: GuardInput) -> GuardState:
    """Kein Bezug: den Deckel schrittweise zurueckziehen."""
    if state.cap_a is None:
        return replace(state, import_since=None)

    if state.last_raise is None:
        return replace(state, import_since=None, last_raise=inp.now)
    if inp.now - state.last_raise < RAISE_INTERVAL_S:
        return replace(state, import_since=None)

    # Von 0 direkt auf den Mindeststrom - Werte dazwischen kann die Wallbox
    # ohnehin nicht fahren.
    naechster = inp.min_current_a if state.cap_a == 0 else state.cap_a + 1
    if naechster >= inp.max_current_a:
        # Deckel ganz aufloesen, die normale Regelung hat wieder freie Hand.
        return replace(
            state, import_since=None, cap_a=None, last_raise=None, reason=None
        )
    return replace(state, import_since=None, cap_a=naechster, last_raise=inp.now)


def _deckeln(state: GuardState, inp: GuardInput, import_w: float) -> GuardState:
    """Bezug haelt an: um so viel Ampere senken, wie dem Bezug entspricht."""
    basis = state.cap_a
    if basis is None:
        basis = inp.current_limit_a
    if basis is None:
        basis = inp.max_current_a

    zu_viel_a = max(1, math.ceil(import_w / (inp.voltage_v * inp.phases)))
    ziel = basis - zu_viel_a
    if ziel < inp.min_current_a:
        ziel = 0  # unterhalb des Mindeststroms gibt es nur noch Pause
    ziel = max(0, min(ziel, inp.max_current_a))

    reason = state.reason
    if ziel != state.cap_a:
        reason = f"Netzbezug {round(import_w)} W - Limit auf {ziel} A begrenzt"

    # Nach dem Eingriff neu messen lassen, statt im selben Atemzug weiterzusenken.
    return replace(
        state, cap_a=ziel, reason=reason, import_since=inp.now, last_raise=inp.now
    )


def apply_cap(desired_a: int, state: GuardState) -> int:
    """Wendet den Deckel auf einen gewuenschten Ladestrom an.

    Hebt einen niedrigeren Wunsch nie an - das ist reines Begrenzen.
    """
    return desired_a if state.cap_a is None else min(desired_a, state.cap_a)
