"""Seed-Quelle: laedt realistische Beispieldaten, damit das Tool sofort laeuft.

Die Zahlen sind plausible Groessenordnungen fuer bekannte DE-Gebrauchtwagen
(inkl. E-Autos) - als Platzhalter zu verstehen. Sobald echte Adapter live gehen,
ueberschreiben deren Daten diese Seeds.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from .base import CollectResult, Source

# (make, model, generation, year_from, year_to, body, fuel)
MODELS = [
    ("VW", "Golf", "VII (2012-2019)", 2012, 2019, "Kompakt", "Benzin"),
    ("Toyota", "Corolla", "E210 (2019-)", 2019, 2025, "Kompakt", "Hybrid"),
    ("BMW", "3er", "F30 (2011-2019)", 2011, 2019, "Limousine", "Diesel"),
    ("Skoda", "Octavia", "III (2013-2020)", 2013, 2020, "Kombi", "Benzin"),
    ("Ford", "Focus", "III (2011-2018)", 2011, 2018, "Kompakt", "Benzin"),
    ("Mazda", "3", "BM (2013-2019)", 2013, 2019, "Kompakt", "Benzin"),
    ("Tesla", "Model 3", "(2019-)", 2019, 2025, "Limousine", "Elektro"),
    ("Hyundai", "Ioniq 5", "(2021-)", 2021, 2025, "SUV", "Elektro"),
    ("VW", "ID.3 Pro", "58 kWh (2020-)", 2020, 2025, "Kompakt", "Elektro"),
    ("VW", "ID.3 Pro S", "77 kWh (2020-)", 2020, 2025, "Kompakt", "Elektro"),
    ("Renault", "Zoe", "(2019-)", 2019, 2024, "Kleinwagen", "Elektro"),
]

# vehicle_spec je Modell:
# drivetrain, class, cons_l, cons_kwh, battery, range, dc_kw, km/30min, insurance, tax, typical_price
SPECS = {
    "VW Golf":        ("benzin",  "kompakt",      6.5, None, None, None, None, None, 480, 120, 12900),
    "Toyota Corolla": ("hybrid",  "kompakt",      4.5, None, None, None, None, None, 420,  40, 19900),
    "BMW 3er":        ("diesel",  "mittelklasse", 5.5, None, None, None, None, None, 620, 220, 15900),
    "Skoda Octavia":  ("benzin",  "kompakt",      6.0, None, None, None, None, None, 450, 130, 13500),
    "Ford Focus":     ("benzin",  "kompakt",      6.5, None, None, None, None, None, 460, 120,  8900),
    "Mazda 3":        ("benzin",  "kompakt",      6.8, None, None, None, None, None, 430, 130, 12500),
    # Elektro: cons_kwh, battery, range, dc_kw, km_per_30min gesetzt
    "Tesla Model 3":  ("elektro", "mittelklasse", None, 15.5, 57.0, 430, 170, 320, 520, 0, 22000),
    "Hyundai Ioniq 5":("elektro", "suv",          None, 17.5, 72.0, 400, 220, 340, 560, 0, 31000),
    # realistisch warm/vorkonditioniert; Winter deutlich weniger
    "VW ID.3 Pro":    ("elektro", "kompakt",      None, 16.5, 58.0, 340, 120, 250, 500, 0, 19000),
    "VW ID.3 Pro S":  ("elektro", "kompakt",      None, 16.5, 77.0, 420, 170, 305, 520, 0, 22900),
    "Renault Zoe":    ("elektro", "kleinwagen",   None, 17.0, 52.0, 300,  46, 120, 470, 0, 11000),
}

# Pannen pro 1000 (kleiner=besser) und TUEV Maengelquote % (kleiner=besser)
RELIABILITY = {
    "VW Golf": 8.0, "Toyota Corolla": 3.5, "BMW 3er": 9.5, "Skoda Octavia": 7.0,
    "Ford Focus": 11.0, "Mazda 3": 5.5, "Tesla Model 3": 6.0, "Hyundai Ioniq 5": 4.0,
    "VW ID.3 Pro": 7.5, "VW ID.3 Pro S": 7.5, "Renault Zoe": 5.0,
}
MAENGEL = {
    "VW Golf": 6.5, "Toyota Corolla": 3.0, "BMW 3er": 5.5, "Skoda Octavia": 6.0,
    "Ford Focus": 8.5, "Mazda 3": 4.5, "Tesla Model 3": 14.5, "Hyundai Ioniq 5": 5.5,
    "VW ID.3 Pro": 4.5, "VW ID.3 Pro S": 4.5, "Renault Zoe": 4.0,
}

# Eintraege: (Bauteil, Beschreibung, Schwere 1-3, typ. Reparaturkosten EUR).
# Kosten = Werkstatt-Groessenordnung des typischen Defekts (fliesst in die TCO).
WEAK = {
    "VW Golf": [("DSG", "DSG-Mechatronik/Kupplung (DQ200)", 3, 1500),
                ("Steuerkette", "Steuerkettenlaengung fruehe 1.4 TSI", 3, 1500)],
    "Toyota Corolla": [("Hybrid-12V", "12V-Zusatzbatterie schwach im Winter", 1, 200)],
    "BMW 3er": [("Steuerkette", "Steuerkette N47-Diesel", 3, 1800),
                ("Kettenspanner", "Kettenspanner N20 Benziner", 2, 900),
                ("Achse", "Querlenker/Traggelenke (TÜV: Achsaufhaengung)", 2, 700)],
    "Skoda Octavia": [("DSG", "DQ200 Trockenkupplung", 2, 1500),
                      ("Wasserpumpe", "Wasserpumpe undicht 1.8/2.0 TSI", 2, 500)],
    "Ford Focus": [("Doppelkupplung", "Powershift-Getriebe ruckelt", 3, 1800),
                   ("Zuendspule", "Zuendspulen 1.0 EcoBoost", 2, 300),
                   ("Kuehlung", "Kuehlmittelverlust 1.0 EcoBoost (Motorschaden-Risiko)", 3, 900)],
    "Mazda 3": [("Rost", "Radlaeufe/Unterboden Rost", 2, 600)],
    "Tesla Model 3": [("Querlenker/Achse", "Vordere Querlenker/Buchsen Verschleiss "
                       "(TÜV bemaengelt Achsaufhaengung)", 2, 750),
                      ("Bremsen", "Bremsscheiben-Korrosion (Rekuperation, wenig genutzt)", 2, 400),
                      ("Beleuchtung", "Licht-/Elektronik-Beanstandungen bei HU", 1, 200),
                      ("MCU", "eMMC-Speicher Verschleiss aeltere Baujahre", 2, 1500)],
    "Hyundai Ioniq 5": [("ICCU", "ICCU/12V-Ladewandler-Ausfall (oft Garantie)", 3, 1200)],
    "VW ID.3 Pro": [("Software", "Infotainment-Bugs Fruehserien (Update)", 2, 0)],
    "VW ID.3 Pro S": [("Software", "Infotainment-Bugs Fruehserien (Update)", 2, 0)],
    "Renault Zoe": [("Akku", "Akkumiete/Degradation bei aelteren", 2, 0),
                    ("12V-Batterie", "Starterbatterie/12V auffaellig (ADAC)", 2, 150)],
}

RECALLS = {
    "Ford Focus": [("009xyz", "2018-05-01", "Kuehlmittel-Ueberhitzung 1.0 EcoBoost")],
    "BMW 3er": [("007abc", "2016-03-15", "EGR-Modul Diesel Brandgefahr")],
    "Hyundai Ioniq 5": [("011ic", "2023-04-01", "ICCU-Update 12V-Ladung")],
}

# Wartung/Reparatur OHNE Versicherung (die steckt in vehicle_spec.insurance_eur)
REPAIR = {
    "VW Golf": [("inspektion", 350, "pro_jahr"), ("zahnriemen", 600, "pro_intervall")],
    "Toyota Corolla": [("inspektion", 300, "pro_jahr")],
    "BMW 3er": [("inspektion", 550, "pro_jahr"), ("steuerkette", 1800, "einmalig")],
    "Skoda Octavia": [("inspektion", 330, "pro_jahr"), ("zahnriemen", 550, "pro_intervall")],
    "Ford Focus": [("inspektion", 320, "pro_jahr"), ("kupplung", 1200, "einmalig")],
    "Mazda 3": [("inspektion", 340, "pro_jahr")],
    "Tesla Model 3": [("inspektion", 200, "pro_jahr"), ("bremsfluessigkeit", 120, "pro_intervall")],
    "Hyundai Ioniq 5": [("inspektion", 220, "pro_jahr"), ("bremsfluessigkeit", 120, "pro_intervall")],
    "VW ID.3 Pro": [("inspektion", 210, "pro_jahr"), ("bremsfluessigkeit", 120, "pro_intervall")],
    "VW ID.3 Pro S": [("inspektion", 210, "pro_jahr"), ("bremsfluessigkeit", 120, "pro_intervall")],
    "Renault Zoe": [("inspektion", 190, "pro_jahr"), ("bremsfluessigkeit", 120, "pro_intervall")],
}

# Ersatzteil-Verfuegbarkeit (score 0-100, avg_price_idx 100=Durchschnitt)
PARTS = {
    "VW Golf": (98, 85), "Toyota Corolla": (85, 100), "BMW 3er": (95, 120),
    "Skoda Octavia": (95, 88), "Ford Focus": (90, 90), "Mazda 3": (75, 105),
    "Tesla Model 3": (70, 110), "Hyundai Ioniq 5": (78, 105),
    "VW ID.3 Pro": (90, 95), "VW ID.3 Pro S": (90, 95), "Renault Zoe": (85, 95),
}

# Angebote je Modell: (price, mileage, first_reg, plz, ort)
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
    "Tesla Model 3": [(19900, 130000, "2019-06", "79100", "Freiburg"),
                      (23900, 70000, "2020-03", "79104", "Freiburg")],
    "Hyundai Ioniq 5": [(30900, 45000, "2022-01", "79106", "Freiburg")],
    "VW ID.3 Pro": [(18900, 60000, "2021-05", "79100", "Freiburg")],
    "VW ID.3 Pro S": [(22900, 45000, "2022-03", "79104", "Freiburg")],
    "Renault Zoe": [(10500, 40000, "2020-08", "79098", "Freiburg")],
}

# Verschleiss/Teile: (Bauteil, at_km erste Faelligkeit, interval_km 0=einmalig, Kosten EUR)
# Generische Teile je Antriebsart (gelten fuer JEDES Modell dieser Art):
WEAR_TEMPLATE = {
    "benzin": [
        ("Bremsbelaege", 50000, 60000, 250), ("Bremsscheiben", 90000, 90000, 400),
        ("Reifen (Satz)", 45000, 45000, 520), ("12V-Batterie", 70000, 80000, 150),
        ("Stossdaempfer", 140000, 0, 600), ("Auspuff/Abgas", 160000, 0, 500),
    ],
    "diesel": [
        ("Bremsbelaege", 50000, 60000, 250), ("Bremsscheiben", 90000, 90000, 400),
        ("Reifen (Satz)", 45000, 45000, 520), ("12V-Batterie", 70000, 80000, 150),
        ("Stossdaempfer", 140000, 0, 600), ("AGR/DPF", 150000, 0, 900),
    ],
    "hybrid": [
        ("Bremsbelaege", 80000, 90000, 250), ("Bremsscheiben", 120000, 0, 400),
        ("Reifen (Satz)", 45000, 45000, 520), ("12V-Batterie", 70000, 80000, 150),
        ("Stossdaempfer", 150000, 0, 600),
    ],
    "elektro": [
        ("Bremsbelaege", 100000, 100000, 250), ("Bremsscheiben", 120000, 0, 450),
        ("Reifen (Satz)", 35000, 35000, 560), ("12V-Batterie", 70000, 80000, 150),
        ("Fahrwerk/Lenker", 120000, 0, 700),
    ],
}
# Modellspezifische Teile (bekannte Schwachstellen mit typischer km-Faelligkeit):
WEAR_SPECIFIC = {
    "VW Golf": [("Zahnriemen", 120000, 120000, 600), ("DSG-Kupplung", 130000, 0, 1500),
                ("Steuerkette 1.4 TSI", 150000, 0, 1500)],
    "Skoda Octavia": [("Zahnriemen", 120000, 120000, 550), ("DSG-Kupplung", 130000, 0, 1500),
                      ("Wasserpumpe", 120000, 0, 500)],
    "Seat Leon": [("Zahnriemen", 120000, 120000, 550), ("DSG-Kupplung", 130000, 0, 1500)],
    "Audi A3": [("Zahnriemen", 120000, 120000, 600), ("Oelverbrauch/Kolbenringe", 130000, 0, 2500)],
    "BMW 3er": [("Steuerkette N47", 150000, 0, 1800), ("Querlenker/Achse", 110000, 0, 700)],
    "Ford Focus": [("Powershift-Kupplung", 120000, 0, 1800), ("Kuehlung EcoBoost", 90000, 0, 900)],
    "Opel Astra": [("Steuerkette", 140000, 0, 1200), ("Wasserpumpe", 120000, 0, 500)],
    "Peugeot 308": [("Steuerkette 1.2 PureTech", 100000, 0, 1400)],
    "Renault Megane": [("Zahnriemen", 120000, 120000, 520)],
    "Hyundai i30": [("Zahnriemen", 120000, 120000, 500)],
    "Kia Ceed": [("Zahnriemen", 120000, 120000, 480)],
    "Tesla Model 3": [("Querlenker/Achse", 80000, 0, 750), ("MCU-eMMC", 150000, 0, 1500)],
    "Hyundai Ioniq 5": [("ICCU/12V-Wandler", 60000, 0, 1200)],
    "Kia EV6": [("ICCU/12V-Wandler", 60000, 0, 1200)],
}

WORKSHOPS = [
    ("VW", "Autohaus Suedbaden VW", "79100", "Freiburg", 1),
    ("VW", "Freie KFZ Meier", "79098", "Freiburg", 0),
    ("Skoda", "Autohaus Suedbaden Skoda", "79100", "Freiburg", 1),
    ("BMW", "BMW Niederlassung Freiburg", "79106", "Freiburg", 1),
    ("Toyota", "Toyota Freiburg", "79104", "Freiburg", 1),
    ("Ford", "Ford Haendler Emmendingen", "79312", "Emmendingen", 1),
    ("Hyundai", "Hyundai Autohaus Freiburg", "79106", "Freiburg", 1),
    ("Tesla", "Tesla Service Center", "79108", "Freiburg", 1),
    ("Renault", "Renault Freiburg", "79111", "Freiburg", 1),
    (None, "Freie Werkstatt ATU", "79100", "Freiburg", 0),
    (None, "Freie Werkstatt Bosch Service", "79114", "Freiburg", 0),
]

# --- Weitere gaengige Kompaktmodelle (fuer ein groesseres Top-15-Ranking) ----
MODELS += [
    ("Toyota", "Auris", "E180 (2012-2018)", 2012, 2018, "Kompakt", "Hybrid"),
    ("Opel", "Astra", "K (2015-2021)", 2015, 2021, "Kompakt", "Benzin"),
    ("Seat", "Leon", "III (2012-2020)", 2012, 2020, "Kompakt", "Benzin"),
    ("Audi", "A3", "8V (2012-2020)", 2012, 2020, "Kompakt", "Benzin"),
    ("Hyundai", "i30", "III (2016-)", 2016, 2025, "Kompakt", "Benzin"),
    ("Kia", "Ceed", "III (2018-)", 2018, 2025, "Kompakt", "Benzin"),
    ("Honda", "Civic", "X (2017-2021)", 2017, 2021, "Kompakt", "Benzin"),
    ("Peugeot", "308", "II (2013-2021)", 2013, 2021, "Kompakt", "Benzin"),
    ("Renault", "Megane", "IV (2016-)", 2016, 2025, "Kompakt", "Benzin"),
]
SPECS.update({
    "Toyota Auris":   ("hybrid", "kompakt", 4.2, None, None, None, None, None, 400,  40, 13500),
    "Opel Astra":     ("benzin", "kompakt", 6.2, None, None, None, None, None, 440, 120, 11500),
    "Seat Leon":      ("benzin", "kompakt", 6.0, None, None, None, None, None, 450, 120, 12900),
    "Audi A3":        ("benzin", "kompakt", 6.1, None, None, None, None, None, 520, 130, 14500),
    "Hyundai i30":    ("benzin", "kompakt", 6.0, None, None, None, None, None, 420, 120, 12500),
    "Kia Ceed":       ("benzin", "kompakt", 6.1, None, None, None, None, None, 420, 120, 13500),
    "Honda Civic":    ("benzin", "kompakt", 5.8, None, None, None, None, None, 460, 120, 14500),
    "Peugeot 308":    ("benzin", "kompakt", 6.0, None, None, None, None, None, 440, 120, 10900),
    "Renault Megane": ("benzin", "kompakt", 6.1, None, None, None, None, None, 430, 120, 11500),
})
RELIABILITY.update({
    "Toyota Auris": 4.0, "Opel Astra": 9.0, "Seat Leon": 7.5, "Audi A3": 6.5,
    "Hyundai i30": 4.5, "Kia Ceed": 4.2, "Honda Civic": 5.0, "Peugeot 308": 9.5,
    "Renault Megane": 8.5,
})
MAENGEL.update({
    "Toyota Auris": 3.2, "Opel Astra": 6.8, "Seat Leon": 6.0, "Audi A3": 5.2,
    "Hyundai i30": 3.8, "Kia Ceed": 3.5, "Honda Civic": 4.0, "Peugeot 308": 7.0,
    "Renault Megane": 6.5,
})
WEAK.update({
    "Toyota Auris": [("Hybridakku", "selten Zellalterung bei hoher Laufleistung", 1)],
    "Opel Astra": [("Steuerkette", "1.4 Turbo Kettenlaengung", 2),
                   ("Wasserpumpe", "undicht", 2)],
    "Seat Leon": [("DSG", "DQ200 Trockenkupplung", 2),
                  ("Steuerkette", "1.2/1.4 TSI Kettenlaengung", 2)],
    "Audi A3": [("Oelverbrauch", "1.8 TFSI Kolbenringe", 3), ("DSG", "Mechatronik", 2)],
    "Hyundai i30": [("Kupplung", "leichtes Rupfen Handschalter", 1)],
    "Kia Ceed": [("Zahnriemen", "Wechselintervall strikt beachten", 1)],
    "Honda Civic": [("Klimakompressor", "selten Ausfall", 1)],
    "Peugeot 308": [("Steuerkette", "1.2 PureTech Kettenverschleiss", 3),
                    ("Turbo", "1.2 PureTech", 2)],
    "Renault Megane": [("Elektrik", "Steuergeraet-/Sensorfehler", 2)],
})
REPAIR.update({
    "Toyota Auris": [("inspektion", 280, "pro_jahr")],
    "Opel Astra": [("inspektion", 320, "pro_jahr"), ("zahnriemen", 500, "pro_intervall")],
    "Seat Leon": [("inspektion", 330, "pro_jahr"), ("zahnriemen", 550, "pro_intervall")],
    "Audi A3": [("inspektion", 420, "pro_jahr"), ("zahnriemen", 600, "pro_intervall")],
    "Hyundai i30": [("inspektion", 300, "pro_jahr")],
    "Kia Ceed": [("inspektion", 300, "pro_jahr"), ("zahnriemen", 480, "pro_intervall")],
    "Honda Civic": [("inspektion", 340, "pro_jahr")],
    "Peugeot 308": [("inspektion", 330, "pro_jahr"), ("steuerkette", 1400, "einmalig")],
    "Renault Megane": [("inspektion", 320, "pro_jahr"), ("zahnriemen", 520, "pro_intervall")],
})
PARTS.update({
    "Toyota Auris": (82, 100), "Opel Astra": (88, 90), "Seat Leon": (92, 88),
    "Audi A3": (94, 110), "Hyundai i30": (80, 95), "Kia Ceed": (78, 95),
    "Honda Civic": (72, 105), "Peugeot 308": (82, 92), "Renault Megane": (84, 92),
})
LISTINGS.update({
    "Toyota Auris": [(11900, 90000, "2016-05", "79100", "Freiburg"),
                     (13900, 60000, "2017-08", "79104", "Freiburg")],
    "Opel Astra": [(10900, 95000, "2016-06", "79098", "Freiburg"),
                   (12900, 70000, "2018-03", "77933", "Lahr")],
    "Seat Leon": [(11900, 100000, "2016-04", "79100", "Freiburg"),
                  (13900, 72000, "2018-06", "79106", "Freiburg")],
    "Audi A3": [(13900, 98000, "2015-09", "79106", "Freiburg"),
                (14900, 80000, "2016-11", "77652", "Offenburg")],
    "Hyundai i30": [(11500, 85000, "2017-05", "79106", "Freiburg"),
                    (13500, 55000, "2019-02", "79100", "Freiburg")],
    "Kia Ceed": [(12900, 70000, "2019-03", "79100", "Freiburg")],
    "Honda Civic": [(13900, 75000, "2018-04", "79104", "Freiburg")],
    "Peugeot 308": [(9900, 105000, "2015-07", "79098", "Freiburg"),
                    (11900, 78000, "2017-09", "79312", "Emmendingen")],
    "Renault Megane": [(10900, 92000, "2017-04", "79111", "Freiburg")],
})
WORKSHOPS += [
    ("Opel", "Opel Autohaus Freiburg", "79106", "Freiburg", 1),
    ("Seat", "Seat Zentrum Freiburg", "79100", "Freiburg", 1),
    ("Audi", "Audi Zentrum Freiburg", "79108", "Freiburg", 1),
    ("Kia", "Kia Freiburg", "79104", "Freiburg", 1),
    ("Honda", "Honda Haendler Freiburg", "79111", "Freiburg", 1),
    ("Peugeot", "Peugeot Freiburg", "79098", "Freiburg", 1),
]

# --- Hyundai Kona Elektro: effizient + hohe Reichweite, ABER langsamer Lader --
MODELS += [("Hyundai", "Kona Elektro", "64 kWh (2018-2023)", 2018, 2023, "SUV", "Elektro")]
SPECS.update({
    # sehr effizient, grosse Reichweite; DC nur ~77 kW -> km/30min (warm) niedrig
    "Hyundai Kona Elektro": ("elektro", "suv", None, 15.0, 64.0, 400, 77, 190, 520, 0, 24000),
})
RELIABILITY.update({"Hyundai Kona Elektro": 4.0})
MAENGEL.update({"Hyundai Kona Elektro": 3.5})
WEAK.update({"Hyundai Kona Elektro": [
    ("Akku", "LG-Zellen Brandrisiko (Rueckruf 2020/21, Akkutausch/Update)", 3),
    ("Ladeleistung", "max ~77 kW DC, kein 800V -> langsames Schnellladen", 1),
]})
RECALLS.update({"Hyundai Kona Elektro": [
    ("kona-bms-21", "2021-02-01", "Batterie-Rueckruf: LG-Zellen Brandrisiko, Austausch/BMS-Update")]})
REPAIR.update({"Hyundai Kona Elektro": [
    ("inspektion", 210, "pro_jahr"), ("bremsfluessigkeit", 120, "pro_intervall")]})
PARTS.update({"Hyundai Kona Elektro": (80, 100)})
LISTINGS.update({"Hyundai Kona Elektro": [
    (23900, 55000, "2020-06", "79106", "Freiburg"),
    (21900, 72000, "2019-09", "79100", "Freiburg")]})

# --- Weitere gute E-Autos in der Klasse (Kompakt/Mittelklasse) ---------------
# Realistisch warm. Nur schnelle Lader (800V bzw. gute Kurve) schaffen 300 km/30min.
MODELS += [
    ("Kia", "EV6", "77 kWh 800V (2021-)", 2021, 2025, "SUV", "Elektro"),
    ("Cupra", "Born", "77 kWh (2021-)", 2021, 2025, "Kompakt", "Elektro"),
    ("MG", "MG4", "64 kWh (2022-)", 2022, 2025, "Kompakt", "Elektro"),
    ("Polestar", "2", "78 kWh (2020-)", 2020, 2025, "Mittelklasse", "Elektro"),
]
SPECS.update({
    "Kia EV6":    ("elektro", "suv",          None, 16.5, 77.0, 420, 240, 360, 560, 0, 32000),
    "Cupra Born": ("elektro", "kompakt",      None, 16.0, 77.0, 400, 170, 300, 520, 0, 24500),
    "MG MG4":     ("elektro", "kompakt",      None, 16.5, 64.0, 350, 140, 250, 480, 0, 21000),
    "Polestar 2": ("elektro", "mittelklasse", None, 18.0, 78.0, 400, 150, 260, 560, 0, 28000),
})
RELIABILITY.update({"Kia EV6": 4.5, "Cupra Born": 6.5, "MG MG4": 6.0, "Polestar 2": 5.5})
MAENGEL.update({"Kia EV6": 3.8, "Cupra Born": 4.5, "MG MG4": 5.0, "Polestar 2": 4.2})
WEAK.update({
    "Kia EV6": [("ICCU", "ICCU/12V-Ladewandler kann ausfallen", 3)],
    "Cupra Born": [("Software", "MEB-Infotainment-Bugs Fruehserien", 2)],
    "MG MG4": [("Software", "Assistenz/Software unausgereift", 1),
               ("Langzeit", "wenig Langzeiterfahrung", 1)],
    "Polestar 2": [("12V", "12V-Batterie-Entladung Fruehserien", 2),
                   ("Software", "OTA-Kinderkrankheiten frueh", 1)],
})
REPAIR.update({
    "Kia EV6": [("inspektion", 230, "pro_jahr"), ("bremsfluessigkeit", 120, "pro_intervall")],
    "Cupra Born": [("inspektion", 220, "pro_jahr"), ("bremsfluessigkeit", 120, "pro_intervall")],
    "MG MG4": [("inspektion", 200, "pro_jahr"), ("bremsfluessigkeit", 120, "pro_intervall")],
    "Polestar 2": [("inspektion", 260, "pro_jahr"), ("bremsfluessigkeit", 120, "pro_intervall")],
})
PARTS.update({"Kia EV6": (76, 105), "Cupra Born": (88, 95), "MG MG4": (62, 100), "Polestar 2": (70, 110)})
LISTINGS.update({
    "Kia EV6": [(31900, 45000, "2022-02", "79106", "Freiburg")],
    "Cupra Born": [(23900, 40000, "2022-05", "79100", "Freiburg"),
                   (25900, 28000, "2023-01", "79104", "Freiburg")],
    "MG MG4": [(20900, 30000, "2023-03", "79100", "Freiburg")],
    "Polestar 2": [(27900, 50000, "2021-06", "79106", "Freiburg")],
})
WORKSHOPS += [
    ("Cupra", "Cupra/Seat Zentrum Freiburg", "79100", "Freiburg", 1),
    ("Polestar", "Polestar Space Freiburg", "79108", "Freiburg", 1),
]

# Jaehrlicher Wertverlust-Anteil (Wertstabilitaet). Kleiner = wertstabiler.
# Toyota/Mazda halten gut; E-Autos verloren zuletzt stark (Marktlage 2023/24).
DEPR = {
    "VW Golf": 0.10, "Toyota Corolla": 0.09, "BMW 3er": 0.12, "Skoda Octavia": 0.11,
    "Ford Focus": 0.13, "Mazda 3": 0.11, "Toyota Auris": 0.09, "Opel Astra": 0.14,
    "Seat Leon": 0.12, "Audi A3": 0.12, "Hyundai i30": 0.12, "Kia Ceed": 0.12,
    "Honda Civic": 0.11, "Peugeot 308": 0.14, "Renault Megane": 0.14,
    "Tesla Model 3": 0.16, "Hyundai Ioniq 5": 0.16, "VW ID.3 Pro": 0.17,
    "VW ID.3 Pro S": 0.17, "Renault Zoe": 0.16, "Hyundai Kona Elektro": 0.15,
    "Kia EV6": 0.15, "Cupra Born": 0.17, "MG MG4": 0.18, "Polestar 2": 0.17,
}

# Ausstattung je Modell: verfuegbare Assistenz/Komfort-Features (Serie oder Option)
# + ob das Modell haeufig teure Matrix-/adaptive-LED-Scheinwerfer hat.
WANTED_FEATURES = ["einparkhilfe", "rueckfahrkamera", "notbremsassistent", "spurhalteassistent"]
ALL_COMMON = set(WANTED_FEATURES)          # moderne Kompakte bieten i.d.R. alle vier
EQUIP = {k: set(ALL_COMMON) for k in DEPR}  # Standardannahme: alle vier verfuegbar
# Ausnahmen (aeltere/einfachere Modelle ohne bestimmte Assistenz ab Werk verfuegbar):
EQUIP["Ford Focus"] = {"einparkhilfe", "rueckfahrkamera"}          # AEB/Spur nur spaeter/selten
EQUIP["Mazda 3"] = {"einparkhilfe", "rueckfahrkamera", "notbremsassistent"}
EQUIP["Renault Zoe"] = {"einparkhilfe", "rueckfahrkamera"}
# Modelle, die oft teure Matrix-/Voll-LED-Scheinwerfer tragen (teuer in Reparatur):
MATRIX_MODELS = {"Audi A3", "BMW 3er", "VW Golf", "Kia EV6", "Polestar 2", "Hyundai Ioniq 5"}


class SeedSource(Source):
    name = "seed"
    live = True

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        from ..db import upsert_model, upsert_spec
        res = CollectResult(source=self.name)
        now = datetime.now(timezone.utc)

        model_ids: dict[str, int] = {}
        for make, model, gen, yf, yt, body, fuel in MODELS:
            mid = upsert_model(conn, make, model, gen,
                               year_from=yf, year_to=yt, body=body, fuel=fuel)
            model_ids[f"{make} {model}"] = mid
            res.inserted += 1

        for key, mid in model_ids.items():
            dt, vclass, cl, ck, batt, rng, dc, km30, ins, tax, tprice = SPECS[key]
            upsert_spec(conn, mid, drivetrain=dt, vehicle_class=vclass,
                        cons_l_100km=cl, cons_kwh_100km=ck, battery_kwh=batt,
                        range_km=rng, dc_charge_kw=dc, km_per_30min=km30,
                        insurance_eur=ins, tax_eur=tax, typical_price=tprice,
                        depr_pct_year=DEPR.get(key, 0.13),
                        features=",".join(sorted(EQUIP.get(key, ALL_COMMON))),
                        has_matrix=1 if key in MATRIX_MODELS else 0)

            conn.execute(
                "INSERT OR REPLACE INTO reliability_stat(model_id,source,metric,vehicle_age,value,year)"
                " VALUES (?,?,?,?,?,?)",
                (mid, "ADAC", "pannen_pro_1000", None, RELIABILITY[key], 2024))
            conn.execute(
                "INSERT OR REPLACE INTO reliability_stat(model_id,source,metric,vehicle_age,value,year)"
                " VALUES (?,?,?,?,?,?)",
                (mid, "TUEV", "maengelquote_pct", 4, MAENGEL[key], 2024))

            conn.execute("DELETE FROM weak_point WHERE model_id=?", (mid,))
            for entry in WEAK.get(key, []):
                comp, desc, sev = entry[0], entry[1], entry[2]
                cost = entry[3] if len(entry) > 3 else None
                conn.execute(
                    "INSERT INTO weak_point(model_id,component,description,severity,cost_eur,source)"
                    " VALUES (?,?,?,?,?,?)", (mid, comp, desc, sev, cost, "seed"))

            # Verschleiss-Teile: generisch je Antrieb + modellspezifisch
            conn.execute("DELETE FROM wear_item WHERE model_id=?", (mid,))
            wear = list(WEAR_TEMPLATE.get(dt, [])) + list(WEAR_SPECIFIC.get(key, []))
            for comp, at_km, interval, cost in wear:
                conn.execute(
                    "INSERT INTO wear_item(model_id,component,at_km,interval_km,cost_eur,source)"
                    " VALUES (?,?,?,?,?,?)", (mid, comp, at_km, interval, cost, "seed"))

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
                for w in range(6):
                    ts = (now - timedelta(weeks=5 - w)).isoformat(timespec="seconds")
                    p = round(price * (1.0 + 0.02 * (5 - w)))
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
        res.notes = f"{len(MODELS)} Modelle (inkl. E-Autos), Seed-Daten geladen"
        return res
