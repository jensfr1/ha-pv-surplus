"""Automatische Phasenumschaltung zwischen einphasig und dreiphasig.

Das Problem in Zahlen: Dreiphasig sind 6 A Mindeststrom bereits 4140 W. An einem
truben Tag mit 2 kW Ueberschuss steht eine dreiphasige Wallbox deshalb still,
obwohl einphasig laengst geladen werden koennte - dort sind 6 A nur 1380 W.

Die zentrale Unterscheidung, ohne die jede Umsetzung falsch rechnet:

  switch_phases      Stellung der Wallbox: 1 oder 3
  effective_phases   was das Auto tatsaechlich zieht: 1, 2 oder 3

Ein zweiphasig ladendes Fahrzeug an einer dreiphasig gestellten Box ergibt
``switch_phases=3, effective_phases=2``. Wer beides in eine Variable steckt,
rechnet dauerhaft mit 690 statt 460 V*A und gibt dem Auto ein Drittel zu wenig.

Bewusst frei von I/O, mit ``now`` als Parameter - wie Sperre und Tastregler.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final


class PhaseMode(StrEnum):
    """Zustaende der Umschaltung."""

    STABLE = "stable"
    #: Bedingung liegt an, die Haltezeit laeuft.
    ARMED_UP = "armed_up"
    ARMED_DOWN = "armed_down"
    #: Ladung wird beendet, bevor geschaltet wird.
    SEQ_STOP = "seq_stop"
    #: Schaltbefehl gesetzt, warte auf Rueckmeldung.
    SEQ_SET = "seq_set"
    #: Freigabe wieder erteilt, warte auf Ladestrom.
    SEQ_START = "seq_start"
    #: Mindestverweildauer nach einer Umschaltung.
    COOLDOWN = "cooldown"
    #: Dauerhaft gesperrt, mit Grund.
    BLOCKED = "blocked"


class PhaseCommand(StrEnum):
    STOP_CHARGE = "stop_charge"
    SET_PHASES = "set_phases"
    START_CHARGE = "start_charge"


# ── Zeiten ────────────────────────────────────────────────────────────────────

#: Hochschalten ist der teure Fehler: Ladepause plus womoeglich ein Fahrzeug,
#: das nicht wieder anlaeuft. Also der geduldigere Timer. Wolkenluecken dauern
#: typisch ein bis drei Minuten.
UP_HOLD_S: Final = 300.0

#: Runterschalten ist der billige, umkehrbare Fehler - und zu lange dreiphasig
#: zu verharren kostet sofort, weil unterhalb von 4140 W gar nicht geregelt
#: werden kann. Deshalb bewusst asymmetrisch.
DOWN_HOLD_S: Final = 120.0

#: Hat die Netzsperre bereits bis auf null gedeckelt, liegt der Beweis vor, dass
#: dreiphasig nicht traegt. Dann muss nicht zwei Minuten gewartet werden.
DOWN_HOLD_GUARD_S: Final = 60.0

#: Mindestverweildauer nach jeder Umschaltung. Deckelt auf hoechstens vier
#: Umschaltungen je Stunde.
MIN_DWELL_S: Final = 900.0

#: In den ersten Minuten einer Ladung rampen die Stroeme und die Phasenerkennung
#: ist noch nicht belastbar.
START_GRACE_S: Final = 180.0

#: Notbremse gegen unvorhergesehene Muster.
SWITCH_BUDGET: Final = 6
BUDGET_WINDOW_S: Final = 6 * 3600.0

# Zeitschranken der Umschaltsequenz
STOP_TIMEOUT_S: Final = 20.0
SET_TIMEOUT_S: Final = 30.0
START_TIMEOUT_S: Final = 120.0
#: So lange wird auf ein schlafendes Fahrzeug gewartet, bevor aufgegeben wird.
WAIT_CAR_S: Final = 600.0

#: Nach so vielen erfolglosen Sequenzen wird bis zum naechsten Anstecken gesperrt.
MAX_FAILURES: Final = 3

#: Ab diesem Strom gilt eine Phase als benutzt. Manche Boxen melden auf
#: abgeschalteten Phasen Rauschen bis rund 1,5 A.
PHASE_ACTIVE_A: Final = 2.0

#: So viele uebereinstimmende Messungen, bevor die Phasenzahl uebernommen wird.
DETECT_EVIDENCE: Final = 3

#: Ab dieser Ladeleistung gilt die Wallbox als ladend.
CHARGING_W: Final = 100.0


@dataclass(frozen=True, slots=True)
class PhaseState:
    """Zustand der Umschaltung. Unveraenderlich."""

    mode: PhaseMode = PhaseMode.STABLE
    #: Stellung der Wallbox.
    switch_phases: int = 1
    #: Angestrebte Stellung waehrend einer Sequenz.
    target_phases: int = 1
    pending_since: float | None = None
    step_since: float | None = None
    last_switch_at: float | None = None
    switch_times: tuple[float, ...] = ()
    failures: int = 0
    charging_since: float | None = None
    #: Zaehler fuer die Entprellung der Erkennung.
    detect_candidate: int | None = None
    detect_evidence: int = 0
    #: Entprellt uebernommene Phasenzahl des Fahrzeugs.
    detected_phases: int | None = None
    #: Bestaetigte Faehigkeit des Fahrzeugs (nur wenn die Box mehr anbot).
    vehicle_max_phases: int | None = None
    blocked_reason: str | None = None
    #: Steckzyklus, an dem fahrzeugbezogene Erkenntnisse haengen.
    plug_epoch: int = 0

    def effective_phases(self) -> int:
        """Phasenzahl fuer die Ampere-Rechnung - was das Auto wirklich zieht."""
        return self.detected_phases or self.switch_phases


@dataclass(frozen=True, slots=True)
class PhaseInput:
    """Messwerte und Randbedingungen eines Takts."""

    now: float
    plug_epoch: int = 0
    plugged: bool = False
    ev_power_w: float | None = None
    phase_currents: tuple[float | None, float | None, float | None] = (None, None, None)
    #: Gemessener Ueberschuss - NIE der getastete Wert (sonst Mitkopplung).
    surplus_w: float | None = None
    #: Rueckmeldung der Umschalt-Entitaet.
    reported_phases: int | None = None
    can_switch: bool = False
    switching_enabled: bool = False
    mode_allows_switching: bool = True
    voltage_v: float = 230.0
    min_current_a: int = 6
    max_current_1p_a: int = 16
    guard_capped: bool = False
    guard_cap_a: int | None = None


@dataclass(frozen=True, slots=True)
class PhaseResult:
    """Ergebnis eines Takts."""

    state: PhaseState
    #: Phasenzahl fuer die Ampere-Rechnung DIESES Takts.
    phases: int
    commands: tuple[tuple[PhaseCommand, int | None], ...] = ()
    #: Ist er gesetzt, regelt der Laderegler in diesem Takt nicht selbst.
    hold_current_a: int | None = None
    #: Netzbezugsmessung zuruecksetzen (Einschaltstoss nach dem Wiederanlauf).
    freeze_guard: bool = False
    freeze_probe: bool = False
    #: Getastete Obergrenze verwerfen - sie gilt in Watt, nicht in Ampere.
    reset_probe_ceiling: bool = False
    reason: str | None = None


def _erwartete_phasen(state: PhaseState) -> int:
    """Wieviele Phasen das Auto dreiphasig nutzen wuerde."""
    return state.vehicle_max_phases or 3


def upper_threshold_w(state: PhaseState, inp: PhaseInput) -> float:
    """Ab hier lohnt dreiphasig.

    Ein Ampere Reserve je Phase ueber dem dreiphasigen Minimum. Direkt an der
    Grenze hochzuschalten hiesse, im schmalsten denkbaren Betriebspunkt zu
    landen: Jede Wolke drueckt sofort darunter und erzwingt Netzbezug oder
    Stillstand. Zugleich sicher ueber dem einphasigen Maximum, damit nicht
    geschaltet wird, solange einphasig noch Luft hat.
    """
    p3 = _erwartete_phasen(state)
    p3_min = inp.min_current_a * inp.voltage_v * p3
    p1_max = inp.max_current_1p_a * inp.voltage_v
    reserve = inp.voltage_v * p3
    return max(p3_min + reserve, p1_max + reserve)


def lower_threshold_w(state: PhaseState, inp: PhaseInput) -> float:
    """Darunter kann dreiphasig nicht mehr geregelt werden."""
    p3 = _erwartete_phasen(state)
    return inp.min_current_a * inp.voltage_v * p3 - inp.voltage_v


def _budget_frei(state: PhaseState, now: float) -> bool:
    aktuell = [t for t in state.switch_times if now - t < BUDGET_WINDOW_S]
    return len(aktuell) < SWITCH_BUDGET


def detect_phases(state: PhaseState, inp: PhaseInput) -> PhaseState:
    """Erkennt aus den Phasenstroemen, wieviele Phasen das Auto nutzt."""
    laedt = (inp.ev_power_w or 0.0) > CHARGING_W
    charging_since = state.charging_since
    if laedt and charging_since is None:
        charging_since = inp.now
    elif not laedt:
        charging_since = None

    state = replace(state, charging_since=charging_since)

    # In der ersten Minute rampen die Stroeme - da misst man Unsinn.
    if not laedt or charging_since is None or inp.now - charging_since < 60.0:
        return state
    if all(c is None for c in inp.phase_currents):
        return state

    kandidat = sum(
        1 for c in inp.phase_currents if c is not None and c > PHASE_ACTIVE_A
    )
    if kandidat == 0:
        return state

    if kandidat == state.detect_candidate:
        evidence = state.detect_evidence + 1
    else:
        return replace(state, detect_candidate=kandidat, detect_evidence=1)

    if evidence < DETECT_EVIDENCE:
        return replace(state, detect_evidence=evidence)

    neu = replace(state, detect_evidence=evidence, detected_phases=kandidat)
    # Nur wenn die Box mehr anbot, als genommen wird, liegt es am Fahrzeug.
    if state.switch_phases >= 3 and kandidat < 3 and (inp.ev_power_w or 0) > 1000:
        neu = replace(neu, vehicle_max_phases=kandidat)
    return neu


def plan_phases(state: PhaseState, inp: PhaseInput) -> PhaseResult:
    """Ein Takt der Phasenumschaltung."""
    # Ein anderer Steckzyklus koennte ein anderes Auto sein.
    if inp.plug_epoch != state.plug_epoch:
        state = PhaseState(
            switch_phases=state.switch_phases,
            target_phases=state.switch_phases,
            plug_epoch=inp.plug_epoch,
            switch_times=state.switch_times,
        )

    state = detect_phases(state, inp)

    # Fremde Umschaltung uebernehmen, statt dagegen anzuregeln: Zwei Automatiken,
    # die sich gegenseitig korrigieren, sind das schlimmste Pendelmuster.
    if (
        inp.reported_phases is not None
        and state.mode not in (PhaseMode.SEQ_SET, PhaseMode.SEQ_START)
        and inp.reported_phases != state.switch_phases
    ):
        state = replace(
            state,
            switch_phases=inp.reported_phases,
            target_phases=inp.reported_phases,
            mode=PhaseMode.COOLDOWN,
            last_switch_at=inp.now,
            pending_since=None,
        )
        return PhaseResult(
            state=state,
            phases=state.effective_phases(),
            reset_probe_ceiling=True,
            reason=f"Phasen extern auf {inp.reported_phases} gestellt",
        )

    if state.mode in (
        PhaseMode.SEQ_STOP,
        PhaseMode.SEQ_SET,
        PhaseMode.SEQ_START,
    ):
        return _sequenz(state, inp)

    if not (inp.can_switch and inp.switching_enabled and inp.mode_allows_switching):
        return PhaseResult(state=state, phases=state.effective_phases())

    if state.mode is PhaseMode.BLOCKED:
        return PhaseResult(state=state, phases=state.effective_phases())

    if state.vehicle_max_phases == 1:
        # Umschalten brächte nichts - die Box steht auf 3, das Auto nimmt L1.
        return PhaseResult(
            state=replace(
                state,
                mode=PhaseMode.BLOCKED,
                blocked_reason="Fahrzeug laedt nur einphasig",
            ),
            phases=1,
        )

    return _pruefe_bedingung(state, inp)


def _pruefe_bedingung(state: PhaseState, inp: PhaseInput) -> PhaseResult:
    """Haelt die Auf- oder Abschaltbedingung lange genug an?"""
    phases = state.effective_phases()

    if state.mode is PhaseMode.COOLDOWN:
        if (
            state.last_switch_at is None
            or inp.now - state.last_switch_at >= MIN_DWELL_S
        ):
            state = replace(state, mode=PhaseMode.STABLE)
        else:
            return PhaseResult(state=state, phases=phases)

    if (
        state.charging_since is not None
        and inp.now - state.charging_since < START_GRACE_S
    ):
        return PhaseResult(state=state, phases=phases)

    if inp.surplus_w is None:
        return PhaseResult(state=replace(state, pending_since=None), phases=phases)

    hoch = inp.surplus_w >= upper_threshold_w(state, inp) and state.switch_phases == 1
    runter = inp.surplus_w < lower_threshold_w(state, inp) and state.switch_phases >= 3

    # Sonderfall: Die Sperre hat dreiphasig bis auf null gedeckelt, aber fuer
    # einphasig reicht es. Dann ist Runterschalten die einzige Handlung, die die
    # Ladung rettet.
    dringend = (
        inp.guard_cap_a == 0
        and state.switch_phases >= 3
        and inp.surplus_w >= inp.min_current_a * inp.voltage_v
    )
    if dringend:
        runter = True

    # Kein Hochschalten, solange die Sperre deckelt: Der Ueberschusswert, auf dem
    # die Bedingung fusst, ist in genau dieser Lage nachweislich falsch.
    if inp.guard_capped:
        hoch = False

    if not (hoch or runter):
        return PhaseResult(state=replace(state, pending_since=None), phases=phases)

    modus = PhaseMode.ARMED_UP if hoch else PhaseMode.ARMED_DOWN
    haltezeit = UP_HOLD_S if hoch else (DOWN_HOLD_GUARD_S if dringend else DOWN_HOLD_S)

    if state.mode is not modus or state.pending_since is None:
        return PhaseResult(
            state=replace(state, mode=modus, pending_since=inp.now), phases=phases
        )
    if inp.now - state.pending_since < haltezeit:
        return PhaseResult(state=state, phases=phases)

    if not _budget_frei(state, inp.now):
        return PhaseResult(
            state=state,
            phases=phases,
            reason="Schaltbudget erschoepft - Phasenzahl bleibt stehen",
        )

    ziel = 3 if hoch else 1
    return _sequenz_beginnen(state, inp, ziel)


def _sequenz_beginnen(state: PhaseState, inp: PhaseInput, ziel: int) -> PhaseResult:
    """Startet die Umschaltung - kalt ohne Sequenz, unter Last mit."""
    laedt = (inp.ev_power_w or 0.0) > CHARGING_W

    if not laedt:
        # Nichts zu unterbrechen: direkt stellen, ohne Mindestverweildauer.
        neu = replace(
            state,
            mode=PhaseMode.STABLE,
            switch_phases=ziel,
            target_phases=ziel,
            pending_since=None,
            detected_phases=None,
            detect_evidence=0,
            detect_candidate=None,
        )
        return PhaseResult(
            state=neu,
            phases=ziel,
            commands=((PhaseCommand.SET_PHASES, ziel),),
            reset_probe_ceiling=True,
            reason=f"kalt auf {ziel}-phasig gestellt",
        )

    return PhaseResult(
        state=replace(
            state,
            mode=PhaseMode.SEQ_STOP,
            target_phases=ziel,
            step_since=inp.now,
            pending_since=None,
        ),
        phases=state.effective_phases(),
        commands=((PhaseCommand.STOP_CHARGE, None),),
        hold_current_a=0,
        freeze_guard=True,
        freeze_probe=True,
        reason=f"schalte auf {ziel}-phasig um",
    )


def _sequenz(state: PhaseState, inp: PhaseInput) -> PhaseResult:
    """Faehrt die Umschaltsequenz. Niemals unter Last schalten."""
    seit = inp.now - (state.step_since or inp.now)
    laedt = (inp.ev_power_w or 0.0) > CHARGING_W

    if state.mode is PhaseMode.SEQ_STOP:
        if not laedt:
            return PhaseResult(
                state=replace(state, mode=PhaseMode.SEQ_SET, step_since=inp.now),
                phases=state.effective_phases(),
                commands=((PhaseCommand.SET_PHASES, state.target_phases),),
                hold_current_a=0,
                freeze_guard=True,
                freeze_probe=True,
            )
        if seit >= STOP_TIMEOUT_S:
            return _abbrechen(state, inp, "Ladung endete nicht rechtzeitig")
        return PhaseResult(
            state=state,
            phases=state.effective_phases(),
            hold_current_a=0,
            freeze_guard=True,
            freeze_probe=True,
        )

    if state.mode is PhaseMode.SEQ_SET:
        angekommen = (
            inp.reported_phases is None or inp.reported_phases == state.target_phases
        )
        if angekommen and seit >= 5.0:
            return PhaseResult(
                state=replace(
                    state,
                    mode=PhaseMode.SEQ_START,
                    switch_phases=state.target_phases,
                    step_since=inp.now,
                    detected_phases=None,
                    detect_evidence=0,
                    detect_candidate=None,
                ),
                # Ab hier wird bereits mit der neuen Phasenzahl gerechnet
                phases=state.target_phases,
                commands=((PhaseCommand.START_CHARGE, None),),
                freeze_guard=True,
                freeze_probe=True,
                reset_probe_ceiling=True,
            )
        if seit >= SET_TIMEOUT_S:
            return _abbrechen(state, inp, "Wallbox nahm die Phasenzahl nicht an")
        return PhaseResult(
            state=state,
            phases=state.effective_phases(),
            hold_current_a=0,
            freeze_guard=True,
            freeze_probe=True,
        )

    # SEQ_START
    if laedt:
        return PhaseResult(
            state=replace(
                state,
                mode=PhaseMode.COOLDOWN,
                last_switch_at=inp.now,
                switch_times=(*state.switch_times, inp.now),
                failures=0,
                step_since=None,
            ),
            phases=state.switch_phases,
            freeze_guard=True,
            reason=f"laeuft wieder, jetzt {state.switch_phases}-phasig",
        )
    if seit >= WAIT_CAR_S:
        # Nicht zurueckschalten: Das erzeugte eine zweite Unterbrechung ohne
        # jede Garantie, dass das Auto danach aufwacht. Die Freigabe bleibt
        # gesetzt, damit es von selbst anlaufen kann.
        return PhaseResult(
            state=replace(
                state,
                mode=PhaseMode.BLOCKED,
                blocked_reason="Fahrzeug startet nach der Umschaltung nicht",
                failures=state.failures + 1,
                last_switch_at=inp.now,
                switch_times=(*state.switch_times, inp.now),
            ),
            phases=state.switch_phases,
            reason="Fahrzeug laeuft nach der Umschaltung nicht wieder an",
        )
    return PhaseResult(state=state, phases=state.switch_phases, freeze_guard=True)


def _abbrechen(state: PhaseState, inp: PhaseInput, grund: str) -> PhaseResult:
    """Sequenz abbrechen, alten Zustand wiederherstellen."""
    failures = state.failures + 1
    gesperrt = failures >= MAX_FAILURES
    return PhaseResult(
        state=replace(
            state,
            mode=PhaseMode.BLOCKED if gesperrt else PhaseMode.COOLDOWN,
            blocked_reason=grund if gesperrt else None,
            target_phases=state.switch_phases,
            failures=failures,
            last_switch_at=inp.now,
            step_since=None,
        ),
        phases=state.effective_phases(),
        commands=(
            (PhaseCommand.SET_PHASES, state.switch_phases),
            (PhaseCommand.START_CHARGE, None),
        ),
        reason=f"Umschaltung abgebrochen: {grund}",
    )
