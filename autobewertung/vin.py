"""VIN-/FIN-Decoder ueber die kostenlose NHTSA vPIC API.

Dekodiert eine Fahrgestellnummer zu Marke/Modell/Baujahr/Motor und versucht,
das Fahrzeug einem vorhandenen Modell + Untermodell (Variante) zuzuordnen.

Hinweis: NHTSA ist US-orientiert - Marke/Modell/Baujahr/Kraftstoff kommen meist
sauber, exakte EU-Motorvarianten oft nur unvollstaendig.
"""
from __future__ import annotations

import json
import re
import sqlite3

NHTSA_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"
FIELDS = ["Make", "Model", "ModelYear", "Series", "Trim", "FuelTypePrimary",
          "ElectrificationLevel", "DisplacementL", "EngineCylinders", "BodyClass",
          "DriveType", "TransmissionStyle", "PlantCountry", "ErrorText"]

BRAND_NORMALIZE = {
    "volkswagen": "VW", "vw": "VW", "mercedes-benz": "Mercedes", "bmw": "BMW",
    "audi": "Audi", "skoda": "Skoda", "škoda": "Skoda", "seat": "Seat", "opel": "Opel",
    "ford": "Ford", "toyota": "Toyota", "mazda": "Mazda", "kia": "Kia",
    "hyundai": "Hyundai", "honda": "Honda", "peugeot": "Peugeot", "renault": "Renault",
    "tesla": "Tesla", "cupra": "Cupra", "mg": "MG", "polestar": "Polestar",
}


def _http_get(url: str) -> str:
    import requests
    return requests.get(url, timeout=20,
                        headers={"User-Agent": "auto-bewertung/0.1"}).text


def valid_vin(vin: str) -> bool:
    vin = (vin or "").strip().upper()
    return bool(re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin))  # ohne I,O,Q


def decode_vin(vin: str, fetch=None) -> dict:
    """Gibt die relevanten dekodierten Felder zurueck (leere weggelassen)."""
    vin = vin.strip().upper()
    raw = (fetch or _http_get)(NHTSA_URL.format(vin=vin))
    res = json.loads(raw)["Results"][0]
    out = {k: res[k] for k in FIELDS if res.get(k)}
    if out.get("Make"):
        out["make_norm"] = BRAND_NORMALIZE.get(out["Make"].strip().lower(), out["Make"].title())
    return out


def match_model(conn: sqlite3.Connection, decoded: dict) -> int | None:
    """Ordnet die dekodierten Daten einem vorhandenen car_model zu."""
    make = decoded.get("make_norm")
    model = decoded.get("Model")
    if not make or not model:
        return None
    row = conn.execute(
        "SELECT id FROM car_model WHERE lower(make)=lower(?) AND ("
        "  lower(?)=lower(model) OR lower(?) LIKE lower(model)||'%' "
        "  OR lower(model) LIKE lower(?)||'%'"
        ") ORDER BY length(model) DESC LIMIT 1",
        (make, model, model, model)).fetchone()
    return row["id"] if row else None


def guess_variant(conn: sqlite3.Connection, model_id: int, decoded: dict) -> str | None:
    """Versucht, aus Hubraum/Kraftstoff/Baujahr eine passende Variante zu raten."""
    variants = [r["variant"] for r in conn.execute(
        "SELECT DISTINCT variant FROM wear_item WHERE model_id=? AND variant!='alle'", (model_id,))]
    if not variants:
        return None
    disp = decoded.get("DisplacementL")
    fuel = (decoded.get("FuelTypePrimary") or "").lower()
    year = decoded.get("ModelYear")
    # 1) Hubraum als "1.4"/"1.8" im Variantennamen suchen
    if disp:
        try:
            d = f"{float(disp):.1f}"      # z.B. '1.4'
            for v in variants:
                if d in v:
                    return v
        except ValueError:
            pass
    # 2) Diesel/Benziner grob
    key = "diesel" if "diesel" in fuel else ("tdi" if "diesel" in fuel else None)
    if key:
        for v in variants:
            if "diesel" in v.lower() or "tdi" in v.lower() or "dci" in v.lower():
                return v
    # 3) Baujahr in Variantennamen ("ab 2021", "2019-2020")
    if year:
        for v in variants:
            if str(year) in v:
                return v
    return None
