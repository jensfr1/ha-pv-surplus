"""Stellt den Ladestrom - die einzige Stelle mit Service-Aufrufen.

Zwei Dinge machen den Unterschied zwischen "funktioniert bei mir" und
"funktioniert bei allen":

* **Rueckmeldung pruefen.** Manche Wallboxen klemmen oder runden das Limit
  stillschweigend. Wer das nicht bemerkt, laesst den Tastbetrieb ins Leere
  laufen: Er rechnet mit dem zurueckgemeldeten Wert und kommt nie hoeher.
* **Eigene Befehle erkennen.** Ohne einen eigenen ``Context`` liesse sich nicht
  unterscheiden, ob eine Aenderung von uns kam oder vom Nutzer per App.
"""

from __future__ import annotations

import logging
import time

from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import COMMAND_ECHO_S, REASSERT_AFTER_S
from .sources import read_number

_LOGGER = logging.getLogger(__name__)


class Actuator:
    """Setzt Ladestrom, Freigabe und Phasenzahl an den gewaehlten Entitaeten."""

    def __init__(
        self,
        hass: HomeAssistant,
        current_entity: str,
        switch_entity: str | None = None,
        phase_entity: str | None = None,
    ) -> None:
        self._hass = hass
        self._current_entity = current_entity
        self._switch_entity = switch_entity
        self._phase_entity = phase_entity
        self._context = Context()

        self._last_commanded_a: int | None = None
        self._last_command_at: float | None = None
        self._failures = 0
        #: Bis wann eine fremde Aenderung respektiert wird.
        self._override_until: float | None = None

    @property
    def context(self) -> Context:
        """Eigener Kontext - macht unsere Aenderungen im Logbuch erkennbar."""
        return self._context

    @property
    def failures(self) -> int:
        """Wie oft in Folge das Stellglied den Wert nicht angenommen hat."""
        return self._failures

    def reported_current(self) -> int | None:
        """Zurueckgemeldetes Limit der Stell-Entitaet."""
        wert = read_number(self._hass.states.get(self._current_entity))
        return None if wert is None else int(round(wert))

    def external_change(self, now: float) -> bool:
        """Hat jemand anderes das Limit veraendert?

        Nur aussagekraeftig ausserhalb des Echo-Fensters: Direkt nach einem
        eigenen Befehl steht dort noch der alte Wert.
        """
        if self._last_commanded_a is None or self._last_command_at is None:
            return False
        if now - self._last_command_at < COMMAND_ECHO_S:
            return False
        gemeldet = self.reported_current()
        return gemeldet is not None and gemeldet != self._last_commanded_a

    def begin_override(self, now: float, dauer_s: float) -> None:
        """Regelung fuer eine Weile ruhen lassen."""
        self._override_until = now + dauer_s

    def override_active(self, now: float) -> bool:
        if self._override_until is None:
            return False
        if now >= self._override_until:
            self._override_until = None
            return False
        return True

    async def apply(self, target_a: int, now: float, step: float = 1.0) -> None:
        """Setzt den Ladestrom, wenn noetig, und schaltet die Freigabe."""
        gemeldet = self.reported_current()
        faellig = (
            self._last_command_at is None
            or now - self._last_command_at >= REASSERT_AFTER_S
        )
        abweichung = gemeldet is None or abs(gemeldet - target_a) > max(step / 2, 0.5)

        if abweichung or faellig:
            await self._set_current(target_a)
            self._last_commanded_a = target_a
            self._last_command_at = now
            if gemeldet is not None and not faellig:
                self._pruefe_erfolg(gemeldet, target_a)

        if self._switch_entity:
            await self._set_switch(target_a > 0)

    async def set_phases(self, phases: int, option: str | None) -> None:
        """Schaltet die Phasenzahl um, sofern eine Entitaet konfiguriert ist."""
        if not self._phase_entity or option is None:
            return
        domain = self._phase_entity.split(".")[0]
        try:
            if domain == "select":
                await self._call("select", "select_option", option=option)
            elif domain == "switch":
                dienst = SERVICE_TURN_ON if phases >= 3 else SERVICE_TURN_OFF
                await self._call("switch", dienst)
            elif domain == "number":
                await self._call("number", "set_value", value=float(phases))
        except HomeAssistantError as err:
            _LOGGER.warning("Phasenumschaltung fehlgeschlagen: %s", err)

    async def release(self) -> None:
        """Beim Beenden nichts veraendern - der Nutzer hat die Hoheit."""
        self._last_commanded_a = None
        self._last_command_at = None

    def _pruefe_erfolg(self, gemeldet: int, gewollt: int) -> None:
        """Zaehlt mit, ob das Stellglied ueberhaupt reagiert."""
        if self._last_commanded_a is not None and gemeldet == self._last_commanded_a:
            self._failures = 0
        elif gemeldet != gewollt:
            self._failures += 1

    async def _set_current(self, target_a: int) -> None:
        try:
            await self._hass.services.async_call(
                "number",
                "set_value",
                {ATTR_ENTITY_ID: self._current_entity, "value": float(target_a)},
                blocking=True,
                context=self._context,
            )
        except HomeAssistantError as err:
            self._failures += 1
            _LOGGER.warning(
                "Ladestrom %s A konnte nicht gesetzt werden (%s): %s",
                target_a,
                self._current_entity,
                err,
            )

    async def _set_switch(self, on: bool) -> None:
        zustand = self._hass.states.get(self._switch_entity or "")
        if zustand is not None and zustand.state == ("on" if on else "off"):
            return
        try:
            await self._hass.services.async_call(
                "switch",
                SERVICE_TURN_ON if on else SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: self._switch_entity},
                blocking=True,
                context=self._context,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Ladefreigabe konnte nicht geschaltet werden: %s", err)

    async def _call(self, domain: str, service: str, **daten) -> None:
        await self._hass.services.async_call(
            domain,
            service,
            {ATTR_ENTITY_ID: self._phase_entity, **daten},
            blocking=True,
            context=self._context,
        )


def monotonic() -> float:
    """Zeitquelle der Regelung.

    Bewusst monoton: Eine NTP-Korrektur oder die Zeitumstellung wuerde sonst
    einen laufenden 15-Minuten-Timer beliebig verschieben.
    """
    return time.monotonic()
