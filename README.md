# Auto-Bewertung 🚗

Automatisches Bewertungs- und Ranking-Tool für **Gebrauchtwagen (DE)**. Du gibst
deine Kriterien vor, das Tool sammelt Daten aus mehreren Quellen, speichert sie
in einer SQLite-Datenbank und liefert ein sortierbares Ranking – als CLI-Tabelle
oder interaktives Dashboard.

Bewertet werden sechs Dimensionen (jeweils 0–100, relativ zum Datenbestand):

| Dimension | Bedeutung | Quelle(n) |
|---|---|---|
| **TCO/Jahr** | komplette Haltekosten pro Jahr (Wertverlust + Energie + Versicherung + Steuer + Wartung + Sonstiges) | Fahrzeug-Specs + Kostenschätzungen |
| **Preis/Deal** | Preis unter Modell-Median + fallender Preistrend → Schnäppchen | Inserate + Preisverlauf |
| **Zuverlässigkeit** | Pannen-/Mängelquote (wenig = gut) | ADAC-Pannenstatistik, TÜV-Report |
| **Schwachstellen** | bekannte Modellprobleme + Rückrufe | Foren/Werkstatt, KBA |
| **Ersatzteile** | Verfügbarkeit + Preisindex | Teile-Marktplätze |
| **Werkstätten** | Werkstattdichte/Spezialisten in deiner Nähe | Verzeichnisse (PLZ) |

Der Gesamtscore ist die **gewichtete Summe** – die Gewichte bestimmst du.

### Total Cost of Ownership (TCO)

Für jedes Modell werden die **kompletten jährlichen Haltekosten** über die
Haltedauer berechnet:

```
Wertverlust + Energie (Sprit/Strom) + Versicherung + Kfz-Steuer
+ Wartung/Reparatur + Sonstiges (Reifen, HU, Kleinkram)
```

Alle Annahmen (km/Jahr, Haltedauer, Sprit-/Strompreise) stehen in
`data/criteria.yaml` und sind im Dashboard live einstellbar.

**Lade-Mix beim E-Auto:** die Stromkosten mischen sich aus mehreren Quellen mit
eigenen Preisen und Anteilen – z. B. *95 % kostenlos in der Firma*, etwas
Solarstrom, Rest zuhause/Schnelllader. Das senkt die EV-Energiekosten drastisch
(Beispiel: Tesla Model 3 fällt damit auf den besten TCO-Wert im Feld).

### Angebote verfolgen (Preisverlauf)

Einzelne Inserate lassen sich per URL beobachten – bei jedem Lauf wird der Preis
mitgeschrieben, so entsteht ein Preisverlauf je Angebot:

```bash
python -m autobewertung.collect watch "https://www.autoscout24.de/angebote/..."
python -m autobewertung.collect run     # holt Preise der beobachteten URLs
```

Oder direkt im Dashboard unter **Angebote/Portale → „Angebot verfolgen"**. Der
Preis wird robots-konform aus schema.org-Daten der Seite gelesen; ein Preispunkt
entsteht nur bei Änderung.

### Harte Kriterien & E-Auto-Ausnahme

- **Budget** `max_price` (Standard 15.000 €) gilt für Verbrenner.
- **Klasse** ab `min_vehicle_class` (Standard `kompakt` = Golf/Auris) aufwärts.
- **E-Auto-Ausnahme:** ein E-Auto darf das Budget überschreiten, **soweit seine
  jährliche Ersparnis bei den laufenden Kosten** (vs. Verbrenner-Median) den
  Aufpreis über die Haltedauer deckt. Beispiel aus den Seed-Daten: der Tesla
  Model 3 (19.900 €) qualifiziert sich, weil er ~1.150 €/Jahr spart.
- **E-Auto-Schnelllade-Pflicht:** `ev_min_charge_km_30min` (Standard 300 km in
  30 min) – langsam ladende EVs fallen raus.

Nicht qualifizierte Modelle werden mit Begründung separat ausgewiesen (CLI:
Abschnitt „Ausgeschlossen"; Dashboard: aufklappbarer Bereich).

## Schnellstart

Ein Skript richtet alles ein (venv, Abhängigkeiten, Datenbank, Datensammlung)
und startet das Dashboard:

```bash
./start.sh                 # einrichten + Daten sammeln + Dashboard öffnen
./start.sh --no-dashboard  # nur einrichten + Ranking in der Konsole
./start.sh --rank          # nur Ranking neu ausgeben
./start.sh --inserate-csv meine_liste.csv   # zusätzlich eigene Angebote importieren
```

Das Skript ist idempotent (venv/Installation nur beim ersten Lauf) und öffnet das
Dashboard auf <http://localhost:8501>.

<details><summary>Manuell (ohne Skript)</summary>

```bash
pip install -r requirements.txt
python -m autobewertung.collect run
python -m autobewertung.collect rank
streamlit run autobewertung/dashboard.py
```
</details>

Beim ersten `run` werden **Beispieldaten** (6 populäre DE-Gebrauchtwagen) geladen,
damit sofort etwas Sinnvolles im Dashboard steht. Diese werden von echten
Adaptern überschrieben, sobald du sie scharfschaltest.

## Deine Kriterien

`data/criteria.yaml` anpassen (Gewichte + harte Filter wie `max_price`,
`max_mileage_km`, `home_plz`). Im Dashboard lassen sich die Gewichte per
Schieberegler live verändern.

## Architektur

```
autobewertung/
  db.py            SQLite-Schema (Modelle, Angebote, Preisverlauf, Pannen,
                   Schwachstellen, Rückrufe, Kosten, Ersatzteile, Werkstätten)
  config.py        Kriterien/Gewichte/Filter (aus data/criteria.yaml)
  tco.py           Total-Cost-of-Ownership-Berechnung + Fahrzeugklassen
  scoring.py       Aggregation + TCO + Filter → 6 Dimensionen → Gesamtscore
  collect.py       CLI: init / run / rank
  dashboard.py     Streamlit-Dashboard mit Drill-down: Zeile=Auto + Spalte=
                   Kategorie anklicken -> Liste -> Eintrag aufklappen (Details)
  sources/
    base.py        Adapter-Interface (robots-Check, höfliches Rate-Limit)
    seed.py        Beispieldaten (sofort lauffähig)
    watchlist.py   Einzel-Angebote per URL verfolgen (JSON-LD-Parser, Preisverlauf)
    inserate.py    mobile.de / AutoScout24 – Gerüst (+ CSV-Import)
    kba_recalls.py KBA-Rückrufe – Gerüst
```

Jede Datenquelle ist ein **Adapter**, der normalisiert in die DB schreibt. Neue
Quellen einfach in `sources/__init__.py::default_sources()` registrieren.

## Datenbeschaffung: legal/pragmatisch zuerst

Das Tool startet mit offiziellen/offenen Wegen und respektiert `robots.txt` sowie
höfliche Rate-Limits (`sources/base.py`). Wichtig:

- **Inserate (mobile.de/AutoScout24):** automatisiertes Massen-Scraping verstößt
  gegen deren AGB und wird per Bot-Schutz unterbunden. Der `inserate`-Adapter ist
  daher standardmäßig **aus** und bietet stattdessen:
  - **CSV-Import** (eigene Merklisten/Exporte):
    `python -m autobewertung.collect run --inserate-csv meine_liste.csv`
    (Spalten: `make,model,generation,source,source_ref,title,price,mileage_km,first_reg,plz,location,url`)
  - Platzhalter für **offizielle Partner-/Händler-APIs** (`fetch_via_api`).
  - Wiederholte Läufe schreiben automatisch den **Preisverlauf** fort → daraus
    entsteht die Schnäppchen-Erkennung.
- **KBA-Rückrufe / ADAC / TÜV:** öffentlich verfügbar; die Adapter laden die
  Seiten robots-konform, der modellspezifische Parser ist als TODO markiert.

## Tests

```bash
python tests/test_scoring.py       # ohne pytest lauffähig
# oder: pytest tests/
```

## Nächste Ausbaustufen

1. Inserate-CSV-Import mit echten Daten füttern → Preisverlauf/Deals werden live.
2. KBA-Rückruf-Parser fertigstellen (öffentliche Quelle).
3. ADAC-/TÜV-Kennzahlen je Modell/Baujahr pflegen (Adapter analog zu `seed`).
4. Ersatzteil-Preisindex und Werkstatt-Verzeichnis je PLZ automatisieren.
5. Benachrichtigung bei neuem Schnäppchen (Preis fällt unter Schwelle).
