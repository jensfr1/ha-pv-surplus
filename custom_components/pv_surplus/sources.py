"""Liest Messwerte aus Home-Assistant-Entitaeten.

Alles, was schiefgehen kann, wird hier abgefangen: fehlende Entitaeten,
``unavailable``, nicht-numerische Zustaende, kW statt W, vertauschte Vorzeichen.
Der Regelkern bekommt danach nur noch ``float`` oder ``None`` zu sehen.
"""

from __future__ import annotations

import logging

from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, State

_LOGGER = logging.getLogger(__name__)

#: Umrechnung gaengiger Leistungseinheiten nach Watt.
_POWER_FACTORS: dict[str, float] = {
    UnitOfPower.WATT: 1.0,
    UnitOfPower.KILO_WATT: 1000.0,
    UnitOfPower.MEGA_WATT: 1_000_000.0,
}


def read_number(state: State | None) -> float | None:
    """Zustand als Zahl, oder ``None`` wenn nicht lesbar."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def read_power(state: State | None, invert: bool = False) -> float | None:
    """Leistung in Watt, unabhaengig von der Einheit der Quelle.

    ``invert`` dreht das Vorzeichen. Das ist die haeufigste Fehlerquelle einer
    Ueberschussregelung ueberhaupt: Meldet der Zaehler Einspeisung positiv statt
    negativ, wird aus der Netzsperre ein Gaspedal. Deshalb wird der Wert im
    Einrichtungsdialog im Klartext angezeigt, statt ihn nur abzufragen.
    """
    wert = read_number(state)
    if wert is None:
        return None
    if state is not None:
        einheit = state.attributes.get("unit_of_measurement")
        if einheit and einheit not in _POWER_FACTORS:
            # Unbekannte Einheit: lieber nichts liefern als falsch rechnen
            _LOGGER.debug("Unbekannte Leistungseinheit %s", einheit)
            return None
        wert *= _POWER_FACTORS.get(einheit, 1.0)
    return -wert if invert else wert


def number_limits(state: State | None) -> tuple[int, int, float] | None:
    """Grenzen und Schrittweite einer ``number``-Entitaet.

    Daraus werden die Stromgrenzen vorbelegt, damit der Nutzer sie im
    Normalfall nie anfassen muss.
    """
    if state is None:
        return None
    try:
        minimum = int(float(state.attributes.get("min", 0)))
        maximum = int(float(state.attributes.get("max", 0)))
        step = float(state.attributes.get("step", 1))
    except (TypeError, ValueError):
        return None
    if maximum <= 0:
        return None
    return minimum, maximum, step


class SourceReader:
    """Buendelt die konfigurierten Quellen und merkt sich Ausfaelle."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        #: Seit wann eine Entitaet keinen brauchbaren Wert mehr liefert.
        self._missing_since: dict[str, float] = {}

    def power(
        self, entity_id: str | None, now: float, invert: bool = False
    ) -> float | None:
        """Leistung einer Quelle, mit Ausfallzeitpunkt."""
        if not entity_id:
            return None
        wert = read_power(self._hass.states.get(entity_id), invert)
        self._vermerken(entity_id, wert, now)
        return wert

    def number(self, entity_id: str | None, now: float) -> float | None:
        """Beliebiger Zahlenwert einer Quelle."""
        if not entity_id:
            return None
        wert = read_number(self._hass.states.get(entity_id))
        self._vermerken(entity_id, wert, now)
        return wert

    def missing_since(self, entity_id: str | None) -> float | None:
        """Seit wann die Quelle nichts mehr liefert; ``None`` = alles gut."""
        if not entity_id:
            return None
        return self._missing_since.get(entity_id)

    def _vermerken(self, entity_id: str, wert: float | None, now: float) -> None:
        if wert is None:
            self._missing_since.setdefault(entity_id, now)
        else:
            self._missing_since.pop(entity_id, None)
