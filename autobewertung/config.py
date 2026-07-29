"""Nutzer-Kriterien und Gewichtung fuer das Ranking.

Die Gewichte werden aus data/criteria.yaml geladen (falls vorhanden),
sonst gelten die Defaults hier. So kannst du deine Kriterien anpassen,
ohne Code zu aendern.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CRITERIA_FILE = Path(__file__).resolve().parent.parent / "data" / "criteria.yaml"

# Die sechs Bewertungsdimensionen. Jede wird auf 0..100 normalisiert
# (100 = bestes Fahrzeug in dieser Dimension), dann gewichtet summiert.
DIMENSIONS = [
    "price_value",       # Schnaeppchen: Preis unter Marktwert + fallender Trend
    "reliability",       # Pannen-/Maengelquote (invertiert -> wenig Pannen = hoch)
    "weak_points",       # bekannte Schwachstellen + Rueckrufe (invertiert)
    "repair_cost",       # Reparatur-/Unterhaltskosten (invertiert)
    "parts_availability",# Ersatzteil-Verfuegbarkeit
    "workshop_access",   # Werkstattdichte/Spezialisten in der Naehe
]

DEFAULT_WEIGHTS = {
    "price_value": 0.30,
    "reliability": 0.25,
    "weak_points": 0.15,
    "repair_cost": 0.15,
    "parts_availability": 0.08,
    "workshop_access": 0.07,
}


@dataclass
class Criteria:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    # optionale harte Filter
    max_price: float | None = None
    max_mileage_km: int | None = None
    min_year: int | None = None
    home_plz: str | None = None          # fuer Werkstatt-/Standortnaehe

    def normalized_weights(self) -> dict[str, float]:
        total = sum(self.weights.get(d, 0.0) for d in DIMENSIONS) or 1.0
        return {d: self.weights.get(d, 0.0) / total for d in DIMENSIONS}


def load_criteria(path: Path | str = CRITERIA_FILE) -> Criteria:
    path = Path(path)
    if not path.exists():
        return Criteria()
    try:
        import yaml  # optional
        raw = yaml.safe_load(path.read_text()) or {}
    except ModuleNotFoundError:
        return Criteria()
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(raw.get("weights", {}))
    return Criteria(
        weights=weights,
        max_price=raw.get("max_price"),
        max_mileage_km=raw.get("max_mileage_km"),
        min_year=raw.get("min_year"),
        home_plz=raw.get("home_plz"),
    )
