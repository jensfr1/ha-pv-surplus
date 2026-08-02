"""Gemeinsame Basis aller Entitaeten dieser Integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SurplusCoordinator


class SurplusEntity(CoordinatorEntity[SurplusCoordinator]):
    """Haengt am Regelkreis und gehoert zu dessen Geraet."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SurplusCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_base}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.unique_base)},
            name=coordinator.device_name,
            manufacturer="PV-Ueberschussladen",
            # Es gibt keine Hardware - das ehrlich darstellen, statt ein
            # Geraet vorzutaeuschen, das niemand anfassen kann.
            entry_type=DeviceEntryType.SERVICE,
        )
