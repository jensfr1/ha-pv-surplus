"""Betriebsart der Laderegelung."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .control.models import Mode
from .coordinator import SurplusCoordinator
from .entity import SurplusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([ModeSelect(entry.runtime_data)])


class ModeSelect(SurplusEntity, SelectEntity, RestoreEntity):
    """Auswahl zwischen Aus, PV, Min+PV, Manuell und Maximum."""

    _attr_options: ClassVar[list[str]] = [m.value for m in Mode]

    def __init__(self, coordinator: SurplusCoordinator) -> None:
        super().__init__(coordinator, "mode")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Der Modus soll einen Neustart ueberleben - anders als die Zeitstempel
        # der Regelung, die bewusst verworfen werden.
        letzter = await self.async_get_last_state()
        if letzter is not None and letzter.state in self._attr_options:
            self.coordinator.mode = Mode(letzter.state)

    @property
    def current_option(self) -> str:
        return self.coordinator.mode.value

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_mode(Mode(option))
        self.async_write_ha_state()
