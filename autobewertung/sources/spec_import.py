"""Import echter/recherchierter Fahrzeug-Specs aus data/specs_real.csv.

Vertieft Modelle (v.a. auto-entdeckte, die nur eine Minimal-Spec haben) mit
belegten Specs: Klasse, Verbrauch, Batterie/Reichweite/Ladetempo, Versicherung,
Steuer, Wertverlust, Abmessungen. Nur NICHT-leere Felder werden gesetzt
(partielles Update) -> bestehende (Seed-)Werte bleiben, wo die CSV nichts liefert.

Matcht per Marke + Modell (Generation egal), legt KEINE Modelle an. Laeuft nach
Seed/Discovery. Analog zu reliability_import / wear_import.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .base import CollectResult, Source

SPECS_CSV = Path(__file__).resolve().parent.parent.parent / "data" / "specs_real.csv"

TEXT_FIELDS = {"vehicle_class"}
INT_FIELDS = {"length_mm", "width_mm", "tk_kh", "tk_vk", "tk_tk"}
FLOAT_FIELDS = {"cons_l_100km", "cons_kwh_100km", "battery_kwh", "range_km",
                "dc_charge_kw", "km_per_30min", "insurance_eur", "tax_eur",
                "depr_pct_year", "turning_m"}
SPEC_FIELDS = TEXT_FIELDS | INT_FIELDS | FLOAT_FIELDS


def _find_model(conn, make: str, model: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM car_model WHERE lower(make)=lower(?) AND ("
        "  lower(?)=lower(model) OR lower(?) LIKE lower(model)||'%'"
        ") ORDER BY length(model) DESC LIMIT 1",
        (make, model, model)).fetchone()
    return row["id"] if row else None


class SpecImportSource(Source):
    name = "specs"
    live = True

    def __init__(self, csv_path: Path | str = SPECS_CSV):
        self.csv_path = Path(csv_path)

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        res = CollectResult(source=self.name)
        if not self.csv_path.exists():
            res.notes = "keine specs_real.csv gefunden"
            return res
        unmatched: list[str] = []
        with open(self.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(row for row in f if not row.lstrip().startswith("#"))
            for r in reader:
                mid = _find_model(conn, r["make"].strip(), r["model"].strip())
                if mid is None:
                    unmatched.append(f"{r['make']} {r['model']}")
                    continue
                fields: dict[str, object] = {}
                for k in SPEC_FIELDS:
                    v = (r.get(k) or "").strip()
                    if not v:
                        continue
                    try:
                        fields[k] = v if k in TEXT_FIELDS else (
                            int(v) if k in INT_FIELDS else float(v))
                    except ValueError:
                        continue
                if not fields:
                    continue
                sets = ", ".join(f"{k}=?" for k in fields)
                conn.execute(f"UPDATE vehicle_spec SET {sets} WHERE model_id=?",
                             (*fields.values(), mid))
                res.updated += 1
        conn.commit()
        res.notes = f"{res.updated} Modelle mit echten Specs vertieft"
        if unmatched:
            res.notes += f"; nicht zugeordnet: {', '.join(sorted(set(unmatched)))}"
        return res
