"""Import echter TUEV-/ADAC-Zuverlaessigkeitsdaten aus data/reliability_real.csv.

Ueberschreibt die Seed-Platzhalter fuer Modelle, fuer die belegte Werte vorliegen
(is_estimate=0, mit Quelle + Altersklasse). Modelle ohne echte Zahl behalten die
Seed-Schaetzung (is_estimate=1). Laeuft nach SeedSource.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .base import CollectResult, Source

REAL_CSV = Path(__file__).resolve().parent.parent.parent / "data" / "reliability_real.csv"


def _find_model(conn, make: str, model: str) -> int | None:
    """Nur vorhandene Modelle zuordnen (kein Anlegen)."""
    row = conn.execute(
        "SELECT id FROM car_model WHERE lower(make)=lower(?) AND ("
        "  lower(?)=lower(model) OR lower(?) LIKE lower(model)||'%'"
        ") ORDER BY length(model) DESC LIMIT 1",
        (make, model, model)).fetchone()
    return row["id"] if row else None


class ReliabilityImportSource(Source):
    name = "tuev_adac"
    live = True

    def __init__(self, csv_path: Path | str = REAL_CSV):
        self.csv_path = Path(csv_path)

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        res = CollectResult(source=self.name)
        if not self.csv_path.exists():
            res.notes = "keine reliability_real.csv gefunden"
            return res
        unmatched = []
        with open(self.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(row for row in f if not row.lstrip().startswith("#"))
            for r in reader:
                val = (r.get("value") or "").strip()
                if not val or val.lower() == "n/a":
                    continue
                mid = _find_model(conn, r["make"].strip(), r["model"].strip())
                if mid is None:
                    unmatched.append(f"{r['make']} {r['model']}")
                    continue
                # echte Werte ersetzen die Seed-Schaetzung fuer dieses Modell+Quelle+Metrik
                conn.execute(
                    "DELETE FROM reliability_stat WHERE model_id=? AND source=? AND metric=?",
                    (mid, r["source"].strip(), r["metric"].strip()))
                conn.execute(
                    "INSERT INTO reliability_stat(model_id,source,metric,vehicle_age,value,"
                    "year,is_estimate,source_url,note) VALUES (?,?,?,?,?,?,0,?,?)",
                    (mid, r["source"].strip(), r["metric"].strip(),
                     int(r["vehicle_age"]) if r.get("vehicle_age") else None,
                     float(val), int(r["report_year"]) if r.get("report_year") else None,
                     r.get("source_url"), r.get("note")))
                res.inserted += 1
        conn.commit()
        res.notes = f"{res.inserted} echte Werte importiert"
        if unmatched:
            res.notes += f"; nicht zugeordnet: {', '.join(sorted(set(unmatched)))}"
        return res
