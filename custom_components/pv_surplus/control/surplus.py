"""Ueberschussrechnung.

Was als "Ueberschuss" gilt, haengt daran, wo die Wallbox im Verhaeltnis zum
messenden Zaehler sitzt - und diese Frage ist die haeufigste Fehlerquelle einer
Ueberschussregelung ueberhaupt. Deshalb steht sie hier allein, mit Begruendung.
"""

from __future__ import annotations

import math


def compute_surplus_w(
    grid_power_w: float | None,
    ev_power_w: float | None = None,
    includes_ev: bool = False,
    reserve_w: float = 0.0,
) -> float | None:
    """Verfuegbarer Ueberschuss in W, oder ``None`` ohne Netzmesswert.

    ``includes_ev`` rechnet die laufende Ladeleistung hinzu. Das ist richtig,
    wenn die Wallbox HINTER dem messenden Zaehler haengt: Dann senkt jedes
    Ampere ins Auto die gemessene Einspeisung, und ohne Rueckrechnung wuerde
    sich die Regelung selbst aushungern.

    Es ist aber falsch - und gefaehrlich -, wenn die Wallbox nicht mitgemessen
    wird. Dann entsteht eine Mitkopplung: Laedt das Auto irrtuemlich aus dem
    Netz, ist die Einspeisung null, der gerechnete Ueberschuss aber so hoch wie
    die Ladeleistung, und der Regler bestaetigt sich selbst. Die Netzsperre
    faengt das ab, aber erst nach einer halben Minute.

    ``reserve_w`` laesst dem Haus einen Puffer, bevor ueberhaupt geladen wird.
    """
    if grid_power_w is None:
        return None
    # Nur Einspeisung zaehlt; Bezug ergibt keinen negativen Ueberschuss.
    feed_in = max(0.0, -grid_power_w)
    ev_back = (ev_power_w or 0.0) if includes_ev else 0.0
    return max(0.0, feed_in + ev_back - reserve_w)


def current_from_power(watts: float, phases: int, voltage_v: float = 230.0) -> int:
    """Rechnet Leistung in einen ganzzahligen Ladestrom um.

    Abgerundet, nie aufgerundet: Ein halbes Ampere zu viel bedeutet Netzbezug,
    ein halbes Ampere zu wenig nur etwas verschenkten Ertrag.
    """
    if phases <= 0 or voltage_v <= 0:
        return 0
    return max(0, math.floor(watts / (voltage_v * phases)))
