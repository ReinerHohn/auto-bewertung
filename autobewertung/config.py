"""Nutzer-Kriterien und Gewichtung fuer das Ranking.

Die Gewichte + Filter werden aus data/criteria.yaml geladen (falls vorhanden),
sonst gelten die Defaults hier. So kannst du deine Kriterien anpassen,
ohne Code zu aendern.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .tco import TcoAssumptions

CRITERIA_FILE = Path(__file__).resolve().parent.parent / "data" / "criteria.yaml"

# Die Bewertungsdimensionen. Jede wird auf 0..100 normalisiert
# (100 = bestes Fahrzeug in dieser Dimension), dann gewichtet summiert.
DIMENSIONS = [
    "tco",               # komplette Haltekosten pro Jahr (invertiert -> guenstig = hoch)
    "value_stability",   # Wertstabilitaet: geringer Wertverlust/Jahr = hoch
    "equipment",         # gewuenschte Assistenz/Komfort vorhanden, Matrix vermieden
    "reliability",       # Pannen-/Maengelquote (invertiert)
    "weak_points",       # bekannte Schwachstellen + Rueckrufe (invertiert)
    "price_value",       # Schnaeppchen: Preis unter Marktwert + fallender Trend
    "parts_availability",# Ersatzteil-Verfuegbarkeit
    "workshop_access",   # Werkstattdichte/Spezialisten in der Naehe
]

DEFAULT_WEIGHTS = {
    "tco": 0.26,
    "value_stability": 0.12,
    "equipment": 0.14,
    "reliability": 0.18,
    "weak_points": 0.10,
    "price_value": 0.08,
    "parts_availability": 0.06,
    "workshop_access": 0.06,
}

# Gewuenschte Ausstattung (Pflicht-/Wunschfeatures) und zu vermeidende Extras.
DEFAULT_WANT_FEATURES = ["einparkhilfe", "rueckfahrkamera", "notbremsassistent", "spurhalteassistent"]


@dataclass
class Criteria:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    # harte Filter
    max_price: float | None = None
    max_mileage_km: int | None = None
    min_year: int | None = None
    min_vehicle_class: str | None = None   # z.B. 'kompakt' (Golf/Auris) aufwaerts
    home_plz: str | None = None            # fuer Werkstatt-/Standortnaehe
    # EV-Ausnahme: E-Autos duerfen max_price ueberschreiten, wenn ihre
    # jaehrliche Ersparnis vs. Verbrenner es ueber die Haltedauer rechtfertigt.
    ev_price_exception: bool = True
    ev_min_charge_km_30min: float | None = None  # Pflicht: km nachladbar in 30 min
    # Ausstattung
    want_features: list[str] = field(default_factory=lambda: list(DEFAULT_WANT_FEATURES))
    avoid_matrix: bool = True            # teure Matrix-/Voll-LED meiden
    # TCO-Annahmen
    tco: TcoAssumptions = field(default_factory=TcoAssumptions)

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

    tco_raw = raw.get("tco", {}) or {}
    tco = TcoAssumptions(**{k: v for k, v in tco_raw.items()
                            if k in TcoAssumptions.__dataclass_fields__})

    return Criteria(
        weights=weights,
        max_price=raw.get("max_price"),
        max_mileage_km=raw.get("max_mileage_km"),
        min_year=raw.get("min_year"),
        min_vehicle_class=raw.get("min_vehicle_class"),
        home_plz=raw.get("home_plz"),
        ev_price_exception=raw.get("ev_price_exception", True),
        ev_min_charge_km_30min=raw.get("ev_min_charge_km_30min"),
        want_features=raw.get("want_features", list(DEFAULT_WANT_FEATURES)),
        avoid_matrix=raw.get("avoid_matrix", True),
        tco=tco,
    )
