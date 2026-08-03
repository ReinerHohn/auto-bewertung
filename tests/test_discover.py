"""Tests fuer die Auto-Discovery neuer Modelle (netzfrei via Fixture)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.db import init_db
from autobewertung.sources.discover import DiscoverSource, _norm_model
from autobewertung.sources.seed import SeedSource


def _listing(id_, make, model, fuel, price, reg, mil=80000, rng=0, damaged=False):
    return json.dumps({
        "id": id_, "url": f"/angebote/{id_}",
        "price": {"priceRaw": price, "priceEvaluation": 2},
        "vehicle": {"make": make, "model": model, "isCurrentlyDamaged": damaged,
                    "modelVersionInput": f"{model} Basis"},
        "tracking": {"firstRegistration": reg, "mileage": str(mil), "fuelType": fuel},
        "location": {"zip": "79100", "city": "Freiburg"},
        "vehicleDetails": [{"iconName": "distance", "data": f"{rng} km Reichweite"}] if rng else []})


FIX = ('<html><script id="__NEXT_DATA__" type="application/json">'
       '{"props":{"pageProps":{"listings":[' + ",".join([
           _listing("g1", "Volkswagen", "Golf", "b", 12000, "05-2016"),      # existiert -> skip
           _listing("g2", "Volkswagen", "Golf", "d", 13000, "03-2017"),
           _listing("t1", "Volkswagen", "Touran", "b", 15000, "06-2017"),    # NEU (3x)
           _listing("t2", "Volkswagen", "Touran", "b", 17000, "01-2018"),
           _listing("t3", "Volkswagen", "Touran", "d", 16000, "09-2016"),
           _listing("p1", "Volkswagen", "Polo", "b", 9000, "04-2018"),       # NEU (2x)
           _listing("p2", "Volkswagen", "Polo", "b", 10000, "07-2019"),
           _listing("pv1", "Volkswagen", "Passat Variant", "d", 14000, "05-2016"),  # -> Passat (existiert)
           _listing("pv2", "Volkswagen", "Passat Variant", "d", 15000, "08-2017"),
           _listing("tg1", "Volkswagen", "Touareg", "d", 25000, "01-2017"),  # nur 1x -> unter Schwelle
           _listing("cd1", "Volkswagen", "Caddy", "d", 14000, "01-2017"),    # Nutzfahrzeug -> raus
           _listing("d1", "Volkswagen", "Arteon", "b", 20000, "01-2019", damaged=True),  # Unfall -> raus
       ]) + ']}}}</script></html>')


def _fetch(url):
    return FIX if url.endswith("/lst/vw") else None


def _run(dry=False, min_listings=2):
    conn = init_db(":memory:"); SeedSource().collect(conn)
    src = DiscoverSource(fetch=_fetch, makes=[("VW", "vw")],
                         min_listings=min_listings, dry_run=dry)
    res = src.collect(conn)
    return conn, src, res


def test_norm_model_strips_body_suffix():
    assert _norm_model("Passat Variant") == "Passat"
    assert _norm_model("Astra Sports Tourer") == "Astra"
    assert _norm_model("up!") == "up"


def test_creates_new_models_with_marker():
    conn, src, res = _run()
    assert res.inserted == 2                       # Touran + Polo
    rows = {r["model"]: r for r in conn.execute(
        "SELECT model, generation, year_from, year_to FROM car_model "
        "WHERE generation='auto-entdeckt'")}
    assert set(rows) == {"Touran", "Polo"}
    assert rows["Touran"]["year_from"] == 2016 and rows["Touran"]["year_to"] == 2018


def test_derives_drivetrain_and_price():
    conn, src, res = _run()
    mid = conn.execute("SELECT id FROM car_model WHERE model='Touran'").fetchone()["id"]
    spec = conn.execute("SELECT drivetrain, typical_price FROM vehicle_spec WHERE model_id=?",
                        (mid,)).fetchone()
    assert spec["drivetrain"] == "benzin"          # 2x b vs 1x d -> Mehrheit benzin
    assert spec["typical_price"] == 16000          # Median(15000,17000,16000)


def test_attaches_listings():
    conn, _, _ = _run()
    mid = conn.execute("SELECT id FROM car_model WHERE model='Polo'").fetchone()["id"]
    n = conn.execute("SELECT COUNT(*) c FROM listing WHERE model_id=?", (mid,)).fetchone()["c"]
    assert n == 2


def test_skips_existing_and_body_variant_of_existing():
    conn, _, _ = _run()
    # Golf existiert schon -> kein Duplikat; Passat existiert (Seed) -> "Passat Variant" nicht neu
    assert conn.execute("SELECT COUNT(*) c FROM car_model WHERE model='Golf'").fetchone()["c"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM car_model WHERE model='Passat' AND generation='auto-entdeckt'"
    ).fetchone()["c"] == 0


def test_skips_commercial_and_below_threshold():
    conn, _, _ = _run()
    assert conn.execute("SELECT COUNT(*) c FROM car_model WHERE model='Caddy'").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM car_model WHERE model='Touareg'").fetchone()["c"] == 0


def test_dry_run_creates_nothing():
    conn, src, res = _run(dry=True)
    assert res.inserted == 0 and res.updated == 2  # zaehlt Kandidaten, legt nichts an
    assert conn.execute(
        "SELECT COUNT(*) c FROM car_model WHERE generation='auto-entdeckt'").fetchone()["c"] == 0
    assert len(src.report) == 2


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
