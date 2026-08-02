"""Einrichtung ueber die Oberflaeche.

Der Dialog fragt so wenig wie moeglich: Netzzaehler, Stell-Entitaet, fertig.
Alles Weitere hat einen brauchbaren Vorgabewert und lebt unter "Konfigurieren".

Eine Pruefung ist dabei wichtiger als alle anderen zusammen: **das Vorzeichen
des Netzzaehlers**. Ist es vertauscht, wird aus der Netzbezugs-Sperre ein
Gaspedal - sie gaebe genau dann mehr frei, wenn Strom aus dem Netz fliesst.
Deshalb wird der gemessene Wert im Klartext angezeigt, statt nur abgefragt.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_INVERT,
    CONF_BATTERY_POWER,
    CONF_BATTERY_SOC,
    CONF_CURRENT_ENTITY,
    CONF_EV_POWER,
    CONF_GRID_INVERT,
    CONF_GRID_POWER,
    CONF_NAME,
    CONF_PHASE_CURRENTS,
    CONF_PHASE_ENTITY,
    CONF_PHASE_OPTION_1P,
    CONF_PHASE_OPTION_3P,
    CONF_PV_POWER,
    CONF_RESPECT_MANUAL,
    CONF_STALE_ACTION,
    CONF_SURPLUS_INCLUDES_EV,
    CONF_SWITCH_ENTITY,
    CONF_VEHICLE_SOC,
    CONF_VEHICLE_TARGET_SOC,
    CONF_VOLTAGE,
    DEFAULT_VOLTAGE,
    DOMAIN,
)
from .control.models import StaleAction
from .sources import number_limits, read_power

_LOGGER = logging.getLogger(__name__)


def _power_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["sensor"], device_class=["power"])
    )


class SurplusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Dreistufige Einrichtung."""

    VERSION = 1

    def __init__(self) -> None:
        self._daten: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Schritt 1: Was wird gemessen?"""
        fehler: dict[str, str] = {}
        hinweis = ""

        if user_input is not None:
            zustand = self.hass.states.get(user_input[CONF_GRID_POWER])
            wert = read_power(zustand, user_input.get(CONF_GRID_INVERT, False))
            if wert is None:
                fehler["base"] = "cannot_read_source"
            else:
                self._daten.update(user_input)
                return await self.async_step_wallbox()

        elif (letzte := self._letzte_netzmessung()) is not None:
            hinweis = letzte

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default="PV-Ueberschussladen"): str,
                    vol.Required(CONF_GRID_POWER): _power_selector(),
                    vol.Required(CONF_GRID_INVERT, default=False): bool,
                }
            ),
            errors=fehler,
            description_placeholders={"hinweis": hinweis},
        )

    def _letzte_netzmessung(self) -> str | None:
        return None

    async def async_step_wallbox(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Schritt 2: Womit wird gestellt?"""
        fehler: dict[str, str] = {}

        if user_input is not None:
            eid = user_input[CONF_CURRENT_ENTITY]
            grenzen = number_limits(self.hass.states.get(eid))
            if grenzen is None:
                fehler[CONF_CURRENT_ENTITY] = "cannot_read_source"
            elif grenzen[1] < 6:
                # Eine Entitaet, die keine 6 A kann, ist keine Ladestromvorgabe
                fehler[CONF_CURRENT_ENTITY] = "not_a_current_control"
            else:
                await self.async_set_unique_id(eid)
                self._abort_if_unique_id_configured()
                self._daten.update({k: v for k, v in user_input.items() if v})
                return await self.async_step_confirm()

        return self.async_show_form(
            step_id="wallbox",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CURRENT_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain=["number", "input_number"])
                    ),
                    vol.Optional(CONF_SWITCH_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=["switch", "input_boolean"]
                        )
                    ),
                    vol.Optional(CONF_EV_POWER): _power_selector(),
                }
            ),
            errors=fehler,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Schritt 3: Zusammenfassung."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._daten.get(CONF_NAME, "PV-Ueberschussladen"),
                data=self._daten,
            )

        zustand = self.hass.states.get(self._daten[CONF_GRID_POWER])
        wert = read_power(zustand, self._daten.get(CONF_GRID_INVERT, False))
        richtung = "Einspeisung" if (wert or 0) < 0 else "Bezug"
        grenzen = number_limits(self.hass.states.get(self._daten[CONF_CURRENT_ENTITY]))

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "netz": f"{round(wert or 0)} W ({richtung})",
                "strom": f"{grenzen[0]}-{grenzen[1]} A" if grenzen else "unbekannt",
                "ev": (
                    "vorhanden"
                    if self._daten.get(CONF_EV_POWER)
                    else "nicht gesetzt - Ladeerkennung ist ungenauer"
                ),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return SurplusOptionsFlow()


class SurplusOptionsFlow(OptionsFlow):
    """Alles Weitere - nach Themen sortiert."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["quellen", "wallbox", "phasen", "fahrzeug", "experten"],
        )

    async def async_step_quellen(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._speichern(user_input)
        d = self._aktuell()
        return self.async_show_form(
            step_id="quellen",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Optional(CONF_PV_POWER): _power_selector(),
                        vol.Optional(CONF_BATTERY_POWER): _power_selector(),
                        vol.Optional(CONF_BATTERY_INVERT, default=False): bool,
                        vol.Optional(CONF_BATTERY_SOC): selector.EntitySelector(
                            selector.EntitySelectorConfig(
                                domain=["sensor"], device_class=["battery"]
                            )
                        ),
                    }
                ),
                d,
            ),
        )

    async def async_step_wallbox(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._speichern(user_input)
        return self.async_show_form(
            step_id="wallbox",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        # Auch nachtraeglich aenderbar: Wer beim Einrichten
                        # nicht wusste, dass seine Wallbox einen Mindeststrom
                        # hat, braucht die Freigabe erst hinterher - und muesste
                        # sonst die ganze Integration neu anlegen.
                        vol.Optional(CONF_CURRENT_ENTITY): selector.EntitySelector(
                            selector.EntitySelectorConfig(
                                domain=["number", "input_number"]
                            )
                        ),
                        vol.Optional(CONF_SWITCH_ENTITY): selector.EntitySelector(
                            selector.EntitySelectorConfig(
                                domain=["switch", "input_boolean"]
                            )
                        ),
                        vol.Optional(CONF_EV_POWER): _power_selector(),
                        # Nur richtig, wenn die Wallbox hinter dem Zaehler haengt
                        vol.Optional(CONF_SURPLUS_INCLUDES_EV, default=False): bool,
                        vol.Optional(CONF_RESPECT_MANUAL, default=True): bool,
                    }
                ),
                self._aktuell(),
            ),
        )

    async def async_step_phasen(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Die Abbildung Wert -> Phasenzahl kommt aus der Zielentitaet selbst."""
        if user_input is not None:
            return self._speichern(user_input)

        d = self._aktuell()
        schema: dict[Any, Any] = {
            vol.Optional(CONF_PHASE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["select", "switch", "number"])
            ),
            vol.Optional(CONF_PHASE_CURRENTS): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["sensor"], device_class=["current"], multiple=True
                )
            ),
        }

        # Ist bereits eine select-Entitaet gewaehlt, deren Optionen anbieten -
        # damit passt die Zuordnung zu jeder Herstellerbezeichnung.
        eid = d.get(CONF_PHASE_ENTITY)
        if eid and eid.startswith("select."):
            zustand = self.hass.states.get(eid)
            optionen = list(zustand.attributes.get("options", [])) if zustand else []
            if optionen:
                auswahl = selector.SelectSelector(
                    selector.SelectSelectorConfig(options=optionen)
                )
                schema[vol.Optional(CONF_PHASE_OPTION_1P)] = auswahl
                schema[vol.Optional(CONF_PHASE_OPTION_3P)] = auswahl

        return self.async_show_form(
            step_id="phasen",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(schema), d),
        )

    async def async_step_fahrzeug(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._speichern(user_input)
        return self.async_show_form(
            step_id="fahrzeug",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Optional(CONF_VEHICLE_SOC): selector.EntitySelector(
                            selector.EntitySelectorConfig(domain=["sensor"])
                        ),
                        vol.Optional(CONF_VEHICLE_TARGET_SOC): selector.EntitySelector(
                            selector.EntitySelectorConfig(domain=["sensor", "number"])
                        ),
                    }
                ),
                self._aktuell(),
            ),
        )

    async def async_step_experten(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._speichern(user_input)
        return self.async_show_form(
            step_id="experten",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Optional(CONF_VOLTAGE, default=DEFAULT_VOLTAGE): vol.Coerce(
                            float
                        ),
                        vol.Optional(
                            CONF_STALE_ACTION, default=StaleAction.PAUSE.value
                        ): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[s.value for s in StaleAction],
                                translation_key="stale_action",
                            )
                        ),
                    }
                ),
                self._aktuell(),
            ),
        )

    def _aktuell(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    def _speichern(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        optionen = {**self.config_entry.options}
        optionen.update({k: v for k, v in user_input.items() if v not in (None, "")})
        return self.async_create_entry(title="", data=optionen)
