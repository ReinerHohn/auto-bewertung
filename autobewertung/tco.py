"""Total Cost of Ownership (TCO) - komplette Haltekosten je Modell.

Berechnet die jaehrlichen Gesamtkosten eines Fahrzeugs ueber die Haltedauer:

    Wertverlust  + Energie (Sprit/Strom) + Versicherung + Kfz-Steuer
    + Wartung/Reparatur + Verschleiss/Sonstiges (Reifen, HU, Kleinkram)

Alle Annahmen (Jahreskilometer, Haltedauer, Energiepreise) stecken in
`TcoAssumptions` und sind ueber data/criteria.yaml einstellbar.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

# Fahrzeugklassen als Ordinalskala (Golf/Auris = 'kompakt' = 2).
CLASS_RANK = {
    "kleinstwagen": 0,
    "kleinwagen": 1,
    "kompakt": 2,
    "kombi": 2,
    "mittelklasse": 3,
    "suv": 3,
    "van": 3,
    "obere_mittelklasse": 4,
    "oberklasse": 5,
}


def class_rank(vehicle_class: str | None) -> int:
    if not vehicle_class:
        return 2  # unbekannt -> als Kompakt behandeln (nicht ausschliessen)
    return CLASS_RANK.get(vehicle_class.lower(), 2)


@dataclass
class TcoAssumptions:
    annual_km: int = 15000
    holding_years: int = 5
    price_benzin: float = 1.80        # EUR/l
    price_diesel: float = 1.70        # EUR/l
    price_strom_home: float = 0.30    # EUR/kWh
    price_strom_public: float = 0.55  # EUR/kWh (Schnelllader)
    home_charge_share: float = 0.7    # Anteil Heimladen bei EV
    # Verschleiss/Sonstiges (Reifen, HU/AU, Kleinkram) pauschal pro Jahr
    misc_per_year: float = 250.0
    default_depr_pct_year: float = 0.13

    @property
    def price_strom_blend(self) -> float:
        s = self.home_charge_share
        return s * self.price_strom_home + (1 - s) * self.price_strom_public


@dataclass
class TcoResult:
    annual_total: float
    total: float
    breakdown_year: dict[str, float]     # jaehrliche Einzelposten
    purchase_price: float
    resale_value: float
    running_year: float                  # laufende Kosten OHNE Wertverlust
    is_ev: bool

    def as_row(self) -> dict:
        return {"annual_total": round(self.annual_total),
                "total": round(self.total),
                "running_year": round(self.running_year),
                **{k: round(v) for k, v in self.breakdown_year.items()}}


def _energy_cost_year(spec, a: TcoAssumptions) -> float:
    km100 = a.annual_km / 100.0
    dt = (spec["drivetrain"] or "").lower()
    if dt == "elektro":
        kwh = spec["cons_kwh_100km"] or 18.0
        return km100 * kwh * a.price_strom_blend
    if dt == "diesel":
        return km100 * (spec["cons_l_100km"] or 5.5) * a.price_diesel
    # Benzin/Hybrid/unbekannt
    return km100 * (spec["cons_l_100km"] or 6.5) * a.price_benzin


def compute_tco(spec, purchase_price: float, maintenance_year: float,
                a: TcoAssumptions) -> TcoResult:
    """Berechnet TCO aus Fahrzeug-Spec, Kaufpreis und Wartungskosten/Jahr."""
    dt = (spec["drivetrain"] or "").lower()
    is_ev = dt == "elektro"

    depr_rate = spec["depr_pct_year"] if spec["depr_pct_year"] is not None else a.default_depr_pct_year
    resale = purchase_price * (1 - depr_rate) ** a.holding_years
    depreciation_year = (purchase_price - resale) / a.holding_years

    energy = _energy_cost_year(spec, a)
    insurance = spec["insurance_eur"] if spec["insurance_eur"] is not None else 500.0
    tax = spec["tax_eur"] if spec["tax_eur"] is not None else (0.0 if is_ev else 150.0)
    misc = a.misc_per_year

    breakdown = {
        "wertverlust": depreciation_year,
        "energie": energy,
        "versicherung": insurance,
        "steuer": tax,
        "wartung_reparatur": maintenance_year,
        "sonstiges": misc,
    }
    running = energy + insurance + tax + maintenance_year + misc
    annual = depreciation_year + running
    return TcoResult(
        annual_total=annual,
        total=annual * a.holding_years,
        breakdown_year=breakdown,
        purchase_price=purchase_price,
        resale_value=resale,
        running_year=running,
        is_ev=is_ev,
    )


def ice_reference_running(results: dict[int, TcoResult]) -> float:
    """Median der laufenden Jahreskosten aller Verbrenner/Hybride.

    Dient als Vergleichsmassstab fuer die EV-Ausnahme ("spart mir pro Jahr viel").
    """
    ice = [r.running_year for r in results.values() if not r.is_ev]
    return statistics.median(ice) if ice else 0.0
