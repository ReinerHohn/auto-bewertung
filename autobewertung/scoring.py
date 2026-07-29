"""Bewertungs-/Ranking-Engine.

Aggregiert je Modell die Rohdaten aus der DB, berechnet die komplette
Total Cost of Ownership (TCO) und bildet daraus einen gewichteten Gesamtscore
nach den Nutzer-Kriterien.

Zusaetzlich werden harte Kriterien angewendet:
- Fahrzeugklasse ab `min_vehicle_class` (z.B. Kompakt / Golf/Auris) aufwaerts
- Budget `max_price` fuer Verbrenner
- EV-Ausnahme: E-Autos duerfen teurer sein, soweit ihre jaehrliche Ersparnis
  gegenueber dem Verbrenner-Median ueber die Haltedauer den Aufpreis deckt
- EV-Schnelllade-Pflicht: `ev_min_charge_km_30min` (km nachladbar in 30 min)

Nicht qualifizierte Modelle landen mit Begruendung in `RankResult.excluded`.
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field

from .config import DIMENSIONS, Criteria
from .tco import TcoResult, class_rank, compute_tco, ice_reference_running


@dataclass
class ModelScore:
    model_id: int
    label: str
    drivetrain: str | None = None
    vehicle_class: str | None = None
    dims: dict[str, float] = field(default_factory=dict)   # 0..100 je Dimension
    total: float = 0.0
    n_listings: int = 0
    purchase_price: float | None = None
    best_deal_discount_pct: float | None = None
    annual_tco: float | None = None
    tco_breakdown: dict[str, float] = field(default_factory=dict)
    resale_value: float | None = None
    km_per_30min: float | None = None
    range_km: float | None = None
    ev_savings_year: float | None = None     # ggü. Verbrenner-Median (nur EV)
    allowed_price: float | None = None
    details: dict = field(default_factory=dict)


@dataclass
class ExcludedModel:
    label: str
    reason: str


@dataclass
class RankResult:
    ranked: list[ModelScore] = field(default_factory=list)
    excluded: list[ExcludedModel] = field(default_factory=list)

    def __iter__(self):          # bequemes Iterieren ueber das Ranking
        return iter(self.ranked)

    def __len__(self):
        return len(self.ranked)

    def __getitem__(self, i):
        return self.ranked[i]


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
    tmp: dict[int, list[float]] = {}
    for r in conn.execute(
        "SELECT model_id, metric, AVG(value) v FROM reliability_stat GROUP BY model_id, metric"
    ):
        tmp.setdefault(r["model_id"], []).append(r["v"])
    return {mid: statistics.mean(vs) for mid, vs in tmp.items()}


def _weakpoint_raw(conn) -> dict[int, float]:
    out: dict[int, float] = {}
    for r in conn.execute("SELECT model_id, SUM(severity) s FROM weak_point GROUP BY model_id"):
        out[r["model_id"]] = float(r["s"] or 0)
    for r in conn.execute("SELECT model_id, COUNT(*) c FROM recall GROUP BY model_id"):
        out[r["model_id"]] = out.get(r["model_id"], 0.0) + 2.0 * float(r["c"])
    return out


def _equipment_raw(spec, crit: Criteria) -> float:
    """Ausstattungs-Rohwert: vorhandene Wunsch-Features minus Matrix-Malus."""
    if spec is None:
        return 0.0
    avail = set((spec["features"] or "").split(",")) if spec["features"] else set()
    have = sum(1 for f in crit.want_features if f in avail)
    penalty = 1.5 if (crit.avoid_matrix and spec["has_matrix"]) else 0.0
    return have - penalty


def _maintenance_year(conn) -> dict[int, float]:
    """Jaehrliche Wartungs-/Reparaturkosten je Modell (fuer TCO wiederverwendet)."""
    out: dict[int, float] = {}
    for r in conn.execute("SELECT model_id, category, typical_eur, period FROM repair_cost"):
        if (r["category"] or "").lower().startswith("versicherung"):
            continue  # Versicherung kommt aus vehicle_spec.insurance_eur (kein Doppelzaehlen)
        eur = r["typical_eur"] or 0.0
        if r["period"] == "pro_intervall":
            eur = eur / 2.0
        elif r["period"] == "einmalig":
            eur = eur / 8.0
        out[r["model_id"]] = out.get(r["model_id"], 0.0) + eur
    return out


def _parts_raw(conn) -> dict[int, float]:
    out: dict[int, float] = {}
    for r in conn.execute("SELECT model_id, AVG(score) s FROM parts_availability GROUP BY model_id"):
        if r["s"] is not None:
            out[r["model_id"]] = r["s"]
    return out


def _workshop_raw(conn, home_plz: str | None) -> dict[int, float]:
    makes = {r["id"]: r["make"] for r in conn.execute("SELECT id, make FROM car_model")}
    counts: dict[str | None, float] = {}
    for r in conn.execute("SELECT make, plz, specialized FROM workshop"):
        w = 1.0 + (0.5 if r["specialized"] else 0.0)
        if home_plz and r["plz"] and r["plz"][:2] == home_plz[:2]:
            w += 1.0
        counts[r["make"]] = counts.get(r["make"], 0.0) + w
    free = sum(v for k, v in counts.items() if k is None)
    return {mid: counts.get(mk, 0.0) + 0.25 * free for mid, mk in makes.items()}


def _price_trend(conn, listing_id: int) -> float:
    pts = conn.execute(
        "SELECT price FROM price_point WHERE listing_id=? ORDER BY ts", (listing_id,)
    ).fetchall()
    if len(pts) < 2 or not pts[0]["price"]:
        return 0.0
    weeks = max(1, len(pts) - 1)
    return (pts[-1]["price"] - pts[0]["price"]) / pts[0]["price"] * 100.0 / weeks


def _listings_by_model(conn, crit: Criteria):
    """Aktive Angebote je Modell, gefiltert nach km/Baujahr. Kein Preisfilter hier."""
    rows = conn.execute(
        "SELECT id, model_id, price, mileage_km, first_reg FROM listing "
        "WHERE active=1 AND price IS NOT NULL"
    ).fetchall()
    out: dict[int, list] = {}
    for r in rows:
        if crit.max_mileage_km and (r["mileage_km"] or 0) > crit.max_mileage_km:
            continue
        if crit.min_year and r["first_reg"]:
            try:
                if int(r["first_reg"][:4]) < crit.min_year:
                    continue
            except ValueError:
                pass
        out.setdefault(r["model_id"], []).append(r)
    return out


# ---------------------------------------------------------------------------
# Hauptfunktion
# ---------------------------------------------------------------------------

def score_models(conn: sqlite3.Connection, crit: Criteria) -> RankResult:
    weights = crit.normalized_weights()
    models = {r["id"]: r for r in conn.execute("SELECT * FROM car_model")}
    specs = {r["model_id"]: r for r in conn.execute("SELECT * FROM vehicle_spec")}
    maint = _maintenance_year(conn)
    listings = _listings_by_model(conn, crit)

    # --- Kaufpreis + Deal je Modell -----------------------------------------
    price_meta: dict[int, dict] = {}
    for mid, model in models.items():
        rows = listings.get(mid, [])
        spec = specs.get(mid)
        typical = spec["typical_price"] if spec else None
        if rows:
            prices = [r["price"] for r in rows]
            median = statistics.median(prices)
            best = min(rows, key=lambda r: r["price"])
            discount = (median - best["price"]) / median * 100.0 if median else 0.0
            trend = _price_trend(conn, best["id"])
            price_meta[mid] = {
                "purchase": best["price"], "median": median, "n": len(rows),
                "discount": discount, "deal_score": discount + max(0.0, -trend) * 2.0,
                "best_id": best["id"],
            }
        elif typical:
            price_meta[mid] = {"purchase": typical, "median": typical, "n": 0,
                               "discount": 0.0, "deal_score": 0.0, "best_id": None}
        else:
            price_meta[mid] = {"purchase": None, "median": None, "n": 0,
                               "discount": None, "deal_score": 0.0, "best_id": None}

    # --- TCO je Modell -------------------------------------------------------
    tco: dict[int, TcoResult] = {}
    for mid, spec in specs.items():
        purchase = price_meta.get(mid, {}).get("purchase")
        if purchase is None:
            continue
        tco[mid] = compute_tco(spec, purchase, maint.get(mid, 500.0), crit.tco)
    ice_ref = ice_reference_running(tco)

    # --- Qualifikation (harte Kriterien) ------------------------------------
    qualified: list[int] = []
    excluded: list[ExcludedModel] = []
    for mid, model in models.items():
        spec = specs.get(mid)
        gen = f" {model['generation']}" if model["generation"] else ""
        label = f"{model['make']} {model['model']}{gen}"
        pm = price_meta[mid]
        purchase = pm["purchase"]

        if purchase is None:
            excluded.append(ExcludedModel(label, "kein Preis/Angebot vorhanden"))
            continue

        # Klassenfilter
        if crit.min_vehicle_class and spec:
            if class_rank(spec["vehicle_class"]) < class_rank(crit.min_vehicle_class):
                excluded.append(ExcludedModel(
                    label, f"Klasse {spec['vehicle_class']} < {crit.min_vehicle_class}"))
                continue

        is_ev = bool(spec and (spec["drivetrain"] or "").lower() == "elektro")

        # EV-Schnelllade-Pflicht
        if is_ev and crit.ev_min_charge_km_30min:
            kmh = (spec["km_per_30min"] or 0) if spec else 0
            if kmh < crit.ev_min_charge_km_30min:
                rng = (spec["range_km"] or 0) if spec else 0
                excluded.append(ExcludedModel(
                    label, f"laedt nur {kmh:.0f} km in 30 min nach "
                           f"(Ziel >={crit.ev_min_charge_km_30min:.0f}); Reichweite {rng:.0f} km ok"))
                continue

        # Budget (mit EV-Ausnahme)
        allowed = crit.max_price
        savings = None
        if crit.max_price is not None:
            if is_ev and crit.ev_price_exception and mid in tco:
                savings = ice_ref - tco[mid].running_year   # €/Jahr gespart
                allowed = crit.max_price + max(0.0, savings) * crit.tco.holding_years
            if allowed is not None and purchase > allowed:
                if is_ev and savings is not None:
                    reason = (f"{purchase:.0f}€ > erlaubt {allowed:.0f}€ "
                              f"(Ersparnis {savings:.0f}€/J deckt Aufpreis nicht)")
                else:
                    reason = f"{purchase:.0f}€ > Budget {crit.max_price:.0f}€"
                excluded.append(ExcludedModel(label, reason))
                continue

        qualified.append(mid)
        pm["allowed"] = allowed
        pm["ev_savings"] = savings

    # --- Dimensionen nur ueber qualifizierte Modelle normalisieren ----------
    q = set(qualified)
    dim_raw = {
        "tco":               ({mid: tco[mid].annual_total for mid in q if mid in tco}, True),
        "value_stability":   ({mid: specs[mid]["depr_pct_year"] for mid in q
                               if specs.get(mid) and specs[mid]["depr_pct_year"] is not None}, True),
        "equipment":         ({mid: _equipment_raw(specs.get(mid), crit) for mid in q}, False),
        "price_value":       ({mid: price_meta[mid]["deal_score"] for mid in q}, False),
        "reliability":       ({mid: v for mid, v in _reliability_raw(conn).items() if mid in q}, True),
        "weak_points":       ({mid: v for mid, v in _weakpoint_raw(conn).items() if mid in q}, True),
        "parts_availability":({mid: v for mid, v in _parts_raw(conn).items() if mid in q}, False),
        "workshop_access":   ({mid: v for mid, v in _workshop_raw(conn, crit.home_plz).items() if mid in q}, False),
    }
    dim_scaled = {d: _minmax(raw, invert=inv) for d, (raw, inv) in dim_raw.items()}

    results: list[ModelScore] = []
    for mid in qualified:
        model = models[mid]
        spec = specs.get(mid)
        pm = price_meta[mid]
        gen = f" {model['generation']}" if model["generation"] else ""
        dims = {d: dim_scaled[d].get(mid, 50.0) for d in DIMENSIONS}
        total = sum(dims[d] * weights[d] for d in DIMENSIONS)
        t = tco.get(mid)
        results.append(ModelScore(
            model_id=mid,
            label=f"{model['make']} {model['model']}{gen}",
            drivetrain=spec["drivetrain"] if spec else None,
            vehicle_class=spec["vehicle_class"] if spec else None,
            dims={k: round(v, 1) for k, v in dims.items()},
            total=round(total, 1),
            n_listings=pm["n"],
            purchase_price=pm["purchase"],
            best_deal_discount_pct=round(pm["discount"], 1) if pm["discount"] is not None else None,
            annual_tco=round(t.annual_total) if t else None,
            tco_breakdown={k: round(v) for k, v in t.breakdown_year.items()} if t else {},
            resale_value=round(t.resale_value) if t else None,
            km_per_30min=spec["km_per_30min"] if spec else None,
            range_km=spec["range_km"] if spec else None,
            ev_savings_year=round(pm["ev_savings"]) if pm.get("ev_savings") is not None else None,
            allowed_price=round(pm.get("allowed")) if pm.get("allowed") is not None else None,
        ))
    results.sort(key=lambda r: r.total, reverse=True)
    return RankResult(ranked=results, excluded=excluded)
