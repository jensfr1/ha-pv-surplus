/*
 * Faehrt Szenarien durch die ORIGINALE TypeScript-Regelung und gibt die
 * Ergebnisse als JSON aus. Gegenstueck zu crosscheck_ts.py, das dieselben
 * Szenarien durch die Python-Portierung schickt und beide vergleicht.
 *
 * Aufruf (aus F:\Developments\ecoflow, weil dort tsx und die Abhaengigkeiten
 * liegen):
 *
 *   npx tsx ../ha-pv-surplus/tests/crosscheck_runner.ts < szenarien.json
 *
 * Zeiten kommen in Sekunden herein und werden hier in Millisekunden umgerechnet
 * - die einzige bewusste Abweichung der Portierung.
 */
import { readFileSync } from 'node:fs';
import {
  applyCap,
  emptyGuardState,
  updateGuard,
  type GuardState,
} from '../../ecoflow/src/ocpp/grid-guard.js';
import {
  emptyProbeState,
  probeTarget,
  type ProbeState,
} from '../../ecoflow/src/ocpp/pv-probe.js';

interface GuardTick {
  now: number;
  gridPowerW: number | null;
  charging: boolean;
  currentLimitA: number | null;
  phases: number;
  minCurrentA: number;
  maxCurrentA: number;
}

interface ProbeTick {
  now: number;
  desiredA: number;
  currentLimitA: number | null;
  gridPowerW: number | null;
  batteryPowerW: number | null;
  pvPowerW: number | null;
  minCurrentA: number;
  maxCurrentA: number;
}

interface Szenario {
  name: string;
  art: 'guard' | 'probe';
  ticks: (GuardTick | ProbeTick)[];
}

const szenarien: Szenario[] = JSON.parse(readFileSync(0, 'utf8'));
const ausgabe: Record<string, unknown[]> = {};

for (const s of szenarien) {
  const schritte: unknown[] = [];

  if (s.art === 'guard') {
    let state: GuardState = emptyGuardState();
    for (const t of s.ticks as GuardTick[]) {
      state = updateGuard(state, { ...t, now: t.now * 1000 });
      schritte.push({
        capA: state.capA,
        // Der Deckel wirkt erst durch applyCap - genau das interessiert
        gedeckelt: applyCap(t.maxCurrentA, state),
      });
    }
  } else {
    let state: ProbeState = emptyProbeState();
    for (const t of s.ticks as ProbeTick[]) {
      const r = probeTarget(state, { ...t, now: t.now * 1000 });
      state = r.state;
      schritte.push({ targetA: r.targetA, ceilingA: state.ceilingA });
    }
  }

  ausgabe[s.name] = schritte;
}

process.stdout.write(JSON.stringify(ausgabe));
