# PV-Überschussladen für Home Assistant

Lädt dein Auto mit dem Solarstrom, der sonst ins Netz ginge — **mit jeder Wallbox,
die in Home Assistant steuerbar ist**.

> ⚠️ **Frühe Version.** Der Regelkern ist gegen eine seit Wochen produktiv laufende
> Referenzimplementierung geprüft (siehe unten). Die Home-Assistant-Anbindung selbst
> ist noch nicht in einer echten Installation gelaufen. Rechne beim ersten Einrichten
> mit Kanten und melde sie bitte als Issue.

## Warum noch eine Überschussregelung?

Es gibt [evcc](https://evcc.io/), und es ist gut. Diese Integration hat einen anderen
Zuschnitt:

**Sie läuft in Home Assistant.** Kein zweiter Dienst, keine YAML-Datei, Einrichtung
über die Oberfläche. Sie steuert das, was in Home Assistant ohnehin schon da ist —
eine `number`-Entität für den Ladestrom genügt.

**Sie kommt mit abgeregelten Anlagen zurecht.** Das ist der eigentliche Grund für
dieses Projekt. Bei Nulleinspeisung oder hartem Einspeiselimit drosselt der
Wechselrichter das Dach auf den Bedarf herunter. Die gemessene Einspeisung ist dann
strukturell null — und damit auch der „Überschuss", aus dem jede klassische Regelung
ihren Ladestrom ableitet. Sie startet nie, obwohl mehrere Kilowatt verfügbar wären.

Belegt an einer realen Anlage: Bei voller Batterie und stehender Wallbox lief die PV
mit **251 W**. In der Minute, in der das Auto zu laden begann, sprang sie auf
**6874 W**. Diese Reserve steht in keinem Messwert — man sieht sie erst, wenn man
Last dazuschaltet. Genau das macht der Tast-Betrieb.

## Was sie kann

- **Überschussregelung** mit Pause-Hysterese: Reicht der Überschuss nicht mehr, wird
  drei Minuten lang mit Mindeststrom weitergeladen, bevor pausiert wird. Eine Wolke
  soll die Ladung nicht sofort abwürgen.
- **Netzbezugs-Sperre** als unabhängige zweite Schutzebene. Sie schaut nur auf den
  Zähler: Meldet er länger als 30 Sekunden echten Bezug, während geladen wird, zieht
  sie einen Deckel über den Ladestrom — notfalls bis auf null. Sie greift in **jedem**
  Modus, auch in „Manuell" und „Maximum". Ein Schutz, den man erst einschalten muss,
  schützt nicht.
- **Tast-Betrieb** für abgeregelte Anlagen (siehe oben). Er zieht sich zurück, sobald
  Netzbezug entsteht **oder** die Hausbatterie einspringt — ohne die zweite Bedingung
  würde nachts munter das Auto aus dem Hausspeicher geladen.
- **Hausbatterie-Schutz**: unterhalb eines einstellbaren Ladestands wird nicht geladen.
- **Fahrzeug-Ladestand**: Im Modus „Min + Solar" wird ein fast leeres Auto notfalls aus
  dem Netz nachgeladen — außer es hat sein Ladeziel schon erreicht.
- **Phasenumschaltung** 1-/3-phasig, wenn die Wallbox das kann. Standardmäßig **aus**,
  siehe unten.

## Betriebsarten

| Modus | Verhalten |
|---|---|
| **Aus** | Kein Eingriff. Auch die Netzsperre ruht — das ist die Zusage dieses Modus. |
| **Nur Solarstrom** | Lädt ausschließlich mit Überschuss. Kein Netzbezug. |
| **Min + Solarstrom** | Wie oben, lädt aber notfalls aus dem Netz, solange das Auto unter dem Mindest-Ladestand liegt. |
| **Manuell** | Fester Ladestrom, unabhängig vom Überschuss. |
| **Maximum** | Volle Leistung. |

## Installation

[![Über HACS hinzufügen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jensfr1&repository=ha-pv-surplus&category=integration)

In HACS unter *Benutzerdefinierte Repositories* dieses Repository als Integration
hinzufügen, installieren, Home Assistant neu starten. Danach unter
*Einstellungen → Geräte & Dienste → Integration hinzufügen* nach „PV-Überschussladen"
suchen.

## Einrichtung

Drei Schritte, alles Weitere hat brauchbare Vorgaben.

1. **Netzleistung.** Der Sensor am Netzanschluss — nicht der Wechselrichter. Wichtig
   ist das Vorzeichen: **Einspeisung muss negativ sein.** Der Dialog zeigt dir den
   gemessenen Wert im Klartext an, damit du es sofort siehst.
2. **Ladestrom.** Die `number`-Entität, mit der du den Ladestrom deiner Wallbox
   einstellst. Optional dazu ein Schalter für die Freigabe und ein Leistungssensor.
3. **Bestätigen.**

Alles andere — Solarleistung, Hausbatterie, Fahrzeugdaten, Phasenumschaltung,
Schwellwerte — liegt danach unter *Konfigurieren*.

> **Der Leistungssensor der Wallbox ist dringend empfohlen.** Ohne ihn lässt sich nicht
> erkennen, ob überhaupt geladen wird, und sowohl die Netzsperre als auch der
> Tast-Betrieb arbeiten ungenauer.

### Eine Einstellung, die man falsch setzen kann

**„Wallbox hängt hinter dem Netzzähler"** entscheidet, ob die laufende Ladeleistung zum
Überschuss zurückgerechnet wird. Richtig ist das, wenn jedes Ampere ins Auto die
gemessene Einspeisung senkt — sonst hungert sich die Regelung selbst aus.

Falsch gesetzt entsteht eine Mitkopplung: Lädt das Auto irrtümlich 4 kW aus dem Netz,
ist die Einspeisung null, der gerechnete „Überschuss" aber 4000 W — und die Regelung
bestätigt sich selbst. Die Netzsperre fängt das ab, aber erst nach einer halben Minute.
Im Zweifel ausgeschaltet lassen.

## Phasenumschaltung

Dreiphasig sind 6 A Mindeststrom bereits **4140 W**. An einem trüben Tag mit 2 kW
Überschuss steht eine dreiphasige Wallbox deshalb still, obwohl einphasig längst
geladen werden könnte — dort sind 6 A nur 1380 W.

Kann deine Wallbox umschalten, wähle in den Optionen die passende Entität und die
Zuordnung („welche Option bedeutet einphasig?"). Der Schalter *Phasenumschaltung
erlauben* ist **standardmäßig aus**, und das aus gutem Grund: Jede Umschaltung
erfordert eine Ladepause, und einzelne ältere Fahrzeuge laufen danach nur widerwillig
oder gar nicht wieder an.

Die Regelung schaltet erst hoch, wenn der Überschuss fünf Minuten lang über 4830 W
liegt, und nach jeder Umschaltung gilt eine Mindestverweildauer von 15 Minuten.
Zusätzlich begrenzt ein Budget auf sechs Umschaltungen je sechs Stunden.

## Was gemessen wird

| Entität | Bedeutung |
|---|---|
| Überschuss | Was gerade eingespeist wird (plus Ladeleistung, falls so konfiguriert) |
| Sollstrom | Was die Regelung setzen will |
| Status | Wartet, lädt, pausiert, tastet, Netzsperre aktiv, keine Messwerte |
| Phasen | Wie viele Phasen das Fahrzeug tatsächlich nutzt |
| Geladen gesamt / davon Sonne / davon Netz | kWh-Zähler, neustartfest |

Der Status trägt die Begründung als Attribut mit — damit lässt sich ohne Log-Wühlen
nachvollziehen, warum gerade 0 A anliegen.

> Die Aufteilung Sonne/Netz ist eine **Näherung**: Sie unterstellt, dass Netzbezug bei
> laufender Ladung vorrangig dem Auto zuzuschreiben ist. Für die Frage „wie viel davon
> war Sonne" reicht das; als Abrechnungsgrundlage taugt es nicht.

## Verhalten in Grenzfällen

- **Der Zähler fällt aus.** Nach zwei Minuten ohne Messwert wird pausiert. Ohne Zähler
  ist Überschussladen Raten, und Raten sollte nicht die Voreinstellung sein. Umstellbar
  auf „letztes Limit halten" oder „Mindeststrom".
- **Neustart von Home Assistant.** Betriebsart, Schwellwerte und Energiezähler kommen
  zurück. Der Deckel der Netzsperre, die getastete Obergrenze und alle Zeitstempel
  **nicht** — ein Zeitbezug über einen Neustart hinweg ist eine Lücke, keine Messung.
- **Jemand stellt den Ladestrom von Hand.** Wird erkannt. Auf Wunsch ruht die Regelung
  dann zehn Minuten, statt sofort dagegenzuhalten.
- **Home Assistant fällt aus.** Das zuletzt gesetzte Limit bleibt stehen. Es gibt keinen
  Not-Aus außerhalb von Home Assistant — plane danach.

## Woher die Regelung kommt

Der Regelkern ist die Portierung einer Steuerung, die seit Wochen an einer echten
Anlage läuft (EcoFlow PowerOcean, Wallbox über OCPP). Er ist bewusst **frei von
Home Assistant**: keine Importe, kein I/O, keine Uhr — die Zeit kommt als Parameter
herein. Nur so laufen die Tests ohne Home Assistant, und nur so lässt sich die
Regelung gegen eine Handvoll Zahlen prüfen statt gegen eine laufende Anlage. Ein Test
bewacht diese Grenze und schlägt an, sobald jemand sie einreißt.

Die Portierung ist nicht nur gegen Unit-Tests geprüft, sondern **Takt für Takt gegen
das Original**: Fünf realistische Verläufe laufen durch beide Implementierungen, die
Sollstromfolgen müssen identisch sein. 134 Takte, keine Abweichung.

```bash
python -m pytest tests/ -q      # 122 Tests, ohne Home Assistant
python tests/crosscheck_ts.py   # Vergleich gegen das Original
```

## Lizenz

MIT
