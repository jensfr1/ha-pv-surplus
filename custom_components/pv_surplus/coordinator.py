"""Der Regeltakt und die Verbindung zu Home Assistant.

Aufgabenteilung: Diese Klasse sammelt Messwerte, ruft den Regelkern auf und
gibt dessen Entscheidung an den Actuator weiter. Die Regelung selbst steht in
``control/`` und kennt Home Assistant nicht.

Zwei Entwurfsentscheidungen, die im Alltag den Unterschied machen:

* **Quellen werden abonniert, aber nicht geregelt.** Ein Zaehler, der alle zwei
  Sekunden meldet, wuerde sonst weit ueber hunderttausend Regelvorgaenge am Tag
  ausloesen. Der Callback legt den Wert nur ab.
* **Fester Takt statt Ereignissteuerung.** Die Hysteresen der Regelung (30, 90,
  120, 180 Sekunden) haben nur dann eine verlaessliche Bedeutung, wenn der Takt
  nicht von der Sprechfreudigkeit eines Zaehlers abhaengt.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .actuator import Actuator, monotonic
from .const import (
    CONF_BATTERY_INVERT,
    CONF_BATTERY_POWER,
    CONF_BATTERY_SOC,
    CONF_CURRENT_ENTITY,
    CONF_EV_POWER,
    CONF_GRID_INVERT,
    CONF_GRID_POWER,
    CONF_PHASE_CURRENTS,
    CONF_PHASE_ENTITY,
    CONF_PHASE_OPTION_1P,
    CONF_PHASE_OPTION_3P,
    CONF_PV_POWER,
    CONF_RESPECT_MANUAL,
    CONF_SURPLUS_INCLUDES_EV,
    CONF_SWITCH_ENTITY,
    CONF_VEHICLE_SOC,
    CONF_VEHICLE_TARGET_SOC,
    CONF_VOLTAGE,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MIN_CURRENT,
    DEFAULT_VOLTAGE,
    DOMAIN,
    MANUAL_OVERRIDE_S,
    TICK_INTERVAL_S,
)
from .control.grid_guard import empty_guard_state
from .control.models import (
    ControlInputs,
    ControllerState,
    ControlSettings,
    Decision,
    Mode,
    Status,
)
from .control.phases import (
    CHARGING_W,
    PhaseCommand,
    PhaseInput,
    PhaseResult,
    PhaseState,
    plan_phases,
)
from .control.pv_probe import empty_probe_state
from .control.strategy import decide
from .sources import SourceReader, number_limits

_LOGGER = logging.getLogger(__name__)


class SurplusCoordinator(DataUpdateCoordinator[Decision]):
    """Haelt den Regelkreis am Laufen und verteilt das Ergebnis."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)
        self.entry = entry
        self._sources = SourceReader(hass)
        self._lock = asyncio.Lock()

        daten = {**entry.data, **entry.options}
        self._cfg = daten

        self._actuator = Actuator(
            hass,
            daten[CONF_CURRENT_ENTITY],
            daten.get(CONF_SWITCH_ENTITY),
            daten.get(CONF_PHASE_ENTITY),
        )

        grenzen = number_limits(hass.states.get(daten[CONF_CURRENT_ENTITY]))
        min_a = max(DEFAULT_MIN_CURRENT, grenzen[0]) if grenzen else DEFAULT_MIN_CURRENT
        max_a = grenzen[1] if grenzen else DEFAULT_MAX_CURRENT
        self._step = grenzen[2] if grenzen else 1.0

        # Startwerte; die eigenen Entitaeten setzen sie beim Laden neu
        self.mode: Mode = Mode.PV
        self.manual_a: int = 10
        self.settings = ControlSettings(
            min_current_a=min_a,
            max_current_a=max_a,
            voltage_v=float(daten.get(CONF_VOLTAGE, DEFAULT_VOLTAGE)),
            surplus_includes_ev=bool(daten.get(CONF_SURPLUS_INCLUDES_EV, False)),
        )
        self.phase_switching_allowed = False

        self._state = ControllerState()
        self._phase_state = PhaseState()
        self._plug_epoch = 0
        self._war_eingesteckt = False
        self._gemeldete_gruende: set[str] = set()

        # Fuer die Energiezaehler, die den Rohwert brauchen
        self.last_ev_power_w: float | None = None
        self.last_grid_power_w: float | None = None

    # ── Lebenszyklus ─────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Quellen abonnieren und den Takt starten."""
        quellen = [
            e
            for e in (
                self._cfg.get(CONF_GRID_POWER),
                self._cfg.get(CONF_PV_POWER),
                self._cfg.get(CONF_BATTERY_POWER),
                self._cfg.get(CONF_BATTERY_SOC),
                self._cfg.get(CONF_EV_POWER),
                self._cfg.get(CONF_VEHICLE_SOC),
                self._cfg.get(CONF_VEHICLE_TARGET_SOC),
                self._cfg.get(CONF_CURRENT_ENTITY),
                self._cfg.get(CONF_PHASE_ENTITY),
            )
            if e
        ]
        quellen.extend(self._cfg.get(CONF_PHASE_CURRENTS) or [])

        self.entry.async_on_unload(
            async_track_state_change_event(self.hass, quellen, self._quelle_geaendert)
        )

        # Erst wenn Home Assistant durchgestartet ist: Waehrend des Starts sind
        # Quellen noch "unknown", und wer da regelt, kommandiert 0 A.
        self.entry.async_on_unload(async_at_started(self.hass, self._starte_takt))

    @callback
    def _starte_takt(self, _event) -> None:
        self.entry.async_on_unload(
            async_track_time_interval(
                self.hass,
                self._takt,
                timedelta(seconds=TICK_INTERVAL_S),
                name=f"{DOMAIN}_tick",
                cancel_on_shutdown=True,
            )
        )
        # Der erste Takt schreibt nur den Zustand fort, ohne zu stellen
        self.entry.async_create_task(
            self.hass, self._takt(None, stellen=False), "pv_surplus_first_tick"
        )

    @callback
    def _quelle_geaendert(self, _event: Event) -> None:
        """Nur wahrnehmen - geregelt wird im festen Takt."""

    async def async_stop(self) -> None:
        await self._actuator.release()

    # ── Der Takt ─────────────────────────────────────────────────────────────

    async def _takt(self, _now=None, stellen: bool = True) -> None:
        if self._lock.locked():
            # Ein blockierender Service-Aufruf kann laenger dauern als der Takt
            return
        async with self._lock:
            try:
                await self._regeln(stellen)
            except Exception:
                _LOGGER.exception("Regeltakt fehlgeschlagen")

    async def _regeln(self, stellen: bool) -> None:
        now = monotonic()
        inputs = self._messwerte(now)

        # Phasen zuerst: Sperre und Tastbetrieb sollen im selben Takt schon mit
        # der neuen Phasenzahl rechnen.
        phasen = self._phasen_planen(inputs, now)
        self._phase_state = phasen.state
        inputs = replace(inputs, phases=phasen.phases)
        self._nach_phasenwechsel(phasen, now)

        entscheidung = decide(
            self._state, inputs, self.settings, self.mode, self.manual_a
        )
        self._state = entscheidung.state

        if phasen.hold_current_a is not None:
            # Waehrend einer Umschaltung regelt der Laderegler nicht mit
            entscheidung = replace(
                entscheidung,
                target_a=phasen.hold_current_a,
                status=Status.PAUSED,
                reasons=(*entscheidung.reasons, phasen.reason or "Phasenumschaltung"),
            )
        elif phasen.reason:
            entscheidung = replace(
                entscheidung, reasons=(*entscheidung.reasons, phasen.reason)
            )

        self._melden(entscheidung)

        if stellen:
            await self._phasen_stellen(phasen)
            if entscheidung.should_apply:
                await self._stellen(entscheidung, now)

        self.async_set_updated_data(entscheidung)

    def _messwerte(self, now: float) -> ControlInputs:
        c = self._cfg
        netz = c.get(CONF_GRID_POWER)
        self.last_grid_power_w = self._sources.power(
            netz, now, bool(c.get(CONF_GRID_INVERT, False))
        )
        self.last_ev_power_w = self._sources.power(c.get(CONF_EV_POWER), now)

        return ControlInputs(
            now=now,
            grid_power_w=self.last_grid_power_w,
            pv_power_w=self._sources.power(c.get(CONF_PV_POWER), now),
            battery_power_w=self._sources.power(
                c.get(CONF_BATTERY_POWER), now, bool(c.get(CONF_BATTERY_INVERT, False))
            ),
            battery_soc=self._sources.number(c.get(CONF_BATTERY_SOC), now),
            ev_power_w=self.last_ev_power_w,
            current_limit_a=self._actuator.reported_current(),
            phases=self._phase_state.effective_phases(),
            vehicle_soc=self._sources.number(c.get(CONF_VEHICLE_SOC), now),
            vehicle_target_soc=self._sources.number(
                c.get(CONF_VEHICLE_TARGET_SOC), now
            ),
            grid_missing_since=self._sources.missing_since(netz),
        )

    # ── Phasen ───────────────────────────────────────────────────────────────

    def _phasen_planen(self, inputs: ControlInputs, now: float) -> PhaseResult:
        c = self._cfg
        eingesteckt = (inputs.ev_power_w or 0.0) > CHARGING_W or bool(
            self._cfg.get(CONF_SWITCH_ENTITY)
        )
        # Jedes Anstecken beginnt einen neuen Zyklus - fahrzeugbezogene
        # Erkenntnisse gelten nur fuer das Auto, an dem sie gewonnen wurden.
        if eingesteckt and not self._war_eingesteckt:
            self._plug_epoch += 1
        self._war_eingesteckt = eingesteckt

        stroeme = self._phasenstroeme(now)
        gemeldet = self._gemeldete_phasen()

        return plan_phases(
            self._phase_state,
            PhaseInput(
                now=now,
                plug_epoch=self._plug_epoch,
                plugged=eingesteckt,
                ev_power_w=inputs.ev_power_w,
                phase_currents=stroeme,
                # Bewusst der GEMESSENE Ueberschuss, nie der getastete Wert -
                # sonst entstuende eine Mitkopplung.
                surplus_w=max(0.0, -(inputs.grid_power_w or 0.0))
                if inputs.grid_power_w is not None
                else None,
                reported_phases=gemeldet,
                can_switch=bool(c.get(CONF_PHASE_ENTITY)),
                switching_enabled=self.phase_switching_allowed,
                mode_allows_switching=self.mode in (Mode.PV, Mode.MINPV),
                voltage_v=self.settings.voltage_v,
                min_current_a=self.settings.min_current_a,
                max_current_1p_a=min(16, self.settings.max_current_a),
                guard_capped=self._state.guard.cap_a is not None,
                guard_cap_a=self._state.guard.cap_a,
            ),
        )

    def _phasenstroeme(
        self, now: float
    ) -> tuple[float | None, float | None, float | None]:
        ids = list(self._cfg.get(CONF_PHASE_CURRENTS) or [])
        werte = [self._sources.number(e, now) for e in ids[:3]]
        while len(werte) < 3:
            werte.append(None)
        return (werte[0], werte[1], werte[2])

    def _gemeldete_phasen(self) -> int | None:
        """Liest die Rueckmeldung der Umschalt-Entitaet, je nach deren Art."""
        eid = self._cfg.get(CONF_PHASE_ENTITY)
        if not eid:
            return None
        zustand = self.hass.states.get(eid)
        if zustand is None or zustand.state in ("unavailable", "unknown"):
            return None
        domain = eid.split(".")[0]
        if domain == "switch":
            return 3 if zustand.state == "on" else 1
        if domain == "select":
            if zustand.state == self._cfg.get(CONF_PHASE_OPTION_3P):
                return 3
            if zustand.state == self._cfg.get(CONF_PHASE_OPTION_1P):
                return 1
            return None
        try:
            return int(float(zustand.state))
        except (TypeError, ValueError):
            return None

    def _nach_phasenwechsel(self, phasen: PhaseResult, now: float) -> None:
        """Setzt zurueck, was nach einem Phasenwechsel nicht mehr gilt."""
        if phasen.reset_probe_ceiling:
            # Eine getastete Grenze gilt in Watt, nicht in Ampere: 12 A
            # einphasig sind 2760 W, dreiphasig aber 8280 W. Auch der Deckel der
            # Netzsperre wurde fuer die alte Phasenzahl gerechnet.
            self._state = replace(
                self._state, probe=empty_probe_state(), guard=empty_guard_state()
            )
        if phasen.freeze_guard:
            self._state = replace(
                self._state, guard=replace(self._state.guard, import_since=None)
            )
        if phasen.freeze_probe:
            self._state = replace(
                self._state, probe=replace(self._state.probe, last_step_at=now)
            )

    async def _phasen_stellen(self, phasen: PhaseResult) -> None:
        for befehl, wert in phasen.commands:
            if befehl is PhaseCommand.SET_PHASES and wert is not None:
                option = (
                    self._cfg.get(CONF_PHASE_OPTION_3P)
                    if wert >= 3
                    else self._cfg.get(CONF_PHASE_OPTION_1P)
                )
                await self._actuator.set_phases(wert, option)

    # ── Stellen ──────────────────────────────────────────────────────────────

    async def _stellen(self, entscheidung: Decision, now: float) -> None:
        """Wendet die Entscheidung an - und respektiert Eingriffe von Hand."""
        if self._actuator.override_active(now):
            return

        if self._cfg.get(CONF_RESPECT_MANUAL) and self._actuator.external_change(now):
            _LOGGER.info(
                "Ladestrom wurde von aussen geaendert - Regelung ruht %d Minuten",
                int(MANUAL_OVERRIDE_S / 60),
            )
            self._actuator.begin_override(now, MANUAL_OVERRIDE_S)
            return

        await self._actuator.apply(entscheidung.target_a, now, self._step)

    def _melden(self, entscheidung: Decision) -> None:
        """Jede Begruendung einmal ins Log, nicht in jedem Takt erneut."""
        neu = set(entscheidung.reasons) - self._gemeldete_gruende
        for grund in entscheidung.reasons:
            if grund in neu:
                _LOGGER.info("%s", grund)
        self._gemeldete_gruende = set(entscheidung.reasons)

    # ── Von den eigenen Entitaeten gerufen ───────────────────────────────────

    async def async_set_mode(self, mode: Mode) -> None:
        self.mode = mode
        self._state = replace(self._state, below_min_since=None)
        await self._takt()

    async def async_set_setting(self, **werte) -> None:
        self.settings = replace(self.settings, **werte)
        await self._takt()

    async def async_set_manual_current(self, ampere: int) -> None:
        self.manual_a = ampere
        await self._takt()

    @property
    def status(self) -> Status:
        return self.data.status if self.data else Status.IDLE

    @property
    def device_name(self) -> str:
        return str(self._cfg.get("name") or "PV-Ueberschussladen")

    @property
    def unique_base(self) -> str:
        return self.entry.entry_id
