"""Konstanten und Konfigurationsschluessel."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "pv_surplus"

# ── Konfiguration (Config-Entry) ──────────────────────────────────────────────
CONF_NAME: Final = "name"
CONF_GRID_POWER: Final = "grid_power"
CONF_GRID_INVERT: Final = "grid_invert"
CONF_PV_POWER: Final = "pv_power"
CONF_BATTERY_POWER: Final = "battery_power"
CONF_BATTERY_INVERT: Final = "battery_invert"
CONF_BATTERY_SOC: Final = "battery_soc"
CONF_CURRENT_ENTITY: Final = "current_entity"
CONF_SWITCH_ENTITY: Final = "switch_entity"
CONF_EV_POWER: Final = "ev_power"
CONF_VEHICLE_SOC: Final = "vehicle_soc"
CONF_VEHICLE_TARGET_SOC: Final = "vehicle_target_soc"
CONF_PHASE_ENTITY: Final = "phase_entity"
CONF_PHASE_OPTION_1P: Final = "phase_option_1p"
CONF_PHASE_OPTION_3P: Final = "phase_option_3p"
CONF_PHASE_CURRENTS: Final = "phase_currents"

# ── Optionen ──────────────────────────────────────────────────────────────────
CONF_SURPLUS_INCLUDES_EV: Final = "surplus_includes_ev"
CONF_VOLTAGE: Final = "voltage"
CONF_STALE_ACTION: Final = "stale_action"
CONF_STALE_AFTER: Final = "stale_after"
CONF_PAUSE_DELAY: Final = "pause_delay"
CONF_RESPECT_MANUAL: Final = "respect_manual"

DEFAULT_VOLTAGE: Final = 230.0
DEFAULT_MIN_CURRENT: Final = 6
DEFAULT_MAX_CURRENT: Final = 16

#: Regeltakt. Die Hysteresen (30/90/120/180 s) haben nur bei festem Takt eine
#: verlaessliche Bedeutung - deshalb bewusst nicht ereignisgetrieben.
TICK_INTERVAL_S: Final = 15

#: So lange nach einem eigenen Stellbefehl gilt eine Aenderung als von uns.
COMMAND_ECHO_S: Final = 30.0

#: Spaetestens so oft wird das Limit erneut gesetzt, auch wenn es unveraendert
#: scheint - manche Wallboxen vergessen ihr Limit stillschweigend.
REASSERT_AFTER_S: Final = 300.0

#: So lange ruht die Regelung, wenn jemand von Hand eingegriffen hat.
MANUAL_OVERRIDE_S: Final = 600.0

#: Nach so vielen erfolglosen Stellversuchen wird ein Reparaturhinweis erzeugt.
FAILED_COMMANDS_BEFORE_ISSUE: Final = 3
