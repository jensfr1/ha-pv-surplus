"""Macht ``custom_components.pv_surplus.control`` ohne Home Assistant importierbar.

Das Paket ``custom_components/pv_surplus/__init__.py`` importiert Home Assistant
und laesst sich deshalb in einer nackten pytest-Umgebung nicht laden. Der
Regelkern darunter braucht Home Assistant aber gerade nicht - also werden die
beiden Elternpakete hier synthetisch angelegt und nur mit einem ``__path__``
versehen. Python findet ``control`` darueber als echtes Unterpaket, ohne dass
das eigentliche ``__init__.py`` je ausgefuehrt wird.

Uebernommen aus ha-ecoflow-ocean2, wo derselbe Trick die Decoder-Tests ohne
Home Assistant laufen laesst.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_INTEGRATION = Path(__file__).resolve().parents[1] / "custom_components" / "pv_surplus"

if "custom_components" not in sys.modules:
    _parent = types.ModuleType("custom_components")
    _parent.__path__ = [str(_INTEGRATION.parent)]
    sys.modules["custom_components"] = _parent

if "custom_components.pv_surplus" not in sys.modules:
    _package = types.ModuleType("custom_components.pv_surplus")
    _package.__path__ = [str(_INTEGRATION)]
    sys.modules["custom_components.pv_surplus"] = _package
