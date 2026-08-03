# Auto-Bewertung 🚗

Ein Profi-Tool zur Bewertung von **Gebrauchtwagen (DE)** nach *deinen* Kriterien:
Total Cost of Ownership, Zuverlässigkeit, Wertstabilität, Ausstattung, echter
Verschleiß je Untermodell, Rückrufe, echte Angebote von AutoScout24, Kauf-Check
(Tacho-Betrug), VIN-Decoder – mit sortierbarem Dashboard und automatischem
Preis-Tracking.

## Schnellstart

```bash
./start.sh            # einrichten + Daten laden + Dashboard öffnen (localhost:8501)
./start.sh --rank     # nur Ranking in der Konsole
```

Die Sidebar (links, per Chevron aufklappen) enthält alle Kriterien, Gewichte,
TCO-Annahmen, Parkplatz-Breite und den VIN-Decoder.

## Was das Tool bewertet

**Gewichteter Gesamtscore** aus 8 Dimensionen (je 0–100, relativ zum Feld):
TCO/Jahr · Wertstabilität · Ausstattung · Zuverlässigkeit · Schwachstellen ·
Preis/Deal · Ersatzteile · Werkstätten. Gewichte frei einstellbar.

**Harte Kriterien:** Budget (mit E-Auto-Ausnahme bei Betriebskosten-Ersparnis),
Klasse ab Kompakt, E-Auto ≥300 km/30 min **oder** ≥400 km Reichweite & ≥180/30 min
(Langstrecken-Ausnahme). Nicht-qualifizierte Modelle werden mit Begründung ausgewiesen.

### Kernfunktionen (Dashboard)
- **Sortierbare Tabelle** – Modell anklicken → Detail; nach Score / **Gesamtkosten 5 J / 10 J** / Kaufpreis / Wertstabilität sortieren. Spalten: Baujahr, Preis (echt), L×B, Gesamt 5/10 J, Wertverlust/J, 🚨 Rückrufe. Marker: ⚡🔋⛽ Antrieb, 📉 hoher Wertverlust, 📏 zu breit für deinen Platz.
- **💰 Angebote** – echte AutoScout24-Angebote (Auto-Load je Modell), jedes als **Klick-Button direkt zum Inserat**, mit AS24-Preisbewertung (🟢 Sehr gut … 🔴 Hoch), Version, kW; + pkw.de-Preistrend eingebettet, Preisverlauf.
- **💶 TCO** – volle Kostenaufschlüsselung/Jahr inkl. Lade-Mix (Firma/Solar/Heim/Schnelllader).
- **🔩 Verschleiß** – welches Teil bei wie viel km + Kosten, **je Untermodell/Motor** (Zahnriemen vs Kette, Tesla-Querlenker …), Kostenkurve über die Laufleistung, einstellbares km-Fenster.
- **🕵️ Kauf-Check** – km-Plausibilität (Rückdreh-Warnung), fällige/anstehende Teile, **Nähe-Warnung** („in ~X km Reparatur Y"), Rückrufe, Tacho-Betrug-/Unfall-Checkliste, carVertical/AutoDNA-Links.
- **📉 Wertstabilität** – Wertverlust %/Jahr + pkw.de-Restwerte.
- **⭐ Ausstattung** – Wunsch-Assistenz (Einparkhilfe, Kamera, Notbrems-, Spurhalteassistent), Matrix-LED-Warnung, **Parken & Dellen** (L×B, Wendekreis, „passt in deinen Parkplatz?", Dellen-Reparaturkosten, Alu-Karosserie-Warnung).
- **📊 Zuverlässigkeit** – echte TÜV-Mängel + ADAC-Pannen (🟢 echt / 🟡 Schätzung, mit Quelle).
- **⚖️ Vergleich** – 2–4 Modelle direkt nebeneinander (alle Kennzahlen).
- **🔔 Schnäppchen-Alarm** – neue Top-Preis-Angebote + Preissenkungen.
- **VIN-Decoder** (NHTSA, gratis) – erkennt Modell + Untermodell aus der FIN.

## Echte Datenquellen (vs. Schätzung)

Das Tool trennt sauber **echte, quellenbelegte Daten** von markierten Schätzungen:
- **AutoScout24** – echte Angebote (Preis/km/EZ/kW/Bewertung), gefiltert nach
  Kraftstoff, Baujahr-Generation, Akku-Variante (Reichweite) und Unfall-Flag.
  `data/` – kein Login, nur kanonische `/lst/marke/modell`-Seiten. mobile.de blockt (403).
- **TÜV-Report / ADAC-Pannenstatistik** – `data/reliability_real.csv` (quellenbelegt).
- **KBA-/Hersteller-Rückrufe** – `data/recalls_real.csv` (+ NHTSA-API optional).
- **Verschleiß** – `data/wear_real.csv` (recherchierte Defekt-km + Kosten je Untermodell).

Alle nicht belegten Werte sind im Dashboard klar als 🟡 Schätzung markiert.

## Automatisches Preis-Tracking (Cron)

```bash
python -m autobewertung.collect track --top 20
```
Holt echte AS24-Preise, aktualisiert verfolgte Angebote, schreibt Modell-Preis-
Snapshots und erkennt Schnäppchen (`alerts.log`). Als Cron (z. B. alle 6 h):
```
0 */6 * * * cd /pfad/auto-bewertung && .venv/bin/python -m autobewertung.collect track --top 20 >> track.log 2>&1
```

Einzelnes Inserat verfolgen: im Dashboard unter Angebote „verfolgen", oder
`python -m autobewertung.collect watch "<url>"`.

## Auto-Discovery neuer Modelle

```bash
python -m autobewertung.collect discover --dry-run   # nur anzeigen, was fehlt
python -m autobewertung.collect discover --min 3     # ab 3 Angeboten anlegen
```
Scannt die kanonischen AS24-Marken-Seiten, gruppiert die Angebote nach Modell und
legt noch nicht erfasste Modelle automatisch an (Antrieb aus Kraftstoff, typ. Preis
aus Median, Baujahr-Spanne + Reichweite aus den Angeboten). Neue Modelle sind über
`generation='auto-entdeckt'` markiert und können später via Seed/CSV mit Echtdaten
(Verbrauch, Klasse, Zuverlässigkeit) vertieft werden.

## Fair-Preis-Modell (Deal-Detektor)

```bash
python -m autobewertung.collect deals --top 15   # Angebote unter fairem Preis
```
Statt sich auf das AS24-eigene Preis-Label zu verlassen, schätzt eine globale
log-lineare Regression aus **deinen echten Angeboten** für jedes Inserat einen
fairen Marktpreis:

    log(Preis) = Basis[Modell] + b·Alter + b·log(km) + b·kW

Modell-Fixed-Effects fangen Marke/Segment/Ausstattung ab, die Alters-/km-/kW-
Steigungen sind über alle Modelle gepoolt (robust bei wenig Daten), 2-Pass gegen
Ausreißer. Pro Angebot fällt ein **Residual in Euro** an („2.300 € unter fair").
Das speist die `price_value`-Dimension und den 💸-Alarm „unter Marktwert".

Wichtig: **extreme** Unterbewertung (> 35 % unter fair) ist meist ein Problemauto
(Unfall/Reparaturstau) und wird bewusst **nicht** als Deal gewertet. Ohne `numpy`
fällt das Signal automatisch auf den Median-Rabatt zurück.

## Kriterien anpassen

`data/criteria.yaml` – Gewichte, Budget, Klasse, EV-Regeln, TCO-Annahmen
(inkl. Lade-Mix, z. B. 95 % Firma gratis), Wunsch-Ausstattung. Alles auch live
im Dashboard einstellbar.

## Architektur

```
autobewertung/
  db.py            SQLite-Schema (Modelle, Specs, Angebote+Preisverlauf, Verschleiss,
                   Reliability, Rueckrufe, Alarme, Watchlist, Snapshots)
  config.py        Kriterien/Gewichte/Filter
  tco.py           Total Cost of Ownership + Lade-Mix + Fahrzeugklassen
  wear.py          Verschleiss-km-Kurve + erwartete Reparaturkosten
  checks.py        Kauf-Check (Tacho-Plausibilitaet, faellige Teile, Checkliste)
  vin.py           NHTSA-VIN-Decoder + Modell-/Varianten-Zuordnung
  alerts.py        Schnaeppchen-Alarm (neue Top-Preise, Preissenkungen)
  scoring.py       Aggregation + Filter -> 8 Dimensionen -> Gesamtscore
  tracking.py      Modell-Preis-Snapshots + Zuordnungs-Verifikation
  collect.py       CLI: init / run / track / watch / rank / assignments
  dashboard.py     Streamlit-Dashboard
  sources/         seed, autoscout24 (live), reliability_import, wear_import,
                   recalls, watchlist, inserate (CSV)
data/              criteria.yaml + reliability_real / wear_real / recalls_real .csv
```

## Tests

```bash
for t in scoring wear checks recalls reliability vin watchlist alerts autoscout24; do
    python tests/test_$t.py; done
# oder: pytest tests/
```

## Ehrliche Grenzen

- **Slug-Varianten**: Portal-Slugs mischen Generationen/Akkugrößen – gefiltert nach
  Baujahr/Reichweite/Kraftstoff, aber Grenzfälle möglich; die angezeigte Version + AS24-Bewertung helfen beim Gegencheck.
- **Wenige Treffer**: Bei Modellen mit 1–2 Angeboten kann ein Ausreißer den Preis verzerren (Filter greift ab mehreren Angeboten).
- **Rückrufe** greifen FIN-spezifisch – ob *dieses* Auto betroffen/erledigt ist, klärt nur der FIN-Check.
- **Verschleiß-km/Kosten** sind fundierte Größenordnungen (±30 %), keine Einzelfall-Kostenvoranschläge.
- **AutoScout24-Abrufe** sind für persönliche Recherche gedacht (robots-konform, höflich); Massen-Crawling ist nicht das Ziel.
