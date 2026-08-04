"""Import recherchierter, quellenbelegter Schwachstellen aus data/weak_real.csv.

Fuegt MODELLSPEZIFISCHE Schwachstellen (Achtungsliste/Verhandlungs-Munition) in
die weak_point-Tabelle ein -> speist Kauf-Check, Dossier und Wissenstiefe.
Ergaenzt v.a. auto-entdeckte Modelle, die der Seed nicht kennt. Ein CSV-Eintrag
kann auf mehrere Modelle passen (Praefix in beide Richtungen). Laeuft nach Seed;
ersetzt nur eigene (source='real') Eintraege, laesst Seed-Schwachstellen stehen.

severity: 1=kosmetisch/klein .. 3=teuer/sicherheitsrelevant (Default 2).
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .base import CollectResult, Source

WEAK_CSV = Path(__file__).resolve().parent.parent.parent / "data" / "weak_real.csv"


def _find_models(conn, make: str, model: str) -> list[int]:
    """Alle vorhandenen Modelle, die zu (make, model) passen (Praefix in beide Richtungen)."""
    rows = conn.execute(
        "SELECT id FROM car_model WHERE lower(make)=lower(?) AND ("
        "  lower(model)=lower(?)"
        "  OR lower(?) LIKE lower(model)||'%'"     # CSV-Modell beginnt mit DB-Modell
        "  OR lower(model) LIKE lower(?)||'%'"      # DB-Modell beginnt mit CSV-Modell
        ")", (make, model, model, model)).fetchall()
    return [r["id"] for r in rows]


class WeakPointImportSource(Source):
    name = "weak_real"
    live = True

    def __init__(self, csv_path: Path | str = WEAK_CSV):
        self.csv_path = Path(csv_path)

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        res = CollectResult(source=self.name)
        if not self.csv_path.exists():
            res.notes = "keine weak_real.csv"
            return res
        cleared: set[int] = set()
        unmatched = []
        with open(self.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(row for row in f if not row.lstrip().startswith("#"))
            for r in reader:
                comp = (r.get("component") or "").strip()
                desc = (r.get("description") or "").strip()
                if not comp and not desc:
                    continue
                mids = _find_models(conn, r["make"].strip(), r["model"].strip())
                if not mids:
                    unmatched.append(f"{r['make']} {r['model']}")
                    continue
                try:
                    sev = int(r.get("severity") or 2)
                except (ValueError, TypeError):
                    sev = 2
                try:
                    cost = float(r["cost_eur"]) if (r.get("cost_eur") or "").strip() else None
                except (ValueError, TypeError):
                    cost = None
                url = (r.get("source_url") or "").strip() or None
                for mid in mids:
                    if mid not in cleared:
                        conn.execute("DELETE FROM weak_point WHERE model_id=? AND source='real'", (mid,))
                        cleared.add(mid)
                    conn.execute(
                        "INSERT INTO weak_point(model_id,component,description,severity,cost_eur,source,url)"
                        " VALUES (?,?,?,?,?, 'real', ?)", (mid, comp, desc, sev, cost, url))
                    res.inserted += 1
        conn.commit()
        res.notes = f"{res.inserted} echte Schwachstellen importiert"
        if unmatched:
            res.notes += f"; nicht zugeordnet: {', '.join(sorted(set(unmatched)))}"
        return res
