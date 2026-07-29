"""Inserate-Adapter (mobile.de / AutoScout24) - Geruest.

WICHTIG (legal/pragmatisch): Beide Portale untersagen in ihren AGB das
automatisierte Auslesen und setzen Bot-Schutz ein. Deshalb ist dieser Adapter
bewusst als Geruest angelegt und standardmaessig AUS (`live = False`). Optionen,
um ihn sauber scharfzuschalten:

  1. Offizielle Wege: mobile.de / AutoScout24 bieten Partner-/Haendler-APIs.
     Wer Zugang hat, fuellt `fetch_via_api()`.
  2. RSS/Such-Export oder manueller CSV-Import (siehe `import_csv`), z.B. eigene
     Merklisten exportieren.
  3. Eigenverantwortliches, robots-konformes Abrufen EINZELNER, dir bekannter
     Inserats-URLs mit strengem Rate-Limit (polite_get) - kein Massen-Scraping.

Der Adapter normalisiert alles in die `listing`/`price_point`-Tabellen und
aktualisiert bei Wiederholungslauf den Preisverlauf -> daraus entsteht die
Schnaeppchen-Erkennung.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .base import CollectResult, Source, now_iso


def _record_listing(conn, *, model_id, source, source_ref, title, price,
                    mileage_km=None, first_reg=None, plz=None, location=None,
                    url=None, power_kw=None) -> str:
    """Legt Angebot an bzw. aktualisiert es und schreibt einen Preis-Punkt."""
    now = now_iso()
    existing = conn.execute(
        "SELECT id FROM listing WHERE source=? AND source_ref=?", (source, source_ref)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE listing SET price=?, mileage_km=?, last_seen=?, active=1 WHERE id=?",
            (price, mileage_km, now, existing["id"]))
        lid = existing["id"]
        action = "updated"
    else:
        cur = conn.execute(
            "INSERT INTO listing(model_id,source,source_ref,title,price,mileage_km,"
            "first_reg,power_kw,location,plz,url,first_seen,last_seen,active)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (model_id, source, source_ref, title, price, mileage_km, first_reg,
             power_kw, location, plz, url, now, now))
        lid = cur.lastrowid
        action = "inserted"
    if price is not None:
        conn.execute(
            "INSERT OR REPLACE INTO price_point(listing_id,ts,price) VALUES (?,?,?)",
            (lid, now, price))
    return action


class InserateSource(Source):
    name = "inserate"
    live = False   # bewusst aus, bis du einen Bezugsweg konfigurierst

    def __init__(self, csv_path: Path | str | None = None):
        self.csv_path = Path(csv_path) if csv_path else None

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        res = CollectResult(source=self.name)
        if self.csv_path and self.csv_path.exists():
            self.import_csv(conn, self.csv_path, res)
            conn.commit()
        else:
            res.notes = ("Geruest aktiv, keine Datenquelle konfiguriert. "
                         "CSV via --inserate-csv oder Partner-API einbauen.")
        return res

    # --- Bezugsweg 1: manueller/exportierter CSV-Import ---------------------
    def import_csv(self, conn, path: Path, res: CollectResult) -> None:
        """CSV-Spalten: make,model,generation,source,source_ref,title,price,
        mileage_km,first_reg,plz,location,url"""
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                mid = self._find_model(conn, row["make"], row["model"],
                                       row.get("generation") or None)
                if mid is None:
                    from ..db import upsert_model
                    mid = upsert_model(conn, row["make"], row["model"],
                                       row.get("generation") or None)
                action = _record_listing(
                    conn, model_id=mid, source=row.get("source", "csv"),
                    source_ref=row["source_ref"], title=row.get("title"),
                    price=float(row["price"]) if row.get("price") else None,
                    mileage_km=int(row["mileage_km"]) if row.get("mileage_km") else None,
                    first_reg=row.get("first_reg"), plz=row.get("plz"),
                    location=row.get("location"), url=row.get("url"))
                setattr(res, action, getattr(res, action) + 1)

    # --- Bezugsweg 2: offizielle API (Platzhalter) -------------------------
    def fetch_via_api(self, conn) -> None:
        raise NotImplementedError(
            "Partner-/Haendler-API-Zugang hier einbinden (mobile.de Search API o.ae.).")
