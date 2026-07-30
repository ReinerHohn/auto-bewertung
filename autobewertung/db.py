"""SQLite-Datenhaltung fuer das Auto-Bewertungstool.

Ein schlankes Schema ohne ORM (Stil wie in den anderen Projekten):
- car_model         : Baureihe/Generation (Marke, Modell, Generation, Baujahre)
- listing           : konkretes Gebrauchtwagen-Angebot
- price_point       : Preisverlauf je Angebot (fuer Schnaeppchen-Erkennung)
- reliability_stat  : Pannen-/Maengelquoten je Modell (ADAC, TUEV)
- weak_point        : bekannte Schwachstellen je Modell
- recall            : Rueckrufe (KBA)
- repair_cost       : typische Reparatur-/Unterhaltskosten je Modell
- parts_availability: Ersatzteil-Verfuegbarkeit/Preisindex je Modell
- workshop          : Werkstaetten (markenspezifisch/frei) mit Standort

Alle Quellen schreiben normalisiert hier hinein; das Scoring liest nur.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "autobewertung.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS car_model (
    id          INTEGER PRIMARY KEY,
    make        TEXT NOT NULL,           -- Marke, z.B. 'VW'
    model       TEXT NOT NULL,           -- Modell, z.B. 'Golf'
    generation  TEXT,                    -- Generation/Baureihe, z.B. 'VII (2012-2019)'
    year_from   INTEGER,
    year_to     INTEGER,
    body        TEXT,                    -- Karosserie
    fuel        TEXT,                    -- Kraftstoff (grob)
    UNIQUE(make, model, generation)
);

CREATE TABLE IF NOT EXISTS listing (
    id            INTEGER PRIMARY KEY,
    model_id      INTEGER REFERENCES car_model(id) ON DELETE CASCADE,
    source        TEXT NOT NULL,         -- 'mobile.de', 'autoscout24', 'manual', ...
    source_ref    TEXT,                  -- eindeutige ID/URL im Quellportal
    title         TEXT,
    price         REAL,                  -- aktueller Preis in EUR
    mileage_km    INTEGER,
    first_reg     TEXT,                  -- Erstzulassung 'YYYY-MM'
    power_kw      INTEGER,
    location      TEXT,
    plz           TEXT,
    url           TEXT,
    price_rating  INTEGER,               -- Portal-Preisbewertung 1=sehr gut .. 5=hoch
    first_seen    TEXT NOT NULL,         -- ISO-Zeitstempel (UTC)
    last_seen     TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    UNIQUE(source, source_ref)
);

CREATE TABLE IF NOT EXISTS price_point (
    id          INTEGER PRIMARY KEY,
    listing_id  INTEGER REFERENCES listing(id) ON DELETE CASCADE,
    ts          TEXT NOT NULL,           -- ISO-Zeitstempel (UTC)
    price       REAL NOT NULL,
    UNIQUE(listing_id, ts)
);

CREATE TABLE IF NOT EXISTS reliability_stat (
    id            INTEGER PRIMARY KEY,
    model_id      INTEGER REFERENCES car_model(id) ON DELETE CASCADE,
    source        TEXT NOT NULL,         -- 'ADAC', 'TUEV'
    metric        TEXT NOT NULL,         -- 'pannen_pro_1000' | 'maengelquote_pct'
    vehicle_age   INTEGER,               -- Fahrzeugalter in Jahren (falls quellenspezifisch)
    value         REAL NOT NULL,
    year          INTEGER,               -- Berichtsjahr
    is_estimate   INTEGER DEFAULT 1,     -- 1 = Seed-Schaetzung, 0 = echte Quelle
    source_url    TEXT,                  -- Beleg-URL (bei echten Daten)
    note          TEXT,                  -- z.B. Altersklasse
    UNIQUE(model_id, source, metric, vehicle_age, year)
);

CREATE TABLE IF NOT EXISTS weak_point (
    id           INTEGER PRIMARY KEY,
    model_id     INTEGER REFERENCES car_model(id) ON DELETE CASCADE,
    component    TEXT,                   -- z.B. 'Steuerkette', 'DSG', 'Turbolader'
    description  TEXT NOT NULL,
    severity     INTEGER NOT NULL DEFAULT 2,  -- 1=gering 2=mittel 3=schwer
    cost_eur     REAL,                   -- typische Reparaturkosten dieses Defekts
    source       TEXT,
    url          TEXT
);

CREATE TABLE IF NOT EXISTS recall (
    id           INTEGER PRIMARY KEY,
    model_id     INTEGER REFERENCES car_model(id) ON DELETE CASCADE,
    kba_code     TEXT,                   -- KBA-Referenznummer
    date         TEXT,                   -- 'YYYY-MM-DD'
    description  TEXT NOT NULL,
    url          TEXT,
    UNIQUE(model_id, kba_code)
);

CREATE TABLE IF NOT EXISTS repair_cost (
    id           INTEGER PRIMARY KEY,
    model_id     INTEGER REFERENCES car_model(id) ON DELETE CASCADE,
    category     TEXT NOT NULL,          -- 'inspektion' | 'zahnriemen' | 'bremsen' | 'versicherung_tk'...
    typical_eur  REAL NOT NULL,
    period       TEXT,                   -- 'pro_jahr' | 'einmalig' | 'pro_intervall'
    source       TEXT
);

CREATE TABLE IF NOT EXISTS parts_availability (
    id            INTEGER PRIMARY KEY,
    model_id      INTEGER REFERENCES car_model(id) ON DELETE CASCADE,
    score         REAL,                  -- 0..100 (100 = beste Verfuegbarkeit)
    avg_price_idx REAL,                  -- 100 = Marktdurchschnitt, <100 guenstiger
    notes         TEXT,
    source        TEXT
);

CREATE TABLE IF NOT EXISTS workshop (
    id            INTEGER PRIMARY KEY,
    make          TEXT,                  -- markenbezogen (NULL = frei/allgemein)
    name          TEXT NOT NULL,
    plz           TEXT,
    location      TEXT,
    specialized   INTEGER DEFAULT 0,     -- 1 = Spezialist fuer die Marke
    url           TEXT,
    UNIQUE(name, plz)
);

CREATE TABLE IF NOT EXISTS vehicle_spec (
    model_id       INTEGER PRIMARY KEY REFERENCES car_model(id) ON DELETE CASCADE,
    drivetrain     TEXT,                 -- 'benzin' | 'diesel' | 'hybrid' | 'elektro'
    vehicle_class  TEXT,                 -- 'kleinwagen' | 'kompakt' | 'mittelklasse' ...
    cons_l_100km   REAL,                 -- Verbrauch l/100km (Verbrenner/Hybrid)
    cons_kwh_100km REAL,                 -- Verbrauch kWh/100km (Elektro/Hybrid)
    battery_kwh    REAL,                 -- Netto-Akkukapazitaet (Elektro)
    range_km       REAL,                 -- realistische Reichweite
    dc_charge_kw   REAL,                 -- DC-Ladeleistung (Peak)
    km_per_30min   REAL,                 -- nachladbare Reichweite in 30 min (Schnelllader)
    insurance_eur  REAL,                 -- Versicherung pro Jahr (grob)
    tax_eur        REAL,                 -- Kfz-Steuer pro Jahr (EV meist 0)
    typical_price  REAL,                 -- typischer Marktpreis (Fallback ohne Angebot)
    depr_pct_year  REAL,                 -- jaehrlicher Wertverlust-Anteil (0..1)
    features       TEXT,                 -- verfuegbare Assistenz/Komfort (kommagetrennt)
    has_matrix     INTEGER DEFAULT 0,    -- 1 = Modell oft mit (teuren) Matrix-/Voll-LED
    alu_body       INTEGER DEFAULT 0,    -- 1 = Alu-/CFK-Karosserie -> Dellen teuer in Reparatur
    length_mm      INTEGER,              -- Fahrzeuglaenge in mm
    width_mm       INTEGER               -- Breite (ohne Spiegel) in mm; Spiegel ~+40cm gesamt
);

CREATE TABLE IF NOT EXISTS wear_item (
    id           INTEGER PRIMARY KEY,
    model_id     INTEGER REFERENCES car_model(id) ON DELETE CASCADE,
    component    TEXT NOT NULL,          -- z.B. 'Bremsscheiben', 'Zahnriemen', 'Querlenker'
    variant      TEXT DEFAULT 'alle',    -- Untermodell/Motor/HW, z.B. '1.8 TSI', 'N47 Diesel', 'ab 2021'
    at_km        INTEGER NOT NULL,       -- typische Laufleistung fuer (erste) Faelligkeit
    interval_km  INTEGER DEFAULT 0,      -- Wiederholung (0 = einmalig)
    cost_eur     REAL NOT NULL,          -- typische Reparatur-/Teilekosten
    note         TEXT,
    source       TEXT
);
CREATE INDEX IF NOT EXISTS idx_wear_model ON wear_item(model_id);

CREATE TABLE IF NOT EXISTS alert (
    id         INTEGER PRIMARY KEY,
    ts         TEXT,
    model_id   INTEGER REFERENCES car_model(id) ON DELETE CASCADE,
    listing_id INTEGER,
    kind       TEXT,                  -- 'deal' | 'drop'
    message    TEXT,
    sig        TEXT UNIQUE,           -- Dedup-Signatur (kein Doppel-Alarm)
    seen       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS watch (
    id       INTEGER PRIMARY KEY,
    url      TEXT UNIQUE NOT NULL,     -- verfolgte Inserats-URL
    model_id INTEGER REFERENCES car_model(id) ON DELETE SET NULL,  -- feste Zuordnung (optional)
    note     TEXT,
    added    TEXT
);

CREATE TABLE IF NOT EXISTS model_price_snapshot (
    id           INTEGER PRIMARY KEY,
    model_id     INTEGER REFERENCES car_model(id) ON DELETE CASCADE,
    ts           TEXT NOT NULL,          -- ISO-Zeitstempel (UTC)
    median_price REAL,
    min_price    REAL,
    max_price    REAL,
    n            INTEGER
);
CREATE INDEX IF NOT EXISTS idx_snap_model ON model_price_snapshot(model_id);

CREATE INDEX IF NOT EXISTS idx_listing_model ON listing(model_id);
CREATE INDEX IF NOT EXISTS idx_price_listing ON price_point(listing_id);
CREATE INDEX IF NOT EXISTS idx_weak_model    ON weak_point(model_id);
"""


def connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    """Oeffnet die DB (legt sie inkl. Schema an, falls noetig)."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    """Erstellt das komplette Schema (idempotent)."""
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def upsert_model(conn: sqlite3.Connection, make: str, model: str,
                 generation: str | None = None, **extra) -> int:
    """Legt ein Modell an oder gibt die bestehende id zurueck."""
    cur = conn.execute(
        "SELECT id FROM car_model WHERE make=? AND model=? AND IFNULL(generation,'')=IFNULL(?, '')",
        (make, model, generation),
    )
    row = cur.fetchone()
    if row:
        return row["id"]
    cols = {"make": make, "model": model, "generation": generation, **extra}
    keys = ",".join(cols)
    ph = ",".join("?" for _ in cols)
    cur = conn.execute(f"INSERT INTO car_model ({keys}) VALUES ({ph})", tuple(cols.values()))
    conn.commit()
    return cur.lastrowid


def add_watch(conn: sqlite3.Connection, url: str, model_id: int | None = None,
              note: str | None = None) -> None:
    """Nimmt eine Inserats-URL in die Beobachtungsliste auf (idempotent).

    model_id bindet das Angebot fest an ein Modell (empfohlen, wenn bekannt) -
    dann ist keine unsichere Namens-Zuordnung noetig.
    """
    from datetime import datetime, timezone
    conn.execute(
        "INSERT INTO watch(url, model_id, note, added) VALUES (?,?,?,?) "
        "ON CONFLICT(url) DO UPDATE SET model_id=COALESCE(excluded.model_id, watch.model_id)",
        (url.strip(), model_id, note, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()


def upsert_spec(conn: sqlite3.Connection, model_id: int, **fields) -> None:
    """Legt/aktualisiert die Fahrzeug-Spezifikation (1:1 je Modell)."""
    cols = ["model_id"] + list(fields)
    ph = ",".join("?" for _ in cols)
    updates = ",".join(f"{k}=excluded.{k}" for k in fields)
    conn.execute(
        f"INSERT INTO vehicle_spec ({','.join(cols)}) VALUES ({ph}) "
        f"ON CONFLICT(model_id) DO UPDATE SET {updates}",
        (model_id, *fields.values()),
    )


if __name__ == "__main__":
    conn = init_db()
    print(f"DB initialisiert: {DEFAULT_DB}")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print("Tabellen:", ", ".join(tables))
