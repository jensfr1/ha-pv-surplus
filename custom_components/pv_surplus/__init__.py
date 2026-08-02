"""PV-Ueberschussladen fuer beliebige Wallboxen."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import SurplusCoordinator

PLATFORMS: list[Platform] = [
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type SurplusConfigEntry = ConfigEntry[SurplusCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SurplusConfigEntry) -> bool:
    """Regelkreis starten."""
    coordinator = SurplusCoordinator(hass, entry)
    await coordinator.async_start()

    entry.async_on_unload(coordinator.async_stop)
    entry.async_on_unload(entry.add_update_listener(_optionen_geaendert))
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SurplusConfigEntry) -> bool:
    """Aufraeumen. Das zuletzt gesetzte Limit bleibt bewusst stehen.

    Es auf 0 zu ziehen wuerde jede Ladung bei einem Neustart von Home Assistant
    unterbrechen - das waere schlimmer als der seltene Fall, dass ein Limit
    stehen bleibt, das niemand mehr nachregelt.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _optionen_geaendert(hass: HomeAssistant, entry: SurplusConfigEntry) -> None:
    """Nach Aenderungen in den Optionen neu laden."""
    await hass.config_entries.async_reload(entry.entry_id)
