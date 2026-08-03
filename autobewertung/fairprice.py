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
import re
import sqlite3
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

MIN_PER_MODEL = 4          # weniger Angebote -> kein Basiswert (zu wenig Signal)
FIT_WINDOW_DAYS = 120      # verkaufte Angebote bis hierher zaehlen als Marktdaten

# Ausstattungs-/Trim-Signale aus dem Inserats-Titel. Erklaeren viel Preisvarianz
# INNERHALB eines Modells -> trennen 'guenstig weil nackt' von echtem Schnaeppchen.
TITLE_FLAGS = {
    "automatik": r"automatik|automat\b|\bdsg\b|tiptronic|s-?tronic|steptronic|\bpdk\b|\bat\b",
    "leder": r"leder|nappa|alcantara",
    "navi": r"navi|navigation|mmi|comand",
    "ahk": r"\bahk\b|anh[aä]ngerkupplung|anhaengerkupplung",
    "panorama": r"panorama|\bpano\b|schiebedach|glasdach|sky",
    "allrad": r"4x4|4motion|quattro|allrad|\bawd\b|xdrive|4matic|\b4wd\b|4drive",
    "sport": r"\bgti\b|\bgtd\b|\bgte\b|r-?line|s-?line|n-?line|st-?line|\bamg\b|m-?sport|"
             r"\bvz\b|cupra|\bgt\b|\br\b\s|\bs3\b|\bs4\b|\brs\b",
    "vollausstattung": r"vollausst|voll ausgestattet|highline|\bstyle\b|titanium|elegance|"
                       r"\bgt-?line\b|\bxcellence\b|top ausstattung|\bfr\b",
}
FLAG_ORDER = sorted(TITLE_FLAGS)


def _title_flags(title: str | None) -> set[str]:
    t = (title or "").lower()
    return {k for k, rx in TITLE_FLAGS.items() if re.search(rx, t)}


def _title_power_kw(title: str | None) -> int | None:
    """Leistung aus dem Titel (KA liefert kein kW-Feld). kW bevorzugt, sonst PS."""
    t = (title or "").lower()
    m = re.search(r"(\d{2,3})\s*kw", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{2,3})\s*ps", t)
    if m:
        return round(int(m.group(1)) / 1.36)
    return None
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
    km: float | None = None
    age: float | None = None


# Plausibles Schnaeppchen-Band: unter fair, aber ohne Rand-Artefakte des Modells
# (uralte Vielfahrer + fast neue Basisversionen werden systematisch ueberbewertet).
DEAL_BAND = (-0.35, -0.08)      # resid_pct zwischen -35 % und -8 %
DEAL_MIN_EUR = 700              # mind. 700 EUR unter fair
DEAL_KM_MIN, DEAL_KM_MAX = 5000, 200000
DEAL_AGE_MAX = 15


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
    b_equip: dict[str, float] = field(default_factory=dict)   # Ausstattungs-Aufschlaege (log)

    def predict(self, model_id: int, age: float, km: float, kw: float | None,
                flags: set[str] | None = None) -> float | None:
        if model_id not in self.base:
            return None
        log_fair = (self.base[model_id] + self.b_age * age
                    + self.b_logkm * math.log1p(max(0, km))
                    + self.b_kw * (kw if kw else self.kw_default)
                    + sum(self.b_equip.get(f, 0.0) for f in (flags or ())))
        return math.exp(log_fair)


def _feature_rows(conn: sqlite3.Connection, include_sold: bool = False) -> list[dict]:
    """Merkmalszeilen fuer das Modell. include_sold=True nimmt auch kuerzlich
    VERKAUFTE (inaktive) Angebote mit – deren letzter Preis ist echtes Marktsignal
    und macht die Regression robuster. Fuer kaufbare Deals dagegen nur active=1."""
    now_year = datetime.now().year
    if include_sold:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=FIT_WINDOW_DAYS)).isoformat(timespec="seconds")
        where, params = ("price IS NOT NULL AND source!='seed' AND (active=1 OR last_seen>=?)", (cutoff,))
    else:
        where, params = ("active=1 AND price IS NOT NULL AND source!='seed'", ())
    out: list[dict] = []
    for r in conn.execute(
        "SELECT id, model_id, price, mileage_km, first_reg, power_kw, title FROM listing "
        f"WHERE {where}", params):
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
                    "km": float(km), "age": float(age),
                    "kw": r["power_kw"] or _title_power_kw(r["title"]),
                    "flags": _title_flags(r["title"])})
    return out


def fit(conn: sqlite3.Connection) -> FairPriceModel | None:
    try:
        import numpy as np
    except ModuleNotFoundError:
        return None

    rows = _feature_rows(conn, include_sold=True)   # inkl. verkaufter Angebote (Marktdaten)
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
        equip = [1.0 if f in r["flags"] else 0.0 for f in FLAG_ORDER]
        return base + [r["age"], math.log1p(r["km"]), float(r["kw"] or kw_default)] + equip

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
    b_equip = {f: float(coef[M + 3 + i]) for i, f in enumerate(FLAG_ORDER)}
    return FairPriceModel(model_ids=models, base=base,
                          b_age=float(coef[M]), b_logkm=float(coef[M + 1]),
                          b_kw=float(coef[M + 2]), kw_default=kw_default,
                          n=len(rows), r2=r2, b_equip=b_equip)


def estimate_listings(conn: sqlite3.Connection,
                      model: FairPriceModel | None = None) -> dict[int, FairEstimate]:
    """Fairer Preis + Residual je Angebot (nur fuer Modelle mit Basiswert)."""
    model = model or fit(conn)
    if model is None:
        return {}
    out: dict[int, FairEstimate] = {}
    for r in _feature_rows(conn):
        fair = model.predict(r["model_id"], r["age"], r["km"], r["kw"], r["flags"])
        if not fair:
            continue
        resid = r["price"] - fair
        out[r["id"]] = FairEstimate(
            listing_id=r["id"], model_id=r["model_id"], price=r["price"],
            fair_price=fair, resid_eur=resid, resid_pct=resid / fair,
            km=r["km"], age=r["age"])
    return out


def bargains(conn: sqlite3.Connection,
             model: FairPriceModel | None = None) -> list[FairEstimate]:
    """Plausible Schnaeppchen: unter fairem Preis, aber ohne Rand-Artefakte
    (Extrem-km/-Alter oder fast neue Basisversionen). Sortiert nach Abstand."""
    lo, hi = DEAL_BAND
    out = []
    for e in estimate_listings(conn, model).values():
        if not (lo <= e.resid_pct <= hi) or e.resid_eur > -DEAL_MIN_EUR:
            continue
        if e.km is not None and not (DEAL_KM_MIN <= e.km <= DEAL_KM_MAX):
            continue
        if e.age is not None and e.age > DEAL_AGE_MAX:
            continue
        out.append(e)
    out.sort(key=lambda e: e.resid_pct)
    return out


def top_per_model(estimates, per_model: int = 2, limit: int | None = None) -> list:
    """Vielfalt: hoechstens `per_model` Angebote je Modell (Reihenfolge bleibt),
    damit ein starkes Modell nicht die ganze Liste blockiert."""
    seen: dict[int, int] = {}
    out = []
    for e in estimates:
        if seen.get(e.model_id, 0) >= per_model:
            continue
        seen[e.model_id] = seen.get(e.model_id, 0) + 1
        out.append(e)
        if limit and len(out) >= limit:
            break
    return out
