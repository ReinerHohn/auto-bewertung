"""Schnaeppchen-Alarm: erkennt neue Top-Preis-Angebote und Preissenkungen.

Wird am Ende des `track`-Laufs aufgerufen. Neue Alarme werden dedupliziert in der
`alert`-Tabelle gespeichert (Signatur) und zurueckgegeben (fuer Log/Dashboard).
"""
from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timezone

RATING_LABEL = {1: "Sehr guter Preis", 2: "Guter Preis"}


def _add(conn, ts, mid, lid, kind, msg, sig) -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO alert(ts,model_id,listing_id,kind,message,sig) VALUES (?,?,?,?,?,?)",
        (ts, mid, lid, kind, msg, sig))
    return cur.rowcount > 0


def scan_alerts(conn: sqlite3.Connection, since_ts: str) -> list[str]:
    """Sucht neue Schnaeppchen. `since_ts` = Beginn des aktuellen Laufs (ISO)."""
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out: list[str] = []
    _med: dict[int, float] = {}

    def median_price(model_id):
        if model_id not in _med:
            ps = [x[0] for x in conn.execute(
                "SELECT price FROM listing WHERE model_id=? AND active=1 AND price IS NOT NULL",
                (model_id,))]
            _med[model_id] = statistics.median(ps) if ps else None
        return _med[model_id]

    # 1) NEU aufgetauchte Angebote mit Top-Bewertung UND Preis <= Modell-Median
    #    (echtes Schnaeppchen, nicht teure Sport-/Sonderversion mit gutem Preis)
    for r in conn.execute(
        "SELECT l.id, l.model_id, l.price, l.price_rating, l.mileage_km, l.first_reg, l.url, "
        "       cm.make||' '||cm.model AS model "
        "FROM listing l JOIN car_model cm ON cm.id=l.model_id "
        "WHERE l.active=1 AND l.first_seen>=? AND l.price_rating IN (1,2) AND l.price IS NOT NULL "
        "ORDER BY l.price", (since_ts,)):
        med = median_price(r["model_id"])
        if med and r["price"] > med:
            continue
        sig = f"deal:{r['id']}:{int(r['price'])}"
        lbl = RATING_LABEL.get(r["price_rating"], "Top-Preis")
        msg = (f"🟢 {lbl}: {r['model']} – {r['price']:,.0f} €".replace(",", ".")
               + f" · {r['mileage_km'] or '?'} km · EZ {r['first_reg'] or '?'}"
               + (f" · {r['url']}" if r["url"] else ""))
        if _add(conn, ts, r["model_id"], r["id"], "deal", msg, sig):
            out.append(msg)

    # 2) PREISSENKUNGEN auf aktiven Angeboten (letzter Punkt < vorheriger, >=0.5%)
    for r in conn.execute(
        "SELECT l.id, l.model_id, l.url, cm.make||' '||cm.model AS model "
        "FROM listing l JOIN car_model cm ON cm.id=l.model_id "
        "WHERE l.active=1 AND l.price IS NOT NULL AND l.source IN ('autoscout24','watch')"):
        pts = conn.execute("SELECT price FROM price_point WHERE listing_id=? ORDER BY ts DESC LIMIT 2",
                           (r["id"],)).fetchall()
        if len(pts) == 2 and pts[0]["price"] < pts[1]["price"] * 0.995:
            new_p, old_p = pts[0]["price"], pts[1]["price"]
            sig = f"drop:{r['id']}:{int(new_p)}"
            msg = (f"📉 Preis gefallen: {r['model']} {old_p:,.0f} → {new_p:,.0f} €".replace(",", ".")
                   + f" (−{old_p-new_p:,.0f} €)".replace(",", ".")
                   + (f" · {r['url']}" if r["url"] else ""))
            if _add(conn, ts, r["model_id"], r["id"], "drop", msg, sig):
                out.append(msg)

    conn.commit()
    return out
