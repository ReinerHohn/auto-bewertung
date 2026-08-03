"""Tests fuer Entfernung + Anfahrtskosten (offline PLZ-Koordinaten)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung import geo


def test_known_distances():
    d = geo.distance_km("79100", "10115")          # Freiburg -> Berlin
    assert 600 < d < 700
    assert geo.distance_km("79098", "79312") < 30  # Freiburg -> Emmendingen (nah)
    assert geo.distance_km("79100", "79100") < 5   # gleiche PLZ


def test_travel_cost_round_trip():
    tc = geo.travel_cost_eur(600)
    assert abs(tc - 600 * 0.30 * 2) < 1e-6         # Hin+Rueck


def test_net_saving_subtracts_travel():
    # -3000 EUR unter fair, 600 km weg -> Ersparnis minus Anfahrt < 3000
    net = geo.net_saving_eur(-3000, "10115", "79100")
    assert 2400 < net < 2800
    # kleiner Deal weit weg -> Anfahrt frisst viel
    assert geo.net_saving_eur(-800, "10115", "79100") < 500


def test_unknown_plz_falls_back_or_savings_only():
    # unbekannte/leere PLZ -> nur Ersparnis (keine Anfahrt abziehbar)
    assert geo.net_saving_eur(-1000, None, "79100") == 1000
    assert geo.distance_km("00000", None) is None


def test_prefix_fallback():
    # 5-stellige unbekannt, aber 2-stellige Zone existiert -> Naeherung, kein None
    assert geo.coords("79999") is not None         # 79er Zone existiert


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns)-failed}/{len(fns)} bestanden")
    sys.exit(1 if failed else 0)
