"""Datenmodelle des Regelkerns.

Alles unveraenderlich. Ein Regeltakt nimmt Zustand und Messwerte entgegen und
gibt einen neuen Zustand zurueck - der uebergebene bleibt unberuehrt. Das ist
nicht nur Stil: Es macht jeden Takt einzeln pruefbar und schliesst die Klasse
von Fehlern aus, bei denen ein Zwischenschritt still etwas mitveraendert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .grid_guard import GuardState, empty_guard_state
from .pv_probe import ProbeState, empty_probe_state


class Mode(StrEnum):
    """Betriebsarten der Laderegelung."""

    #: Nicht eingreifen - auch die Netzsperre ruht. Das ist die Zusage dieses
    #: Modus: Wer ihn waehlt, will die Wallbox selbst oder per Backend steuern.
    OFF = "off"
    #: Nur mit Ueberschuss laden; unter dem Mindeststrom nach kurzer Frist Pause.
    PV = "pv"
    #: Wie PV, aber unterhalb des Fahrzeug-Mindest-SoC mit Mindeststrom aus dem
    #: Netz nachladen - sofern das Auto sein Ladeziel noch nicht erreicht hat.
    MINPV = "minpv"
    #: Fester, von Hand eingestellter Ladestrom.
    MANUAL = "manual"
    #: Volle Leistung.
    MAX = "max"


class Status(StrEnum):
    """Was die Regelung gerade tut - fuer Anzeige und Fehlersuche."""

    OFF = "off"
    #: Kein Ueberschuss, nichts zu tun.
    IDLE = "idle"
    CHARGING = "charging"
    #: Ueberschuss reicht nicht mehr, Ladung ausgesetzt.
    PAUSED = "paused"
    #: Tast-Betrieb sucht gerade verdeckte PV-Reserve.
    PROBING = "probing"
    #: Netzbezugs-Sperre begrenzt den Strom.
    GRID_LIMITED = "grid_limited"
    #: Messwerte fehlen zu lange.
    STALE = "stale"


class StaleAction(StrEnum):
    """Was geschehen soll, wenn der Netzzaehler ausfaellt."""

    #: Ladung aussetzen. Ohne Zaehler ist Ueberschussladen Raten.
    PAUSE = "pause"
    #: Letztes Limit halten (Verhalten der urspruenglichen Node-Regelung).
    HOLD = "hold"
    #: Auf den Mindeststrom zurueckgehen.
    MIN_CURRENT = "min_current"


@dataclass(frozen=True, slots=True)
class ControlSettings:
    """Einstellungen, die sich waehrend des Betriebs aendern duerfen."""

    min_current_a: int = 6
    max_current_a: int = 16
    voltage_v: float = 230.0
    #: Ladeleistung zum Ueberschuss zurueckrechnen? Nur richtig, wenn die
    #: Wallbox HINTER dem messenden Zaehler haengt. Sonst entsteht eine
    #: Mitkopplung: Netzbezug fuers Auto laesst den Ueberschuss wachsen.
    surplus_includes_ev: bool = False
    #: Wieviel Ueberschuss dem Haus bleiben soll, bevor geladen wird.
    surplus_reserve_w: float = 0.0
    #: Tast-Betrieb fuer abgeregelte Anlagen.
    pv_probe: bool = False
    #: Fahrzeug-SoC, unterhalb dessen im Modus MINPV aus dem Netz nachgeladen wird.
    min_soc: float = 20.0
    #: Hausbatterie-Schutz: darunter wird nicht geladen. 0 = aus.
    battery_reserve_soc: float = 0.0
    #: So lange unter dem Mindeststrom weiterladen, bevor pausiert wird.
    #: Ueberbrueckt Wolkenluecken, statt bei jedem Schatten abzuschalten.
    pause_delay_s: float = 180.0
    #: Ab wann ein fehlender Netzwert als Ausfall gilt.
    stale_after_s: float = 120.0
    stale_action: StaleAction = StaleAction.PAUSE
    #: Ab dieser Ladeleistung gilt die Wallbox als ladend.
    charging_threshold_w: float = 100.0


@dataclass(frozen=True, slots=True)
class ControlInputs:
    """Messwerte eines Regeltakts. Alles, was fehlen kann, ist ``None``."""

    now: float
    #: Netzleistung in W: positiv = Bezug, negativ = Einspeisung.
    grid_power_w: float | None = None
    pv_power_w: float | None = None
    #: Hausbatterie in W: positiv = laden, negativ = entladen.
    battery_power_w: float | None = None
    battery_soc: float | None = None
    #: Aktuelle Ladeleistung der Wallbox.
    ev_power_w: float | None = None
    #: Vom Stellglied zurueckgemeldetes Limit.
    current_limit_a: int | None = None
    phases: int = 1
    vehicle_soc: float | None = None
    vehicle_target_soc: float | None = None
    #: Seit wann der Netzwert fehlt; None = liegt vor.
    grid_missing_since: float | None = None


@dataclass(frozen=True, slots=True)
class ControllerState:
    """Zustand ueber Takte hinweg. Wird bewusst NICHT ueber Neustarts erhalten.

    Ein Zeitbezug ueber einen Neustart hinweg ist eine Luecke, keine Messung -
    dieselbe Ueberlegung, aus der auch der Energiezaehler seinen Zeitbezug
    verwirft. Nach einem Neustart tastet der Regler eben neu.
    """

    guard: GuardState = field(default_factory=empty_guard_state)
    probe: ProbeState = field(default_factory=empty_probe_state)
    #: Seit wann der Ueberschuss unter dem Mindeststrom liegt.
    below_min_since: float | None = None
    #: Zuletzt kommandierter Strom.
    last_target_a: int | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    """Ergebnis eines Regeltakts."""

    state: ControllerState
    #: Zu setzender Ladestrom in A. 0 = Pause.
    target_a: int
    phases: int
    surplus_w: float | None
    status: Status
    #: Klartext-Begruendungen, in der Reihenfolge ihres Entstehens.
    reasons: tuple[str, ...] = ()
    #: False bedeutet: gar nichts stellen (Modus OFF, oder Daten fehlen und die
    #: Einstellung sagt "halten"). Nicht dasselbe wie ``target_a == 0``.
    should_apply: bool = True
