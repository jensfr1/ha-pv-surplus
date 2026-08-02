"""Ueberschussrechnung und Umrechnung in Ampere."""

from __future__ import annotations

import pytest

from custom_components.pv_surplus.control.surplus import (
    compute_surplus_w,
    current_from_power,
)


class TestUeberschuss:
    def test_zaehlt_nur_die_einspeisung(self) -> None:
        assert compute_surplus_w(-2000.0) == 2000.0

    def test_ergibt_bei_netzbezug_keinen_ueberschuss(self) -> None:
        assert compute_surplus_w(1500.0) == 0.0

    def test_liefert_ohne_netzwert_nichts(self) -> None:
        assert compute_surplus_w(None) is None

    def test_rechnet_die_ladeleistung_zurueck_wenn_die_wallbox_mitgemessen_wird(
        self,
    ) -> None:
        # Wallbox hinter dem Zaehler: 1000 W Einspeisung bei 3000 W Ladung
        # bedeuten 4000 W verfuegbar
        assert compute_surplus_w(-1000.0, 3000.0, includes_ev=True) == 4000.0

    def test_rechnet_ohne_diese_verdrahtung_nicht_zurueck(self) -> None:
        assert compute_surplus_w(-1000.0, 3000.0, includes_ev=False) == 1000.0

    def test_haelt_die_reserve_frei(self) -> None:
        assert compute_surplus_w(-2000.0, reserve_w=500.0) == 1500.0

    def test_faellt_durch_die_reserve_nicht_unter_null(self) -> None:
        assert compute_surplus_w(-200.0, reserve_w=500.0) == 0.0


class TestAmpereRechnung:
    @pytest.mark.parametrize(
        ("watt", "phasen", "erwartet"),
        [
            (1380.0, 1, 6),  # genau der Mindeststrom einphasig
            (1379.0, 1, 5),  # eine Rundung tiefer, nicht hoeher
            (4140.0, 3, 6),  # dreiphasiges Minimum
            (11040.0, 3, 16),
            (0.0, 3, 0),
        ],
    )
    def test_rechnet_leistung_in_ampere(
        self, watt: float, phasen: int, erwartet: int
    ) -> None:
        assert current_from_power(watt, phasen) == erwartet

    def test_rundet_immer_ab(self) -> None:
        # Ein halbes Ampere zu viel bedeutet Netzbezug, ein halbes zu wenig nur
        # etwas verschenkten Ertrag
        assert current_from_power(1600.0, 1) == 6

    def test_kommt_mit_unsinnigen_phasenzahlen_zurecht(self) -> None:
        assert current_from_power(5000.0, 0) == 0
