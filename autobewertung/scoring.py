"""Bewertungs-/Ranking-Engine.

Aggregiert je Modell (und je Angebot) die Rohdaten aus der DB zu sechs
0..100-Kennzahlen und bildet daraus einen gewichteten Gesamtscore nach den
Nutzer-Kriterien. Normalisierung erfolgt relativ zum aktuellen Datenbestand
(Perzentil-/Min-Max-Skalierung), damit "gut/schlecht" immer im Marktkontext steht.
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field

from .config import DIMENSIONS, Criteria


@dataclass
class ModelScore:
    model_id: int
    label: str
    dims: dict[str, float] = field(default_factory=dict)   # 0..100 je Dimension
    total: float = 0.0
    n_listings: int = 0
    best_deal_eur: float | None = None
    best_deal_discount_pct: float | None = None
    details: dict = field(default_factory=dict)


def _minmax(values: dict[int, float], invert: bool = False) -> dict[int, float]:
    """Skaliert Rohwerte auf 0..100. invert=True -> kleiner Rohwert = besser."""
    vals = [v for v in values.values() if v is not None]
    if not vals:
        return {}
    lo, hi = min(vals), max(vals)
    span = hi - lo
    out: dict[int, float] = {}
    for k, v in values.items():
        if v is None:
            continue
        s = 50.0 if span == 0 else (v - lo) / span * 100.0
        out[k] = (100.0 - s) if invert else s
    return out


# ---------------------------------------------------------------------------
# Roh-Aggregationen je Modell aus der DB
# ---------------------------------------------------------------------------

def _reliability_raw(conn) -> dict[int, float]:
    """Kleiner = besser: gemittelter Pannen-/Maengel-Rohwert je Modell."""
    out: dict[int, float] = {}
    rows = conn.execute(
        "SELECT model_id, metric, AVG(value) v FROM reliability_stat GROUP BY model_id, metric"
    ).fetchall()
    tmp: dict[int, list[float]] = {}
    for r in rows:
        # Metriken grob auf vergleichbare Skala bringen
        v = r["v"]
        if r["metric"] == "maengelquote_pct":
            v = v * 1.0
        elif r["metric"] == "pannen_pro_1000":
            v = v * 1.0
        tmp.setdefault(r["model_id"], []).append(v)
    for mid, vs in tmp.items():
        out[mid] = statistics.mean(vs)
    return out


def _weakpoint_raw(conn) -> dict[int, float]:
    """Kleiner = besser: gewichtete Summe Schwachstellen (nach Schwere) + Rueckrufe."""
    out: dict[int, float] = {}
    for r in conn.execute(
        "SELECT model_id, SUM(severity) s FROM weak_point GROUP BY model_id"
    ):
        out[r["model_id"]] = float(r["s"] or 0)
    for r in conn.execute(
        "SELECT model_id, COUNT(*) c FROM recall GROUP BY model_id"
    ):
        out[r["model_id"]] = out.get(r["model_id"], 0.0) + 2.0 * float(r["c"])
    return out


def _repaircost_raw(conn) -> dict[int, float]:
    """Kleiner = besser: normalisierte jaehrliche Kosten je Modell."""
    out: dict[int, float] = {}
    rows = conn.execute("SELECT model_id, category, typical_eur, period FROM repair_cost").fetchall()
    tmp: dict[int, float] = {}
    for r in rows:
        eur = r["typical_eur"] or 0.0
        # auf grobe Jahreskosten umlegen
        if r["period"] == "pro_intervall":
            eur = eur / 2.0          # Annahme: ~2-Jahres-Intervall
        elif r["period"] == "einmalig":
            eur = eur / 8.0          # ueber angenommene Haltedauer strecken
        tmp[r["model_id"]] = tmp.get(r["model_id"], 0.0) + eur
    out.update(tmp)
    return out


def _parts_raw(conn) -> dict[int, float]:
    """Groesser = besser: Verfuegbarkeits-Score je Modell."""
    out: dict[int, float] = {}
    for r in conn.execute(
        "SELECT model_id, AVG(score) s FROM parts_availability GROUP BY model_id"
    ):
        if r["s"] is not None:
            out[r["model_id"]] = r["s"]
    return out


def _workshop_raw(conn, home_plz: str | None) -> dict[int, float]:
    """Groesser = besser: Werkstattdichte je Marke (optional PLZ-Naehe gewichtet)."""
    # Mapping model_id -> make
    makes = {r["id"]: r["make"] for r in conn.execute("SELECT id, make FROM car_model")}
    counts: dict[str, float] = {}
    for r in conn.execute("SELECT make, plz, specialized FROM workshop"):
        mk = r["make"]
        w = 1.0 + (0.5 if r["specialized"] else 0.0)
        if home_plz and r["plz"] and r["plz"][:2] == home_plz[:2]:
            w += 1.0                 # gleiche PLZ-Region -> Bonus
        counts[mk] = counts.get(mk, 0.0) + w
        counts[None] = counts.get(None, 0.0)  # freie Werkstaetten zaehlen fuer alle
    free = sum(v for k, v in counts.items() if k is None)
    out: dict[int, float] = {}
    for mid, mk in makes.items():
        out[mid] = counts.get(mk, 0.0) + 0.25 * free
    return out


def _price_value(conn, crit: Criteria):
    """Schnaeppchen-Dimension: pro Modell bestes Angebot vs. Modell-Median + Trend.

    Rueckgabe: (raw_score dict, meta dict mit best-deal Infos je Modell).
    Groesser = besser.
    """
    listings = conn.execute(
        "SELECT id, model_id, price, mileage_km, first_reg FROM listing WHERE active=1 AND price IS NOT NULL"
    ).fetchall()
    by_model: dict[int, list[sqlite3.Row]] = {}
    for r in listings:
        if crit.max_price and r["price"] > crit.max_price:
            continue
        if crit.max_mileage_km and (r["mileage_km"] or 0) > crit.max_mileage_km:
            continue
        by_model.setdefault(r["model_id"], []).append(r)

    raw: dict[int, float] = {}
    meta: dict[int, dict] = {}
    for mid, rows in by_model.items():
        prices = [r["price"] for r in rows]
        median = statistics.median(prices)
        best = min(rows, key=lambda r: r["price"])
        discount = (median - best["price"]) / median * 100.0 if median else 0.0
        # Preistrend des besten Angebots (fallend = gut)
        trend = _price_trend(conn, best["id"])
        score = discount + max(0.0, -trend) * 2.0    # fallender Trend verstaerkt
        raw[mid] = score
        meta[mid] = {
            "best_listing_id": best["id"],
            "best_price": best["price"],
            "median_price": median,
            "discount_pct": round(discount, 1),
            "trend_pct_per_week": round(trend, 2),
            "n": len(rows),
        }
    return raw, meta


def _price_trend(conn, listing_id: int) -> float:
    """Preis-Trend in %/Woche ueber die Historie (negativ = faellt)."""
    pts = conn.execute(
        "SELECT ts, price FROM price_point WHERE listing_id=? ORDER BY ts", (listing_id,)
    ).fetchall()
    if len(pts) < 2:
        return 0.0
    first, last = pts[0], pts[-1]
    if first["price"] in (None, 0):
        return 0.0
    # grobe lineare Naeherung ueber Anzahl Punkte (1 Punkt ~ 1 Sample)
    weeks = max(1, len(pts) - 1)
    return (last["price"] - first["price"]) / first["price"] * 100.0 / weeks


# ---------------------------------------------------------------------------
# Hauptfunktion
# ---------------------------------------------------------------------------

def score_models(conn: sqlite3.Connection, crit: Criteria) -> list[ModelScore]:
    weights = crit.normalized_weights()

    models = {r["id"]: r for r in conn.execute("SELECT * FROM car_model")}

    pv_raw, pv_meta = _price_value(conn, crit)
    dim_raw = {
        "price_value":        (pv_raw, False),
        "reliability":        (_reliability_raw(conn), True),
        "weak_points":        (_weakpoint_raw(conn), True),
        "repair_cost":        (_repaircost_raw(conn), True),
        "parts_availability": (_parts_raw(conn), False),
        "workshop_access":    (_workshop_raw(conn, crit.home_plz), False),
    }
    dim_scaled = {d: _minmax(raw, invert=inv) for d, (raw, inv) in dim_raw.items()}

    results: list[ModelScore] = []
    for mid, m in models.items():
        # Modelle ohne jedes Angebot ueberspringen, wenn Preisfilter aktiv sind
        gen = f" {m['generation']}" if m["generation"] else ""
        label = f"{m['make']} {m['model']}{gen}"
        dims = {}
        for d in DIMENSIONS:
            dims[d] = dim_scaled[d].get(mid, 50.0)   # fehlend -> neutral 50
        total = sum(dims[d] * weights[d] for d in DIMENSIONS)
        pm = pv_meta.get(mid, {})
        results.append(ModelScore(
            model_id=mid,
            label=label,
            dims={k: round(v, 1) for k, v in dims.items()},
            total=round(total, 1),
            n_listings=pm.get("n", 0),
            best_deal_eur=pm.get("best_price"),
            best_deal_discount_pct=pm.get("discount_pct"),
            details={**{k: m[k] for k in m.keys()}, "price": pm},
        ))
    results.sort(key=lambda r: r.total, reverse=True)
    return results
