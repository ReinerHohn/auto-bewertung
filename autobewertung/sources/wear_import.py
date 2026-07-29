"""Import recherchierter, quellenbelegter Verschleissdaten aus data/wear_real.csv.

Fuegt MODELLSPEZIFISCHE Defekte (km + Kosten) hinzu; generische Teile
(Bremsen/Reifen/12V/...) kommen weiterhin aus dem Seed-Template. Ein CSV-Eintrag
kann auf mehrere Modelle passen (z.B. 'ID.3' -> Pro und Pro S). Laeuft nach Seed.

Nur km-getriebene Posten mit at_km>0 und cost>0 werden uebernommen
(reine Software-/Ereignis-/Garantie-Posten sind fuer die km-Kurve irrelevant).
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .base import CollectResult, Source

WEAR_CSV = Path(__file__).resolve().parent.parent.parent / "data" / "wear_real.csv"


def _find_models(conn, make: str, model: str) -> list[int]:
    """Alle vorhandenen Modelle, die zu (make, model) passen (Praefix in beide Richtungen)."""
    rows = conn.execute(
        "SELECT id FROM car_model WHERE lower(make)=lower(?) AND ("
        "  lower(model)=lower(?)"
        "  OR lower(?) LIKE lower(model)||'%'"     # CSV-Modell beginnt mit DB-Modell
        "  OR lower(model) LIKE lower(?)||'%'"      # DB-Modell beginnt mit CSV-Modell (ID.3 -> ID.3 Pro)
        ")", (make, model, model, model)).fetchall()
    return [r["id"] for r in rows]


class WearImportSource(Source):
    name = "wear_real"
    live = True

    def __init__(self, csv_path: Path | str = WEAR_CSV):
        self.csv_path = Path(csv_path)

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        res = CollectResult(source=self.name)
        if not self.csv_path.exists():
            res.notes = "keine wear_real.csv"
            return res
        cleared: set[int] = set()
        unmatched = []
        with open(self.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(row for row in f if not row.lstrip().startswith("#"))
            for r in reader:
                try:
                    at_km = int(r["at_km"]); cost = float(r["cost_eur"])
                except (ValueError, KeyError, TypeError):
                    continue
                if at_km <= 0 or cost <= 0:
                    continue
                mids = _find_models(conn, r["make"].strip(), r["model"].strip())
                if not mids:
                    unmatched.append(f"{r['make']} {r['model']}")
                    continue
                for mid in mids:
                    if mid not in cleared:
                        conn.execute("DELETE FROM wear_item WHERE model_id=? AND source='real'", (mid,))
                        cleared.add(mid)
                    conn.execute(
                        "INSERT INTO wear_item(model_id,component,variant,at_km,interval_km,"
                        "cost_eur,note,source) VALUES (?,?,?,?,?,?,?, 'real')",
                        (mid, r["component"].strip(), (r.get("variant") or "alle").strip(),
                         at_km, int(r["interval_km"] or 0), cost, r.get("note")))
                    res.inserted += 1
        conn.commit()
        res.notes = f"{res.inserted} echte Verschleiss-Posten importiert"
        if unmatched:
            res.notes += f"; nicht zugeordnet: {', '.join(sorted(set(unmatched)))}"
        return res
