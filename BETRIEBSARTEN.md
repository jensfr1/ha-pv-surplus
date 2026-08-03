# Betriebsarten und Schutzmechanismen

Was die Regelung in welcher Einstellung tut — und warum sie es tut.

Alle Zahlen in diesem Dokument stehen so im Quelltext. Wo sie einstellbar sind,
ist es vermerkt.

## Die kurze Antwort

| Betriebsart | Lädt womit | Kann Netzstrom ziehen? |
|---|---|---|
| **Aus** | gar nicht | nein — es wird nichts gestellt |
| **Nur Solarstrom** | nur Überschuss | **nein** |
| **Min + Solarstrom** | Überschuss, notfalls Netz | ja, aber nur mit Mindeststrom und nur bei fast leerem Auto |
| **Manuell** | fester Strom | ja |
| **Maximum** | volles Limit | ja |

Wenn du dich nicht entscheiden kannst: **Nur Solarstrom**. Das ist die
Einstellung, für die es diese Integration gibt.

---

## Aus

Die Regelung hält sich vollständig heraus. Sie sendet keine Befehle, und — das
ist der wichtige Teil — **auch die Netzbezugs-Sperre ruht**.

Das ist die Zusage dieses Modus: Wer ihn wählt, will die Wallbox selbst
steuern, über deren App oder eine eigene Automation. Eine Regelung, die
trotzdem heimlich mitredet, wäre unbrauchbar.

> Der zuletzt gesetzte Ladestrom bleibt an der Wallbox stehen. „Aus" heißt
> „ich rühre nichts mehr an", nicht „ich stelle alles zurück".

## Nur Solarstrom

Lädt ausschließlich mit dem Strom, der sonst ins Netz ginge.

Der Sollstrom ergibt sich aus der gemessenen Einspeisung:

```
Ampere = abgerundet( Watt / (230 V × Phasen) )
```

Abgerundet, nie aufgerundet. Ein halbes Ampere zu viel bedeutet Netzbezug, ein
halbes Ampere zu wenig nur etwas verschenkten Ertrag.

**Die Pause-Hysterese.** Reicht der Überschuss nicht mehr für den Mindeststrom,
wird nicht sofort abgeschaltet. Drei Minuten lang läuft die Ladung mit
Mindeststrom weiter — eine Wolke soll sie nicht abwürgen. Erst danach geht der
Sollstrom auf 0.

Steht die Wallbox ohnehin still, wird sofort 0 gesetzt. Es gibt bewusst **keine
Verzögerung nach oben**: Sobald der Überschuss für den Mindeststrom reicht,
beginnt die Ladung im nächsten Takt.

## Min + Solarstrom

Wie „Nur Solarstrom", mit einer Ausnahme: Liegt der Ladestand des Autos unter
dem eingestellten Mindestwert, wird mit dem Mindeststrom weitergeladen — auch
wenn dafür Strom aus dem Netz kommen muss.

Gedacht für den Fall, dass du am nächsten Morgen losfahren musst und das Auto
fast leer ist. Sobald der Mindestladestand erreicht ist, verhält sich der Modus
wieder wie „Nur Solarstrom".

**Voraussetzung:** ein Ladestands-Sensor des Fahrzeugs in den Optionen. Ohne ihn
verhält sich dieser Modus exakt wie „Nur Solarstrom" — er kann ja nicht wissen,
wann er eingreifen soll.

Ist zusätzlich ein Ziel-Ladestand konfiguriert und bereits erreicht, wird
**nicht** nachgeladen. Das Auto hat dann schon, was es wollte.

## Manuell

Ein fester Ladestrom, unabhängig vom Überschuss. Der Wert steht im Regler
*Manueller Ladestrom*.

Die Netzbezugs-Sperre greift trotzdem — dazu unten mehr.

## Maximum

Volle Freigabe bis zum eingestellten Höchststrom. Auch hier greift die Sperre.

---

# Die Schutzmechanismen

Diese laufen unabhängig von der Betriebsart mit (außer in „Aus").

## Netzbezugs-Sperre

Die zweite, unabhängige Sicherheitsebene. Sie schaut **nur** auf den Zähler und
kennt die Überschussrechnung nicht.

Die Überschussregelung arbeitet vorausschauend: Sie gibt frei, was gerade
eingespeist wird. Das setzt voraus, dass die Rechnung stimmt. Eine träge
Messung, ein Verbraucher der plötzlich anspringt, ein vertauschtes Vorzeichen —
und es fließt doch Strom aus dem Netz ins Auto.

**Wie sie arbeitet:**

| | Wert |
|---|---|
| Ab wann Bezug als echt gilt | 50 W |
| Wie lange Bezug anliegen darf | 30 Sekunden |
| Freigabe nach Entspannung | +1 A alle 2 Minuten |

Hält der Bezug an, senkt sie den Deckel um so viel Ampere, wie dem Bezug
entspricht. Fällt das Ergebnis unter den Mindeststrom, wird auf 0 gesetzt —
dazwischen gibt es nichts, was eine Wallbox fahren könnte.

**Sie greift in jeder Betriebsart**, auch in „Manuell" und „Maximum". Ein
Schutz, den man erst einschalten muss, schützt nicht.

Kurze Spitzen lösen nichts aus: Der Backofen springt an, die Hausbatterie fängt
es ab, nach zwanzig Sekunden ist es vorbei. Erst wenn der Bezug **anhält**,
liegt es plausibel am Auto.

## Tast-Betrieb

Der eigentliche Grund, warum es diese Integration gibt. Standardmäßig **aus** —
er ist nur für abgeregelte Anlagen sinnvoll.

**Das Problem.** Bei Nulleinspeisung oder einem harten Einspeiselimit darf
nichts ins Netz. Der Wechselrichter drosselt das Dach deshalb auf genau den
Bedarf herunter. Die gemessene Einspeisung ist damit strukturell null — und mit
ihr der „Überschuss", aus dem jede Regelung ihren Ladestrom ableitet. Sie kann
aus dem Stand nie starten, obwohl mehrere Kilowatt bereitstünden.

**Der Beleg**, gemessen an einer realen Anlage: Bei voller Batterie und
stehender Wallbox lief die PV mit **251 W**. In der Minute, in der das Auto zu
laden begann, sprang sie auf **6874 W**. Diese Reserve steht in keinem Messwert.
Man sieht sie erst, wenn man Last dazuschaltet.

**Was der Tast-Betrieb macht.** Er gibt vorsichtig mehr frei, als die Rechnung
hergibt, und beobachtet, ob die Anlage mitzieht:

| | Wert |
|---|---|
| Erster Schritt | direkt auf den Mindeststrom |
| Weitere Schritte | +1 A alle 90 Sekunden |
| Mindest-PV-Leistung | 500 W — nachts gibt es nichts zu holen |
| Gefundene Grenze gilt | 10 Minuten |

**Zwei Abbruchsignale, beide gleichwertig:**

1. Der Zähler meldet Netzbezug (ab 50 W).
2. **Die Hausbatterie fängt an zu entladen** (ab 50 W).

Das zweite ist genauso wichtig wie das erste. Ohne diese Bedingung würde nachts
munter weitergetastet und das Auto aus dem Hausspeicher geladen — technisch
kein Netzbezug, wirtschaftlich Unsinn.

Bei einem Rückzug merkt sich die Regelung, wo die Grenze lag, und bleibt zehn
Minuten darunter. Danach probiert sie erneut: Die Sonne steht in zehn Minuten
anders, und eine einmal gefundene Grenze soll nicht den Rest des Tages
blockieren.

**Was er nicht tut:** Kommt der Netzbezug erkennbar von woanders — das Limit
liegt auf Höhe der Überschussrechnung, es wurde also gar nicht getastet — hält
er sich heraus. Dann ist es der Backofen, nicht seine Sache.

> **Wann brauchst du ihn?** Wenn dein Wechselrichter auf Nulleinspeisung oder
> ein festes Limit geregelt ist. Speist deine Anlage frei ein, lass ihn aus —
> dann ist die gemessene Einspeisung ehrlich und der Tast-Betrieb bringt nur
> Unruhe.

## Hausbatterie-Schutz

Unterhalb des eingestellten Ladestands wird gar nicht geladen. Damit das Auto
den Hausspeicher nicht leersaugt.

Steht der Wert auf 0, ist der Schutz aus. Der Tast-Betrieb hat seine eigene
Bremse über die Entladeleistung — dieser Regler ist die verständlichere,
explizite Variante.

## Verhalten bei Ausfall des Zählers

Liefert die Netzleistung länger als zwei Minuten keinen Wert, wird die Ladung
**pausiert**. Ohne Zähler ist Überschussladen Raten, und Raten sollte nicht die
Voreinstellung sein.

Umstellbar in den Optionen unter *Fortgeschritten*: „Letztes Limit halten" oder
„Auf Mindeststrom gehen".

## Phasenumschaltung

Standardmäßig **aus**, und nur nutzbar, wenn die Wallbox umschalten kann.

**Das Problem in Zahlen:** Dreiphasig sind 6 A Mindeststrom bereits **4140 W**.
An einem trüben Tag mit 2 kW Überschuss steht eine dreiphasige Wallbox deshalb
still, obwohl einphasig längst geladen werden könnte — dort sind 6 A nur 1380 W.

| | Wert |
|---|---|
| Hoch auf dreiphasig | ab 4830 W, muss 5 Minuten anliegen |
| Runter auf einphasig | unter 3910 W, muss 2 Minuten anliegen |
| Mindestverweildauer danach | 15 Minuten |
| Obergrenze | 6 Umschaltungen je 6 Stunden |

Die Zeiten sind bewusst **asymmetrisch**. Hochschalten ist der teure Fehler:
Ladepause, und manche Fahrzeuge laufen danach nur widerwillig wieder an.
Runterschalten ist der billige, umkehrbare Fehler — und zu lange dreiphasig zu
verharren kostet sofort, weil unterhalb von 4140 W dort gar nicht geregelt
werden kann.

**Geschaltet wird nie unter Last.** Der Ablauf: Ladung stoppen, warten bis kein
Strom mehr fließt, Phasen umschalten, Rückmeldung abwarten, Ladung wieder
freigeben. Läuft das Auto danach nicht von selbst an, wartet die Regelung zehn
Minuten und gibt dann auf, statt eine zweite Unterbrechung zu erzwingen.

Steht das Auto ohnehin (eingesteckt, aber nicht ladend), entfällt die Sequenz —
dann kostet die Umschaltung nichts.

**Stellung ist nicht gleich genutzte Phasen.** Ein zweiphasig ladendes Fahrzeug
an einer dreiphasig gestellten Box nutzt zwei. Die Regelung erkennt das aus den
Phasenströmen und rechnet mit der tatsächlichen Zahl — sonst bekäme das Auto
dauerhaft ein Drittel zu wenig. Lädt ein Fahrzeug nur einphasig, wird die
Umschaltung ganz eingestellt: Sie brächte nichts.

---

## Was zusammenspielt

Die Reihenfolge in jedem Regeltakt (alle 15 Sekunden):

1. **Aus?** Dann nichts weiter — auch keine Sperre.
2. **Phasen** planen. Läuft eine Umschaltung, hält sie alles andere an.
3. **Netzbezugs-Sperre** fortschreiben.
4. **Feste Modi** liefern ihren Wunschstrom.
5. **Überschussmodi** rechnen, tasten, prüfen Fahrzeug- und Hausbatterie-Stand,
   entscheiden über die Pause.
6. **Der Deckel der Sperre steht über allem** — auch über dem Tast-Betrieb.

Punkt 6 ist wichtig: Der Tast-Betrieb darf mehr freigeben, als die Rechnung
hergibt, aber nie mehr, als die Sperre erlaubt. Beide Ebenen arbeiten
unabhängig, und die Sperre gewinnt.

## Warum ein fester Takt statt Ereignissteuerung

Alle Hysteresen — 30 Sekunden, 90 Sekunden, 2 Minuten, 3 Minuten — haben nur
dann eine verlässliche Bedeutung, wenn der Takt nicht von der Sprechfreudigkeit
eines Zählers abhängt. Ein Zähler, der alle zwei Sekunden meldet, würde sonst
über hunderttausend Regelvorgänge am Tag auslösen.

Deshalb werden die Messwerte laufend mitgelesen, aber geregelt wird im festen
Takt.
