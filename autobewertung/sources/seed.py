"""Seed-Quelle: laedt realistische Beispieldaten, damit das Tool sofort laeuft.

Die Zahlen sind plausible Groessenordnungen fuer bekannte DE-Gebrauchtwagen
(oeffentlich bekannte Tendenzen aus ADAC-Pannenstatistik/TUEV-Report), aber als
Platzhalter zu verstehen. Sobald echte Adapter live gehen, ueberschreiben deren
Daten diese Seeds.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from .base import CollectResult, Source

# (make, model, generation, year_from, year_to, body, fuel)
MODELS = [
    ("VW", "Golf", "VII (2012-2019)", 2012, 2019, "Kompakt", "Benzin/Diesel"),
    ("Toyota", "Corolla", "E210 (2019-)", 2019, 2025, "Kompakt", "Hybrid"),
    ("BMW", "3er", "F30 (2011-2019)", 2011, 2019, "Limousine", "Benzin/Diesel"),
    ("Skoda", "Octavia", "III (2013-2020)", 2013, 2020, "Kombi", "Benzin/Diesel"),
    ("Ford", "Focus", "III (2011-2018)", 2011, 2018, "Kompakt", "Benzin/Diesel"),
    ("Mazda", "3", "BM (2013-2019)", 2013, 2019, "Kompakt", "Benzin"),
]

# Pannen pro 1000 Fahrzeuge (kleiner = besser), grobe Tendenzen
RELIABILITY = {
    "VW Golf": 8.0, "Toyota Corolla": 3.5, "BMW 3er": 9.5,
    "Skoda Octavia": 7.0, "Ford Focus": 11.0, "Mazda 3": 5.5,
}
# TUEV Maengelquote in % (erste HU, kleiner = besser)
MAENGEL = {
    "VW Golf": 6.5, "Toyota Corolla": 3.0, "BMW 3er": 5.5,
    "Skoda Octavia": 6.0, "Ford Focus": 8.5, "Mazda 3": 4.5,
}

# bekannte Schwachstellen (component, description, severity 1-3)
WEAK = {
    "VW Golf": [("DSG", "DSG-Mechatronik/Kupplung Verschleiss", 3),
                ("Steuerkette", "Steuerkettenlaengung bei fruehen 1.4 TSI", 3)],
    "Toyota Corolla": [("Hybrid", "12V-Zusatzbatterie schwach im Winter", 1)],
    "BMW 3er": [("Steuerkette", "Steuerkette N47-Diesel", 3),
                ("Kettenspanner", "Kettenspanner N20 Benziner", 2)],
    "Skoda Octavia": [("DSG", "DQ200 Trockenkupplung", 2),
                      ("Wasserpumpe", "Wasserpumpe undicht 1.8/2.0 TSI", 2)],
    "Ford Focus": [("Doppelkupplung", "Powershift-Getriebe ruckelt", 3),
                   ("Zuendspule", "Zuendspulen 1.0 EcoBoost", 2),
                   ("Kuehlung", "Kuehlmittelverlust 1.0 EcoBoost", 3)],
    "Mazda 3": [("Rost", "Radlaeufe/Unterboden Rost", 2)],
}

# Rueckrufe (kba_code, date, description)
RECALLS = {
    "Ford Focus": [("009xyz", "2018-05-01", "Kuehlmittel-Ueberhitzung 1.0 EcoBoost")],
    "BMW 3er": [("007abc", "2016-03-15", "EGR-Modul Diesel Brandgefahr")],
}

# Reparatur/Unterhalt (category, typical_eur, period)
REPAIR = {
    "VW Golf": [("inspektion", 350, "pro_jahr"), ("versicherung_tk", 480, "pro_jahr"),
                ("zahnriemen", 600, "pro_intervall")],
    "Toyota Corolla": [("inspektion", 300, "pro_jahr"), ("versicherung_tk", 420, "pro_jahr")],
    "BMW 3er": [("inspektion", 550, "pro_jahr"), ("versicherung_tk", 620, "pro_jahr"),
                ("steuerkette", 1800, "einmalig")],
    "Skoda Octavia": [("inspektion", 330, "pro_jahr"), ("versicherung_tk", 450, "pro_jahr"),
                      ("zahnriemen", 550, "pro_intervall")],
    "Ford Focus": [("inspektion", 320, "pro_jahr"), ("versicherung_tk", 460, "pro_jahr"),
                   ("kupplung", 1200, "einmalig")],
    "Mazda 3": [("inspektion", 340, "pro_jahr"), ("versicherung_tk", 430, "pro_jahr")],
}

# Ersatzteil-Verfuegbarkeit (score 0-100, avg_price_idx 100=Durchschnitt)
PARTS = {
    "VW Golf": (98, 85), "Toyota Corolla": (85, 100), "BMW 3er": (95, 120),
    "Skoda Octavia": (95, 88), "Ford Focus": (90, 90), "Mazda 3": (75, 105),
}

# Beispiel-Angebote je Modell: (price, mileage, first_reg, plz, ort)
LISTINGS = {
    "VW Golf": [(12900, 89000, "2016-06", "79100", "Freiburg"),
                (10500, 120000, "2015-03", "79098", "Freiburg"),
                (15900, 62000, "2018-09", "77933", "Lahr")],
    "Toyota Corolla": [(18900, 55000, "2020-01", "79100", "Freiburg"),
                       (21500, 30000, "2021-05", "79104", "Freiburg")],
    "BMW 3er": [(14900, 110000, "2015-11", "79106", "Freiburg"),
                (17900, 88000, "2017-02", "77652", "Offenburg")],
    "Skoda Octavia": [(13500, 95000, "2016-08", "79114", "Freiburg"),
                      (11900, 130000, "2015-01", "79576", "Weil am Rhein")],
    "Ford Focus": [(8900, 105000, "2015-05", "79098", "Freiburg"),
                   (7500, 140000, "2014-09", "79312", "Emmendingen")],
    "Mazda 3": [(12500, 78000, "2017-04", "79100", "Freiburg")],
}

# ein paar Werkstaetten fuer Werkstattdichte
WORKSHOPS = [
    ("VW", "Autohaus Suedbaden VW", "79100", "Freiburg", 1),
    ("VW", "Freie KFZ Meier", "79098", "Freiburg", 0),
    ("Skoda", "Autohaus Suedbaden Skoda", "79100", "Freiburg", 1),
    ("BMW", "BMW Niederlassung Freiburg", "79106", "Freiburg", 1),
    ("Toyota", "Toyota Freiburg", "79104", "Freiburg", 1),
    ("Ford", "Ford Haendler Emmendingen", "79312", "Emmendingen", 1),
    (None, "Freie Werkstatt ATU", "79100", "Freiburg", 0),
    (None, "Freie Werkstatt Bosch Service", "79114", "Freiburg", 0),
]


class SeedSource(Source):
    name = "seed"
    live = True

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        from ..db import upsert_model
        res = CollectResult(source=self.name)
        now = datetime.now(timezone.utc)

        model_ids: dict[str, int] = {}
        for make, model, gen, yf, yt, body, fuel in MODELS:
            mid = upsert_model(conn, make, model, gen,
                               year_from=yf, year_to=yt, body=body, fuel=fuel)
            model_ids[f"{make} {model}"] = mid
            res.inserted += 1

        for key, mid in model_ids.items():
            conn.execute(
                "INSERT OR REPLACE INTO reliability_stat(model_id,source,metric,vehicle_age,value,year)"
                " VALUES (?,?,?,?,?,?)",
                (mid, "ADAC", "pannen_pro_1000", None, RELIABILITY[key], 2024))
            conn.execute(
                "INSERT OR REPLACE INTO reliability_stat(model_id,source,metric,vehicle_age,value,year)"
                " VALUES (?,?,?,?,?,?)",
                (mid, "TUEV", "maengelquote_pct", 4, MAENGEL[key], 2024))

            conn.execute("DELETE FROM weak_point WHERE model_id=?", (mid,))
            for comp, desc, sev in WEAK.get(key, []):
                conn.execute(
                    "INSERT INTO weak_point(model_id,component,description,severity,source)"
                    " VALUES (?,?,?,?,?)", (mid, comp, desc, sev, "seed"))

            for code, date, desc in RECALLS.get(key, []):
                conn.execute(
                    "INSERT OR IGNORE INTO recall(model_id,kba_code,date,description)"
                    " VALUES (?,?,?,?)", (mid, code, date, desc))

            conn.execute("DELETE FROM repair_cost WHERE model_id=?", (mid,))
            for cat, eur, period in REPAIR.get(key, []):
                conn.execute(
                    "INSERT INTO repair_cost(model_id,category,typical_eur,period,source)"
                    " VALUES (?,?,?,?,?)", (mid, cat, eur, period, "seed"))

            conn.execute("DELETE FROM parts_availability WHERE model_id=?", (mid,))
            sc, idx = PARTS.get(key, (70, 100))
            conn.execute(
                "INSERT INTO parts_availability(model_id,score,avg_price_idx,source)"
                " VALUES (?,?,?,?)", (mid, sc, idx, "seed"))

            # Angebote + Preisverlauf (simulierte 6-Wochen-Historie)
            for i, (price, km, reg, plz, ort) in enumerate(LISTINGS.get(key, [])):
                ref = f"seed-{mid}-{i}"
                first_seen = (now - timedelta(weeks=6)).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT OR REPLACE INTO listing(model_id,source,source_ref,title,price,"
                    "mileage_km,first_reg,location,plz,first_seen,last_seen,active)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
                    (mid, "seed", ref, f"{key} {reg}", price, km, reg, ort, plz,
                     first_seen, now.isoformat(timespec="seconds")))
                lid = conn.execute("SELECT id FROM listing WHERE source_ref=?", (ref,)).fetchone()["id"]
                conn.execute("DELETE FROM price_point WHERE listing_id=?", (lid,))
                # leicht fallender Preis ueber die Wochen (Schnaeppchen-Kandidaten)
                for w in range(6):
                    ts = (now - timedelta(weeks=5 - w)).isoformat(timespec="seconds")
                    p = round(price * (1.0 + 0.02 * (5 - w)))   # frueher teurer
                    conn.execute(
                        "INSERT OR REPLACE INTO price_point(listing_id,ts,price) VALUES (?,?,?)",
                        (lid, ts, p))
                res.inserted += 1

        conn.execute("DELETE FROM workshop")
        for make, name, plz, ort, spec in WORKSHOPS:
            conn.execute(
                "INSERT OR IGNORE INTO workshop(make,name,plz,location,specialized)"
                " VALUES (?,?,?,?,?)", (make, name, plz, ort, spec))

        conn.commit()
        res.notes = f"{len(MODELS)} Modelle, Seed-Daten geladen"
        return res
