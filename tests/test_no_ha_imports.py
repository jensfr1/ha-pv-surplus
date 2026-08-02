"""Bewacht die Architekturgrenze.

Der Regelkern unter ``control/`` darf Home Assistant nicht kennen. Diese Regel
laesst sich nicht durch Disziplin allein halten - ein einziger bequemer Import
im falschen Moment, und die Tests laufen nur noch mit installiertem Home
Assistant. Also wird sie hier maschinell geprueft, statt sie zu dokumentieren
und zu hoffen.

Der Scan liest den Quelltext, statt die Module zu importieren: Ein Import wuerde
den Fehler erst ausloesen, wenn das fehlende Paket tatsaechlich fehlt.
"""

from __future__ import annotations

import ast
from pathlib import Path

CONTROL = (
    Path(__file__).resolve().parents[1] / "custom_components" / "pv_surplus" / "control"
)

#: Was im Regelkern nichts zu suchen hat. ``homeassistant`` ist der eigentliche
#: Punkt; die uebrigen wuerden Zeit oder Zufall hereinholen und die Takte
#: unreproduzierbar machen.
VERBOTEN = ("homeassistant", "voluptuous", "aiohttp")


def _module() -> list[Path]:
    return sorted(CONTROL.glob("*.py"))


def test_es_gibt_ueberhaupt_module_zu_pruefen() -> None:
    # Sonst wuerde der Test unten stillschweigend nichts pruefen
    assert len(_module()) >= 5


def test_der_regelkern_importiert_kein_home_assistant() -> None:
    treffer: list[str] = []
    for pfad in _module():
        baum = ast.parse(pfad.read_text(encoding="utf-8"), filename=str(pfad))
        for knoten in ast.walk(baum):
            namen: list[str] = []
            if isinstance(knoten, ast.Import):
                namen = [a.name for a in knoten.names]
            elif isinstance(knoten, ast.ImportFrom) and knoten.module:
                namen = [knoten.module]
            for name in namen:
                wurzel = name.split(".")[0]
                if wurzel in VERBOTEN:
                    treffer.append(f"{pfad.name}:{knoten.lineno} -> {name}")
    assert not treffer, "Regelkern importiert Fremdpakete: " + ", ".join(treffer)


def test_der_regelkern_nimmt_die_zeit_nicht_selbst() -> None:
    """``now`` kommt als Parameter herein - sonst sind Takte nicht wiederholbar."""
    treffer: list[str] = []
    for pfad in _module():
        baum = ast.parse(pfad.read_text(encoding="utf-8"), filename=str(pfad))
        for knoten in ast.walk(baum):
            if isinstance(knoten, ast.Import):
                for a in knoten.names:
                    if a.name.split(".")[0] in ("time", "datetime", "random"):
                        treffer.append(f"{pfad.name}:{knoten.lineno} -> {a.name}")
            elif isinstance(knoten, ast.ImportFrom) and knoten.module:
                if knoten.module.split(".")[0] in ("time", "datetime", "random"):
                    treffer.append(f"{pfad.name}:{knoten.lineno} -> {knoten.module}")
    assert not treffer, "Regelkern holt sich Zeit oder Zufall: " + ", ".join(treffer)
