"""Entfernung zwischen zwei PLZ + Anfahrtskosten (offline, aus data/plz_geo.csv).

Damit lässt sich die Entfernung eines Angebots zum Wohnort in den echten Vorteil
einrechnen: ein Schnaeppchen weit weg kostet Anfahrt (Sprit/Zeit/Risiko) und ist
netto weniger wert. Exakte 5-stellige PLZ, sonst Fallback auf die 2-stellige Zone.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

PLZ_CSV = Path(__file__).resolve().parent.parent / "data" / "plz_geo.csv"
EUR_PER_KM = 0.30                 # Sprit + Verschleiss grob pro km

_coords: dict[str, tuple[float, float]] | None = None
_prefix: dict[str, tuple[float, float]] = {}


def _load() -> None:
    global _coords
    if _coords is not None:
        return
    _coords = {}
    agg: dict[str, list[tuple[float, float]]] = {}
    if not PLZ_CSV.exists():
        return
    with open(PLZ_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(r for r in f if not r.startswith("#")):
            try:
                plz, lat, lon = row["plz"].strip(), float(row["lat"]), float(row["lon"])
            except (KeyError, ValueError, TypeError):
                continue
            _coords[plz] = (lat, lon)
            agg.setdefault(plz[:2], []).append((lat, lon))
    for p, v in agg.items():
        _prefix[p] = (sum(a for a, _ in v) / len(v), sum(b for _, b in v) / len(v))


def coords(plz: str | None) -> tuple[float, float] | None:
    if not plz:
        return None
    _load()
    p = str(plz).strip()[:5]
    if _coords and p in _coords:
        return _coords[p]
    return _prefix.get(p[:2])      # Fallback: Mittelpunkt der 2-stelligen Zone


def distance_km(a: str | None, b: str | None) -> float | None:
    """Luftlinie zwischen zwei PLZ in km (Haversine)."""
    ca, cb = coords(a), coords(b)
    if not ca or not cb:
        return None
    (lat1, lon1), (lat2, lon2) = ca, cb
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def travel_cost_eur(distance_km: float | None, round_trip: bool = True) -> float | None:
    """Anfahrtskosten: Hin- und Rueckfahrt (Luftlinie ~ Naeherung) x EUR_PER_KM."""
    if distance_km is None:
        return None
    return distance_km * EUR_PER_KM * (2 if round_trip else 1)


def net_saving_eur(resid_eur: float, plz: str | None, home_plz: str | None) -> float | None:
    """Ersparnis unter fair MINUS Anfahrt = echter Vorteil. resid_eur ist negativ
    (unter fair); Rueckgabe positiv = lohnt sich netto trotz Anfahrt."""
    d = distance_km(home_plz, plz)
    tc = travel_cost_eur(d)
    if tc is None:
        return -resid_eur          # Entfernung unbekannt -> nur Ersparnis
    return -resid_eur - tc
