"""Tests fuer den VIN-Decoder (netzfrei via Mock-Fetch)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.db import init_db
from autobewertung.sources.seed import SeedSource
from autobewertung.sources.wear_import import WearImportSource
from autobewertung.vin import decode_vin, guess_variant, match_model, valid_vin


def _mock(fields):
    return lambda url: json.dumps({"Results": [fields]})


def test_valid_vin():
    assert valid_vin("5YJ3E1EA7KF000316")
    assert not valid_vin("kurz")
    assert not valid_vin("5YJ3E1EA7KF00031I")   # I nicht erlaubt


def test_decode_and_match_tesla():
    conn = init_db(":memory:"); SeedSource().collect(conn)
    dec = decode_vin("5YJ3E1EA7KF000316", fetch=_mock(
        {"Make": "TESLA", "Model": "Model 3", "ModelYear": "2019",
         "FuelTypePrimary": "Electric"}))
    assert dec["make_norm"] == "Tesla" and dec["Model"] == "Model 3"
    assert match_model(conn, dec) is not None


def test_decode_and_match_vw_golf():
    conn = init_db(":memory:"); SeedSource().collect(conn)
    dec = decode_vin("WVWZZZAUZGW000000", fetch=_mock(
        {"Make": "VOLKSWAGEN", "Model": "Golf", "ModelYear": "2016",
         "FuelTypePrimary": "Gasoline", "DisplacementL": "1.4"}))
    assert dec["make_norm"] == "VW"
    mid = match_model(conn, dec)
    assert mid is not None
    row = conn.execute("SELECT model FROM car_model WHERE id=?", (mid,)).fetchone()
    assert row["model"] == "Golf"


def test_guess_variant_by_displacement():
    conn = init_db(":memory:"); SeedSource().collect(conn); WearImportSource().collect(conn)
    dec = {"make_norm": "VW", "Model": "Golf", "DisplacementL": "1.4",
           "FuelTypePrimary": "Gasoline"}
    mid = match_model(conn, dec)
    v = guess_variant(conn, mid, dec)
    assert v and "1.4" in v          # sollte die 1.2/1.4-TSI-Variante treffen


def test_unknown_make_no_match():
    conn = init_db(":memory:"); SeedSource().collect(conn)
    dec = decode_vin("XXXXXXXXXXXXXXXXX", fetch=_mock(
        {"Make": "FERRARI", "Model": "488", "ModelYear": "2018"}))
    assert match_model(conn, dec) is None


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