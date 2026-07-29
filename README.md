# Auto-Bewertung 🚗

Automatisches Bewertungs- und Ranking-Tool für **Gebrauchtwagen (DE)**. Du gibst
deine Kriterien vor, das Tool sammelt Daten aus mehreren Quellen, speichert sie
in einer SQLite-Datenbank und liefert ein sortierbares Ranking – als CLI-Tabelle
oder interaktives Dashboard.

Bewertet werden sechs Dimensionen (jeweils 0–100, relativ zum Datenbestand):

| Dimension | Bedeutung | Quelle(n) |
|---|---|---|
| **Preis/Deal** | Preis unter Modell-Median + fallender Preistrend → Schnäppchen | Inserate + Preisverlauf |
| **Zuverlässigkeit** | Pannen-/Mängelquote (wenig = gut) | ADAC-Pannenstatistik, TÜV-Report |
| **Schwachstellen** | bekannte Modellprobleme + Rückrufe | Foren/Werkstatt, KBA |
| **Unterhalt** | Reparatur-/Wartungs-/Versicherungskosten | Kostenschätzungen |
| **Ersatzteile** | Verfügbarkeit + Preisindex | Teile-Marktplätze |
| **Werkstätten** | Werkstattdichte/Spezialisten in deiner Nähe | Verzeichnisse (PLZ) |

Der Gesamtscore ist die **gewichtete Summe** – die Gewichte bestimmst du.

## Schnellstart

```bash
pip install -r requirements.txt        # (Dashboard/Adapter; Kern läuft auch ohne)

python -m autobewertung.collect run    # DB anlegen + Quellen einsammeln (Seed-Daten)
python -m autobewertung.collect rank   # Ranking in der Konsole

streamlit run autobewertung/dashboard.py   # interaktives Dashboard
```

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
  config.py        Kriterien/Gewichte (aus data/criteria.yaml)
  scoring.py       Aggregation der Rohdaten → 6 Dimensionen → Gesamtscore
  collect.py       CLI: init / run / rank
  dashboard.py     Streamlit-Dashboard (Tabelle, Filter, Detail, Preischart)
  sources/
    base.py        Adapter-Interface (robots-Check, höfliches Rate-Limit)
    seed.py        Beispieldaten (sofort lauffähig)
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
