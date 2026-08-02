"""Anzeigewerte und Energiezaehler."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_EV_POWER
from .control.energy import EnergyIntegrator
from .control.models import Decision, Status
from .coordinator import SurplusCoordinator
from .entity import SurplusEntity


@dataclass(frozen=True, kw_only=True)
class SurplusSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Decision], float | str | None]


POWER = {
    "device_class": SensorDeviceClass.POWER,
    "native_unit_of_measurement": UnitOfPower.WATT,
    "state_class": SensorStateClass.MEASUREMENT,
    "suggested_display_precision": 0,
}
AMPERE = {
    "device_class": SensorDeviceClass.CURRENT,
    "native_unit_of_measurement": UnitOfElectricCurrent.AMPERE,
    "state_class": SensorStateClass.MEASUREMENT,
    "suggested_display_precision": 0,
}

SENSORS: tuple[SurplusSensorDescription, ...] = (
    SurplusSensorDescription(key="surplus", value_fn=lambda d: d.surplus_w, **POWER),
    SurplusSensorDescription(
        key="target_current", value_fn=lambda d: d.target_a, **AMPERE
    ),
    SurplusSensorDescription(
        key="phases",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.phases,
    ),
    SurplusSensorDescription(
        key="status",
        device_class=SensorDeviceClass.ENUM,
        options=[s.value for s in Status],
        value_fn=lambda d: d.status.value,
    ),
    # Diagnose - standardmaessig aus, damit die Geraeteseite lesbar bleibt
    SurplusSensorDescription(
        key="grid_guard_cap",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.state.guard.cap_a,
        **AMPERE,
    ),
    SurplusSensorDescription(
        key="probe_ceiling",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.state.probe.ceiling_a,
        **AMPERE,
    ),
)


@dataclass(frozen=True, kw_only=True)
class EnergyDescription(SensorEntityDescription):
    """Ein kWh-Zaehler, gebildet aus einer Leistung."""

    power_fn: Callable[[SurplusCoordinator, Decision], float | None]


ENERGY = {
    "device_class": SensorDeviceClass.ENERGY,
    "state_class": SensorStateClass.TOTAL_INCREASING,
    "native_unit_of_measurement": UnitOfEnergy.WATT_HOUR,
    "suggested_unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
    "suggested_display_precision": 2,
}


def _ev_power(coordinator: SurplusCoordinator, _d: Decision) -> float | None:
    return coordinator.last_ev_power_w


def _grid_share(coordinator: SurplusCoordinator, _d: Decision) -> float | None:
    """Naeherung: Bei laufender Ladung faellt Netzbezug dem Auto zu."""
    ev = coordinator.last_ev_power_w
    netz = coordinator.last_grid_power_w
    if ev is None:
        return None
    if netz is None:
        return 0.0
    return min(ev, max(0.0, netz))


def _solar_share(coordinator: SurplusCoordinator, d: Decision) -> float | None:
    ev = coordinator.last_ev_power_w
    if ev is None:
        return None
    return max(0.0, ev - (_grid_share(coordinator, d) or 0.0))


ENERGIES: tuple[EnergyDescription, ...] = (
    EnergyDescription(key="charged_energy", power_fn=_ev_power, **ENERGY),
    EnergyDescription(key="solar_energy", power_fn=_solar_share, **ENERGY),
    EnergyDescription(key="grid_energy", power_fn=_grid_share, **ENERGY),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SurplusCoordinator = entry.runtime_data
    entitaeten: list[SensorEntity] = [SurplusSensor(coordinator, b) for b in SENSORS]
    # Ohne Leistungssensor der Wallbox gibt es nichts zu zaehlen
    daten = {**entry.data, **entry.options}
    if daten.get(CONF_EV_POWER):
        entitaeten.extend(SurplusEnergySensor(coordinator, b) for b in ENERGIES)
    async_add_entities(entitaeten)


class SurplusSensor(SurplusEntity, SensorEntity):
    """Ein einfacher Anzeigewert aus der letzten Entscheidung."""

    entity_description: SurplusSensorDescription

    def __init__(
        self, coordinator: SurplusCoordinator, beschreibung: SurplusSensorDescription
    ) -> None:
        super().__init__(coordinator, beschreibung.key)
        self.entity_description = beschreibung

    @property
    def native_value(self) -> float | str | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Beim Status die Begruendung mitliefern - erspart Log-Wuehlen."""
        if self.entity_description.key != "status" or self.coordinator.data is None:
            return None
        gruende = self.coordinator.data.reasons
        return {"reason": gruende[-1] if gruende else ""}


class SurplusEnergySensor(SurplusEntity, RestoreSensor):
    """Zaehlt Energie aus einer Leistung - neustartfest."""

    entity_description: EnergyDescription

    def __init__(
        self, coordinator: SurplusCoordinator, beschreibung: EnergyDescription
    ) -> None:
        super().__init__(coordinator, beschreibung.key)
        self.entity_description = beschreibung
        self._integrator = EnergyIntegrator()

    @property
    def available(self) -> bool:
        # Ein Zaehlerstand veraltet nicht - sonst entstuenden Luecken im
        # Energie-Dashboard, wo gar keine sind.
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        letzte = await self.async_get_last_sensor_data()
        if letzte is not None and letzte.native_value is not None:
            try:
                self._integrator.restore(float(letzte.native_value))
            except (TypeError, ValueError):
                pass

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.coordinator.data is not None:
            self._integrator.add(
                self.entity_description.power_fn(
                    self.coordinator, self.coordinator.data
                ),
                time.monotonic(),
            )
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        return round(self._integrator.total_wh, 3)
