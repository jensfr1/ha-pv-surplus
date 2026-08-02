"""Einstellbare Schwellwerte der Regelung."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SurplusCoordinator
from .entity import SurplusEntity


@dataclass(frozen=True, kw_only=True)
class SurplusNumberDescription(NumberEntityDescription):
    """Beschreibt einen Regler samt Anbindung an die Einstellungen."""

    #: Liest den aktuellen Wert aus den Einstellungen.
    value_fn: Callable[[SurplusCoordinator], float]
    #: Name des Feldes in ControlSettings; None = Sonderbehandlung.
    setting: str | None = None


AMPERE = {
    "device_class": NumberDeviceClass.CURRENT,
    "native_unit_of_measurement": UnitOfElectricCurrent.AMPERE,
    "native_step": 1,
    "mode": NumberMode.BOX,
}

NUMBERS: tuple[SurplusNumberDescription, ...] = (
    SurplusNumberDescription(
        key="min_current",
        native_min_value=6,
        native_max_value=32,
        value_fn=lambda c: c.settings.min_current_a,
        setting="min_current_a",
        **AMPERE,
    ),
    SurplusNumberDescription(
        key="max_current",
        native_min_value=6,
        native_max_value=32,
        value_fn=lambda c: c.settings.max_current_a,
        setting="max_current_a",
        **AMPERE,
    ),
    SurplusNumberDescription(
        key="manual_current",
        native_min_value=0,
        native_max_value=32,
        value_fn=lambda c: c.manual_a,
        **AMPERE,
    ),
    SurplusNumberDescription(
        key="surplus_reserve",
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0,
        native_max_value=5000,
        native_step=50,
        mode=NumberMode.BOX,
        value_fn=lambda c: c.settings.surplus_reserve_w,
        setting="surplus_reserve_w",
    ),
    # Bewusst ohne device_class BATTERY: Das ist ein Schwellwert, kein
    # Ladestand, und wuerde sonst als Akkusymbol dargestellt.
    SurplusNumberDescription(
        key="min_soc",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=5,
        mode=NumberMode.BOX,
        value_fn=lambda c: c.settings.min_soc,
        setting="min_soc",
    ),
    SurplusNumberDescription(
        key="battery_reserve_soc",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=5,
        mode=NumberMode.BOX,
        value_fn=lambda c: c.settings.battery_reserve_soc,
        setting="battery_reserve_soc",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        SurplusNumber(entry.runtime_data, beschreibung) for beschreibung in NUMBERS
    )


class SurplusNumber(SurplusEntity, RestoreNumber):
    """Ein Schwellwert, der einen Neustart uebersteht."""

    entity_description: SurplusNumberDescription
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: SurplusCoordinator, beschreibung: SurplusNumberDescription
    ) -> None:
        super().__init__(coordinator, beschreibung.key)
        self.entity_description = beschreibung

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        daten = await self.async_get_last_number_data()
        if daten is not None and daten.native_value is not None:
            await self._uebernehmen(float(daten.native_value))

    @property
    def native_value(self) -> float:
        return self.entity_description.value_fn(self.coordinator)

    async def async_set_native_value(self, value: float) -> None:
        await self._uebernehmen(value)
        self.async_write_ha_state()

    async def _uebernehmen(self, value: float) -> None:
        feld = self.entity_description.setting
        if feld is None:
            await self.coordinator.async_set_manual_current(int(value))
        else:
            wert = int(value) if feld.endswith("_a") else float(value)
            await self.coordinator.async_set_setting(**{feld: wert})
