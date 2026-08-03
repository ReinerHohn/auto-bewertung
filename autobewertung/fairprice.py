"""Statistisches Fair-Preis-Modell aus den echten Live-Angeboten.

Statt sich auf das AS24-eigene Preis-Label zu verlassen, schaetzt dieses Modell
fuer JEDES Angebot einen fairen Marktpreis aus seinen Merkmalen und meldet die
Abweichung (Residual) in Euro: "dieser Wagen ist 2.300 EUR unter seinem
statistisch fairen Preis".

Modell: eine globale log-lineare Regression

    log(Preis) = Basis[Modell] + b_alter*Alter + b_km*log1p(km) + b_kw*kW

- Modell-Fixed-Effects (Basiswert je Modell) fangen Marke/Segment/Ausstattung ab.
- Die Steigungen fuer Alter/Laufleistung/Leistung werden ueber ALLE Modelle
  gepoolt -> robust auch bei Modellen mit wenigen Angeboten.
- 2-Pass-robust: nach dem ersten Fit werden grobe Ausreisser (|Residual|>3 sigma,
  z.B. Bastler/Salvage/Tippfehler) verworfen und einmal nachgefittet.

Nur Modelle mit >= MIN_PER_MODEL echten Angeboten bekommen einen Basiswert;
fuer andere gibt es keine Schaetzung (kein Ueberfitten auf 1-2 Punkte).
Benoetigt numpy (kommt mit pandas). Ohne numpy -> gibt None zurueck (Feature aus).
"""
from __future__ import annotations

import math
import sqlite3
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

MIN_PER_MODEL = 4          # weniger Angebote -> kein Basiswert (zu wenig Signal)
PRICE_MIN, PRICE_MAX = 1500, 150000
KM_MAX = 400000
AGE_MAX = 25


@dataclass
class FairEstimate:
    listing_id: int
    model_id: int
    price: float
    fair_price: float
    resid_eur: float       # price - fair_price (negativ = guenstiger als fair)
    resid_pct: float       # resid_eur / fair_price


@dataclass
class FairPriceModel:
    model_ids: list[int]
    base: dict[int, float]     # Modell -> Basis-Log-Preis (Intercept je Modell)
    b_age: float
    b_logkm: float
    b_kw: float
    kw_default: float
    n: int
    r2: float

    def predict(self, model_id: int, age: float, km: float, kw: float | None) -> float | None:
        if model_id not in self.base:
            return None
        log_fair = (self.base[model_id] + self.b_age * age
                    + self.b_logkm * math.log1p(max(0, km))
                    + self.b_kw * (kw if kw else self.kw_default))
        return math.exp(log_fair)


def _feature_rows(conn: sqlite3.Connection) -> list[dict]:
    now_year = datetime.now().year
    out: list[dict] = []
    for r in conn.execute(
        "SELECT id, model_id, price, mileage_km, first_reg, power_kw FROM listing "
        "WHERE active=1 AND price IS NOT NULL AND source!='seed'"):
        price, km, fr = r["price"], r["mileage_km"], r["first_reg"]
        if not price or km is None or not fr:
            continue
        try:
            age = now_year - int(str(fr)[:4])
        except (TypeError, ValueError):
            continue
        if not (PRICE_MIN <= price <= PRICE_MAX) or km < 0 or km > KM_MAX or age < 0 or age > AGE_MAX:
            continue
        out.append({"id": r["id"], "model_id": r["model_id"], "price": float(price),
                    "km": float(km), "age": float(age), "kw": r["power_kw"]})
    return out


def fit(conn: sqlite3.Connection) -> FairPriceModel | None:
    try:
        import numpy as np
    except ModuleNotFoundError:
        return None

    rows = _feature_rows(conn)
    cnt = Counter(r["model_id"] for r in rows)
    keep = {m for m, c in cnt.items() if c >= MIN_PER_MODEL}
    rows = [r for r in rows if r["model_id"] in keep]
    models = sorted(keep)
    M = len(models)
    if M == 0 or len(rows) < M + 4:
        return None
    midx = {m: i for i, m in enumerate(models)}
    kws = [r["kw"] for r in rows if r["kw"]]
    kw_default = float(statistics.median(kws)) if kws else 100.0

    def feats(r):
        base = [0.0] * M
        base[midx[r["model_id"]]] = 1.0
        return base + [r["age"], math.log1p(r["km"]), float(r["kw"] or kw_default)]

    X = np.array([feats(r) for r in rows], dtype=float)
    y = np.array([math.log(r["price"]) for r in rows], dtype=float)

    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    sd = float(resid.std()) or 1.0
    mask = np.abs(resid) <= 3.0 * sd
    if int(mask.sum()) >= M + 4 and int(mask.sum()) < len(rows):
        coef, *_ = np.linalg.lstsq(X[mask], y[mask], rcond=None)
        yv, Xv = y[mask], X[mask]
    else:
        yv, Xv = y, X

    pred = Xv @ coef
    ss_res = float(((yv - pred) ** 2).sum())
    ss_tot = float(((yv - yv.mean()) ** 2).sum()) or 1.0
    r2 = 1.0 - ss_res / ss_tot

    base = {m: float(coef[midx[m]]) for m in models}
    return FairPriceModel(model_ids=models, base=base,
                          b_age=float(coef[M]), b_logkm=float(coef[M + 1]),
                          b_kw=float(coef[M + 2]), kw_default=kw_default,
                          n=len(rows), r2=r2)


def estimate_listings(conn: sqlite3.Connection,
                      model: FairPriceModel | None = None) -> dict[int, FairEstimate]:
    """Fairer Preis + Residual je Angebot (nur fuer Modelle mit Basiswert)."""
    model = model or fit(conn)
    if model is None:
        return {}
    out: dict[int, FairEstimate] = {}
    for r in _feature_rows(conn):
        fair = model.predict(r["model_id"], r["age"], r["km"], r["kw"])
        if not fair:
            continue
        resid = r["price"] - fair
        out[r["id"]] = FairEstimate(
            listing_id=r["id"], model_id=r["model_id"], price=r["price"],
            fair_price=fair, resid_eur=resid, resid_pct=resid / fair)
    return out
