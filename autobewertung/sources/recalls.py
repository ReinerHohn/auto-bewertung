"""Rueckruf-Adapter.

- RecallImportSource: kuratierte, quellenbelegte KBA-/Hersteller-Rueckrufe aus
  data/recalls_real.csv -> recall-Tabelle (ersetzt Seed-Rueckrufe). Primaerquelle.
- NhtsaRecallSource: live ueber die kostenlose NHTSA-Recalls-API (US-Markt,
  deckt VW/BMW/Tesla/Toyota/... ab; viele EU-only-Modelle fehlen dort). Nicht im
  Default-Lauf (Netz), via `collect run --only nhtsa_recalls` nutzbar.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from .base import CollectResult, Source

RECALLS_CSV = Path(__file__).resolve().parent.parent.parent / "data" / "recalls_real.csv"

# NHTSA-Modellnamen fuer die in den USA verkauften Modelle (Rest -> kein Treffer)
NHTSA_MODEL = {
    ("VW", "Golf"): ("volkswagen", "golf"),
    ("Toyota", "Corolla"): ("toyota", "corolla"),
    ("BMW", "3er"): ("bmw", "3 series"),
    ("Ford", "Focus"): ("ford", "focus"),
    ("Mazda", "3"): ("mazda", "mazda3"),
    ("Audi", "A3"): ("audi", "a3"),
    ("Honda", "Civic"): ("honda", "civic"),
    ("Tesla", "Model 3"): ("tesla", "model 3"),
    ("Hyundai", "Ioniq 5"): ("hyundai", "ioniq 5"),
    ("Hyundai", "Kona Elektro"): ("hyundai", "kona electric"),
    ("Kia", "EV6"): ("kia", "ev6"),
    ("Polestar", "2"): ("polestar", "2"),
}


def _find_models(conn, make: str, model: str) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM car_model WHERE lower(make)=lower(?) AND ("
        " lower(model)=lower(?) OR lower(?) LIKE lower(model)||'%' "
        " OR lower(model) LIKE lower(?)||'%')", (make, model, model, model)).fetchall()
    return [r["id"] for r in rows]


class RecallImportSource(Source):
    name = "recalls"
    live = True

    def __init__(self, csv_path: Path | str = RECALLS_CSV):
        self.csv_path = Path(csv_path)

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        res = CollectResult(source=self.name)
        if not self.csv_path.exists():
            res.notes = "keine recalls_real.csv"
            return res
        cleared: set[int] = set()
        unmatched = []
        with open(self.csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(row for row in f if not row.lstrip().startswith("#"))
            for r in reader:
                mids = _find_models(conn, r["make"].strip(), r["model"].strip())
                if not mids:
                    unmatched.append(f"{r['make']} {r['model']}"); continue
                comp = (r.get("component") or "").strip()
                desc = (r.get("description") or "").strip()
                full = f"{comp}: {desc}" if comp else desc
                code = (r.get("kba_code") or "").strip() or None
                for mid in mids:
                    if mid not in cleared:
                        conn.execute("DELETE FROM recall WHERE model_id=?", (mid,))
                        cleared.add(mid)
                    conn.execute(
                        "INSERT INTO recall(model_id,kba_code,date,description,url) VALUES (?,?,?,?,?)",
                        (mid, code, (r.get("date") or "").strip() or None, full,
                         (r.get("source_url") or "").strip() or None))
                    res.inserted += 1
        conn.commit()
        res.notes = f"{res.inserted} echte Rueckrufe importiert"
        if unmatched:
            res.notes += f"; nicht zugeordnet: {', '.join(sorted(set(unmatched)))}"
        return res


class NhtsaRecallSource(Source):
    name = "nhtsa_recalls"
    live = True
    API = "https://api.nhtsa.gov/recalls/recallsByVehicle?make={mk}&model={md}&modelYear={yr}"

    def __init__(self, fetch=None):
        self._fetch = fetch

    def _get(self, url):
        if self._fetch:
            return self._fetch(url)
        import requests
        return requests.get(url, timeout=8, headers={"User-Agent": "auto-bewertung/0.1"}).text

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        from urllib.parse import quote_plus
        res = CollectResult(source=self.name)
        models = conn.execute("SELECT id, make, model, year_from, year_to FROM car_model").fetchall()
        for m in models:
            key = NHTSA_MODEL.get((m["make"], m["model"]))
            if not key:
                continue
            mk, md = key
            yr = (m["year_from"] or 2018) + 1
            try:
                raw = self._get(self.API.format(mk=quote_plus(mk), md=quote_plus(md), yr=yr))
                data = json.loads(raw)
            except Exception:
                continue
            for rc in data.get("results", []):
                code = rc.get("NHTSACampaignNumber")
                desc = f"{rc.get('Component','')}: {(rc.get('Summary') or '')[:200]}"
                exists = conn.execute(
                    "SELECT 1 FROM recall WHERE model_id=? AND kba_code=?", (m["id"], code)).fetchone()
                if exists:
                    continue
                conn.execute(
                    "INSERT INTO recall(model_id,kba_code,date,description,url) VALUES (?,?,?,?,?)",
                    (m["id"], code, rc.get("ReportReceivedDate"), desc,
                     "https://www.nhtsa.gov/recalls"))
                res.inserted += 1
        conn.commit()
        res.notes = f"{res.inserted} NHTSA-Rueckrufe (US-Markt) ergaenzt"
        return res
