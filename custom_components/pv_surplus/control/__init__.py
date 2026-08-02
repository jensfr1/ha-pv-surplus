"""Regelkern des PV-Ueberschussladens.

Dieses Paket ist bewusst frei von Home Assistant: keine Importe aus
``homeassistant``, kein I/O, keine Uhr. Die Zeit kommt immer als Parameter
``now`` herein, in Sekunden.

Das ist keine Stilfrage. Nur so laufen die Tests ohne installiertes Home
Assistant, und nur so bleibt die Regelung gegen eine Handvoll Zahlen pruefbar,
statt gegen eine laufende Anlage. ``tests/test_no_ha_imports.py`` haelt die
Grenze durch einen Scan des Quelltextes offen.
"""
