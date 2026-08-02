"""Vergleicht die Python-Portierung mit der laufenden TypeScript-Regelung.

Die uebersetzten Unit-Tests beweisen, dass die Portierung die *dokumentierten*
Faelle trifft. Sie beweisen nicht, dass sie sich auch dazwischen gleich verhaelt
- und genau dort sitzen Portierungsfehler: eine Rundung, ein ``<`` statt ``<=``,
eine Reihenfolge im Zustandsuebergang.

Dieses Werkzeug schickt dieselben Szenarien durch beide Implementierungen und
vergleicht Schritt fuer Schritt. Kein pytest: Es braucht Node und das
Nachbarprojekt, beides ist in CI nicht gegeben.

    python tests/crosscheck_ts.py

Erwartet ``F:/Developments/ecoflow`` als Nachbarverzeichnis mit installierten
Abhaengigkeiten.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import conftest  # noqa: F401,E402  (legt die synthetischen Pakete an)

from custom_components.pv_surplus.control.grid_guard import (  # noqa: E402
    GuardInput,
    apply_cap,
    empty_guard_state,
    update_guard,
)
from custom_components.pv_surplus.control.pv_probe import (  # noqa: E402
    ProbeInput,
    empty_probe_state,
    probe_target,
)

ECOFLOW = Path(__file__).resolve().parents[2] / "ecoflow"
RUNNER = Path(__file__).resolve().parent / "crosscheck_runner.ts"

T0 = 1_000_000.0


def _guard_ticks(**feste) -> list[dict]:
    """Baut eine Reihe Guard-Takte mit gemeinsamen Vorgaben."""
    basis = {
        "charging": True,
        "currentLimitA": 16,
        "phases": 3,
        "minCurrentA": 6,
        "maxCurrentA": 16,
    }
    basis.update(feste)
    return basis  # type: ignore[return-value]


def szenarien() -> list[dict]:
    """Realistische Verlaeufe, nicht nur die Faelle aus den Unit-Tests."""
    g = _guard_ticks()
    p = {
        "desiredA": 0,
        "minCurrentA": 6,
        "maxCurrentA": 16,
        "batteryPowerW": 0.0,
        "pvPowerW": 3000.0,
    }

    # Netzbezug schwillt an und klingt ab, ueber eine gute Stunde
    verlauf = [
        -2000.0,
        -1500.0,
        -200.0,
        300.0,
        900.0,
        1800.0,
        2600.0,
        3100.0,
        2400.0,
        1200.0,
        400.0,
        -100.0,
        -800.0,
        -1900.0,
        -3000.0,
        -3000.0,
        -2800.0,
        -400.0,
        700.0,
        2200.0,
        5000.0,
        9000.0,
        4000.0,
        500.0,
        -600.0,
        -2000.0,
        -2500.0,
        -2500.0,
        -2500.0,
        -2500.0,
    ]
    guard_lang = [
        {**g, "now": T0 + i * 15.0, "gridPowerW": w} for i, w in enumerate(verlauf)
    ]

    # Wallbox laedt nicht - die Sperre darf nie greifen
    guard_ohne_ladung = [
        {**g, "charging": False, "now": T0 + i * 15.0, "gridPowerW": 4000.0}
        for i in range(20)
    ]

    # Messwert faellt zwischendurch aus
    guard_luecken = []
    for i in range(24):
        w = None if 6 <= i < 10 else (2500.0 if i % 3 else -1000.0)
        guard_luecken.append({**g, "now": T0 + i * 15.0, "gridPowerW": w})

    # Tastbetrieb bei Nulleinspeisung, spaeter kippt es in Netzbezug
    probe_ticks = []
    limit = 0
    for i in range(40):
        netz = 0.0 if i < 20 else (120.0 if i % 2 else 60.0)
        probe_ticks.append(
            {
                **p,
                "now": T0 + i * 15.0,
                "currentLimitA": limit,
                "gridPowerW": netz,
            }
        )
        # Das Limit folgt dem, was der Regler im vorigen Takt wollte - grob
        # nachgebildet, damit die Rueckkopplung mitgeprueft wird
        limit = min(16, limit + 1) if i % 6 == 5 else limit

    # Hausbatterie springt ein
    probe_batterie = [
        {
            **p,
            "now": T0 + i * 15.0,
            "currentLimitA": 10,
            "gridPowerW": 0.0,
            "batteryPowerW": -900.0 if i > 5 else 0.0,
        }
        for i in range(20)
    ]

    return [
        {"name": "guard_lang", "art": "guard", "ticks": guard_lang},
        {"name": "guard_ohne_ladung", "art": "guard", "ticks": guard_ohne_ladung},
        {"name": "guard_luecken", "art": "guard", "ticks": guard_luecken},
        {"name": "probe_nulleinspeisung", "art": "probe", "ticks": probe_ticks},
        {"name": "probe_batterie", "art": "probe", "ticks": probe_batterie},
    ]


def python_laufen_lassen(szen: list[dict]) -> dict[str, list[dict]]:
    """Dieselben Szenarien durch die Portierung."""
    ergebnis: dict[str, list[dict]] = {}
    for s in szen:
        schritte: list[dict] = []
        if s["art"] == "guard":
            state = empty_guard_state()
            for t in s["ticks"]:
                state = update_guard(
                    state,
                    GuardInput(
                        now=t["now"],
                        grid_power_w=t["gridPowerW"],
                        charging=t["charging"],
                        current_limit_a=t["currentLimitA"],
                        phases=t["phases"],
                        min_current_a=t["minCurrentA"],
                        max_current_a=t["maxCurrentA"],
                    ),
                )
                schritte.append(
                    {
                        "capA": state.cap_a,
                        "gedeckelt": apply_cap(t["maxCurrentA"], state),
                    }
                )
        else:
            state = empty_probe_state()
            for t in s["ticks"]:
                r = probe_target(
                    state,
                    ProbeInput(
                        now=t["now"],
                        desired_a=t["desiredA"],
                        current_limit_a=t["currentLimitA"],
                        grid_power_w=t["gridPowerW"],
                        battery_power_w=t["batteryPowerW"],
                        pv_power_w=t["pvPowerW"],
                        min_current_a=t["minCurrentA"],
                        max_current_a=t["maxCurrentA"],
                    ),
                )
                state = r.state
                schritte.append({"targetA": r.target_a, "ceilingA": state.ceiling_a})
        ergebnis[s["name"]] = schritte
    return ergebnis


def typescript_laufen_lassen(szen: list[dict]) -> dict[str, list[dict]]:
    """Dieselben Szenarien durch das Original."""
    if not ECOFLOW.is_dir():
        raise SystemExit(f"Nachbarprojekt nicht gefunden: {ECOFLOW}")
    lauf = subprocess.run(
        ["npx", "tsx", str(RUNNER)],
        cwd=ECOFLOW,
        input=json.dumps(szen),
        capture_output=True,
        text=True,
        shell=True,
        check=False,
    )
    if lauf.returncode != 0:
        raise SystemExit(f"TypeScript-Lauf fehlgeschlagen:\n{lauf.stderr[-2000:]}")
    return json.loads(lauf.stdout)


def main() -> int:
    szen = szenarien()
    py = python_laufen_lassen(szen)
    ts = typescript_laufen_lassen(szen)

    abweichungen = 0
    for name in py:
        a, b = py[name], ts.get(name, [])
        if len(a) != len(b):
            print(f"FEHLER {name}: {len(a)} Schritte gegen {len(b)}")
            abweichungen += 1
            continue
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            if x != y:
                print(f"FEHLER {name} Schritt {i}: python={x} typescript={y}")
                abweichungen += 1
        if abweichungen == 0:
            print(f"  ok  {name} ({len(a)} Takte)")

    if abweichungen:
        print(f"\n{abweichungen} Abweichungen.")
        return 1
    print(f"\nAlle {sum(len(v) for v in py.values())} Takte identisch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
