"""Verschleiss-/Reparaturkosten als Funktion der Laufleistung.

Aus den wear_item-Daten (welches Teil bei wie viel km, mit Kosten) ergibt sich:
- eine Kostenkurve: kumulierte Reparaturkosten ueber die Laufleistung
- die im eigenen Halte-Fenster erwarteten Reparaturkosten -> fliesst in die TCO

Ein Teil mit interval_km=0 faellt einmalig bei at_km an; mit interval_km>0
wiederholt es sich (at_km, at_km+interval, ...).
"""
from __future__ import annotations

import sqlite3


def _occurrences(at_km: int, interval_km: int, km: float) -> int:
    """Wie oft ist ein Teil bis Laufleistung `km` faellig geworden?"""
    if km < at_km:
        return 0
    if not interval_km:
        return 1
    return 1 + int((km - at_km) // interval_km)


def load_items(conn: sqlite3.Connection, model_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT component, variant, at_km, interval_km, cost_eur, note, source "
        "FROM wear_item WHERE model_id=? ORDER BY at_km", (model_id,))]


def upcoming_from_items(items: list[dict], start_km: float, span_km: float) -> list[dict]:
    """Wie upcoming_items, aber auf einer bereits (z.B. nach Variante) gefilterten Liste."""
    out = []
    for it in items:
        before = _occurrences(it["at_km"], it["interval_km"], start_km)
        after = _occurrences(it["at_km"], it["interval_km"], start_km + span_km)
        n = after - before
        if n > 0:
            out.append({**it, "faellig_im_fenster": n, "kosten_im_fenster": n * it["cost_eur"]})
    return sorted(out, key=lambda x: -x["kosten_im_fenster"])


def cumulative_cost(items: list[dict], km: float) -> float:
    """Kumulierte Reparaturkosten bis Laufleistung `km`."""
    return sum(_occurrences(it["at_km"], it["interval_km"], km) * it["cost_eur"]
               for it in items)


def cost_curve(conn, model_id: int, max_km: int = 250000, step: int = 5000):
    """Liste (km, kumulierte_kosten) fuer die Kurve."""
    items = load_items(conn, model_id)
    return [(km, cumulative_cost(items, km)) for km in range(0, max_km + 1, step)]


def expected_repair_per_year(conn, model_id: int, start_km: float,
                             annual_km: int, holding_years: int) -> float:
    """Erwartete Reparaturkosten pro Jahr im eigenen Halte-Fenster.

    = (kumulierte Kosten bis start+Fahrleistung) - (bis start), auf Jahre umgelegt.
    """
    items = load_items(conn, model_id)
    if not items or holding_years <= 0:
        return 0.0
    end_km = start_km + annual_km * holding_years
    delta = cumulative_cost(items, end_km) - cumulative_cost(items, start_km)
    return max(0.0, delta) / holding_years


def upcoming_items(conn, model_id: int, start_km: float, span_km: float) -> list[dict]:
    """Teile, die im Fenster [start, start+span] faellig werden (fuer die Anzeige)."""
    out = []
    for it in load_items(conn, model_id):
        before = _occurrences(it["at_km"], it["interval_km"], start_km)
        after = _occurrences(it["at_km"], it["interval_km"], start_km + span_km)
        n = after - before
        if n > 0:
            out.append({**it, "faellig_im_fenster": n, "kosten_im_fenster": n * it["cost_eur"]})
    return sorted(out, key=lambda x: -x["kosten_im_fenster"])
