"""Schalter fuer Tast-Betrieb und Phasenumschaltung."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_PHASE_ENTITY
from .coordinator import SurplusCoordinator
from .entity import SurplusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SurplusCoordinator = entry.runtime_data
    schalter: list[SwitchEntity] = [ProbeSwitch(coordinator)]
    # Nur anbieten, wenn die Wallbox ueberhaupt umschalten kann
    if {**entry.data, **entry.options}.get(CONF_PHASE_ENTITY):
        schalter.append(PhaseSwitchingSwitch(coordinator))
    async_add_entities(schalter)


class _BaseSwitch(SurplusEntity, SwitchEntity, RestoreEntity):
    _attr_entity_category = EntityCategory.CONFIG

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (letzter := await self.async_get_last_state()) is not None:
            await self._setzen(letzter.state == "on")


class ProbeSwitch(_BaseSwitch):
    """Tast-Betrieb fuer abgeregelte Anlagen."""

    def __init__(self, coordinator: SurplusCoordinator) -> None:
        super().__init__(coordinator, "pv_probe")

    @property
    def is_on(self) -> bool:
        return self.coordinator.settings.pv_probe

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._setzen(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._setzen(False)

    async def _setzen(self, an: bool) -> None:
        await self.coordinator.async_set_setting(pv_probe=an)
        self.async_write_ha_state()


class PhaseSwitchingSwitch(_BaseSwitch):
    """Erlaubt der Regelung, zwischen ein- und dreiphasig umzuschalten.

    Standardmaessig aus: Manche Fahrzeuge laufen nach der dafuer noetigen
    Ladepause nur widerwillig oder gar nicht wieder an.
    """

    def __init__(self, coordinator: SurplusCoordinator) -> None:
        super().__init__(coordinator, "phase_switching")

    @property
    def is_on(self) -> bool:
        return self.coordinator.phase_switching_allowed

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._setzen(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._setzen(False)

    async def _setzen(self, an: bool) -> None:
        self.coordinator.phase_switching_allowed = an
        self.async_write_ha_state()
