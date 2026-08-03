"""Modell-weites Preis-Tracking und Zuordnungs-Verifikation.

- `snapshot_model_prices`: schreibt bei jedem Lauf je Modell einen Marktpreis-
  Punkt (Median/Min/Max ueber alle aktiven Angebote). Ueber die Zeit entsteht so
  pro Modell ein Preisverlauf - fuer ALLE Modelle mit Angeboten, nicht nur
  einzelne URLs.
- `assignment_report`: zeigt, welchem Modell jedes Angebot zugeordnet ist
  (zur Verifikation der Zuordnung).
"""
from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timedelta, timezone


def deactivate_stale(conn: sqlite3.Connection, max_age_days: int = 10) -> int:
    """Angebote, die seit `max_age_days` in keinem Lauf mehr gesehen wurden
    (= verkauft / vom Markt genommen), auf active=0 setzen.

    Sie bleiben in der DB und fuettern weiter das Fair-Preis-Modell (ihr letzter
    Preis ist echtes Marktsignal), tauchen aber nicht mehr als KAUFBARES Angebot
    oder Schnaeppchen auf. Nur Scraper-Quellen; verfolgte URLs (watch) + Seed
    bleiben unangetastet.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat(timespec="seconds")
    cur = conn.execute(
        "UPDATE listing SET active=0 WHERE active=1 "
        "AND source IN ('autoscout24','kleinanzeigen') AND last_seen < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def snapshot_model_prices(conn: sqlite3.Connection) -> int:
    """Ein Marktpreis-Snapshot je Modell (nur bei Aenderung). Gibt Anzahl neuer Punkte."""
    ts = datetime.now(timezone.utc).isoformat()
    by_model: dict[int, list[float]] = {}
    for r in conn.execute(
            "SELECT model_id, price FROM listing "
            "WHERE active=1 AND price IS NOT NULL AND model_id IS NOT NULL"):
        by_model.setdefault(r["model_id"], []).append(r["price"])

    written = 0
    for mid, prices in by_model.items():
        med = statistics.median(prices)
        last = conn.execute(
            "SELECT median_price FROM model_price_snapshot WHERE model_id=? "
            "ORDER BY ts DESC LIMIT 1", (mid,)).fetchone()
        if last and last["median_price"] == med:
            continue                       # keine Aenderung -> nicht zuspammen
        conn.execute(
            "INSERT INTO model_price_snapshot(model_id,ts,median_price,min_price,max_price,n)"
            " VALUES (?,?,?,?,?,?)",
            (mid, ts, med, min(prices), max(prices), len(prices)))
        written += 1
    conn.commit()
    return written


def assignment_report(conn: sqlite3.Connection) -> list[dict]:
    """Je Angebot: Quelle, Titel/Preis und zugeordnetes Modell (fuer Verifikation)."""
    rows = conn.execute(
        "SELECT l.id, l.source, l.title, l.price, l.model_id, "
        "       cm.make, cm.model, cm.generation "
        "FROM listing l LEFT JOIN car_model cm ON cm.id=l.model_id "
        "WHERE l.active=1 ORDER BY l.source, cm.make, cm.model").fetchall()
    out = []
    for r in rows:
        assigned = (f"{r['make']} {r['model']}"
                    + (f" ({r['generation']})" if r["generation"] else "")) if r["model_id"] else "— (nicht zugeordnet)"
        out.append({
            "listing_id": r["id"], "source": r["source"], "title": r["title"],
            "price": r["price"], "model_id": r["model_id"], "assigned": assigned,
            "ok": r["model_id"] is not None and r["make"] != "Unbekannt",
        })
    return out
