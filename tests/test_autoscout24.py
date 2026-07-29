"""Tests fuer den AutoScout24-Parser + Import (netzfrei via Fixture)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.db import init_db
from autobewertung.sources.autoscout24 import AutoScout24Source, parse_autoscout24
from autobewertung.sources.seed import SeedSource

def _vd(power, rng):
    return [{"iconName": "speedometer", "data": f"{power} kW (306 PS)"},
            {"iconName": "distance", "data": f"{rng} km Reichweite"}]


def _listing(id_, price, ev, fuel, reg, mil, rng, power=225, damaged=False, make="Tesla", model="Model 3"):
    import json
    return json.dumps({
        "id": id_, "url": f"/angebote/{id_}", "price": {"priceRaw": price, "priceEvaluation": ev},
        "vehicle": {"make": make, "model": model, "isCurrentlyDamaged": damaged,
                    "modelVersionInput": f"{model} Var"},
        "tracking": {"firstRegistration": reg, "mileage": str(mil), "fuelType": fuel},
        "location": {"zip": "79100", "city": "Freiburg"},
        "vehicleDetails": _vd(power, rng)})


FIX = ('<html><body><script id="__NEXT_DATA__" type="application/json">'
       '{"props":{"pageProps":{"listings":[' + ",".join([
           _listing("a1", 18490, 2, "e", "12-2019", 155000, 409),
           _listing("a2", 22900, 3, "e", "03-2020", 90000, 430),
           _listing("a3", 9990, 1, "b", "05-2018", 120000, 0, make="Hyundai", model="Kona"),  # Benziner
           _listing("a4", 9500, 1, "e", "01-2018", 100000, 200),   # Mini-Reichweite -> Variantenfilter
           _listing("a5", 8000, 1, "e", "06-2019", 80000, 420, damaged=True),  # Unfall -> raus
       ]) + ']}}}</script></body></html>')


def test_parse_fields():
    items = parse_autoscout24(FIX)
    assert len(items) == 5
    t = items[0]
    assert t["price"] == 18490.0 and t["mileage_km"] == 155000
    assert t["first_reg"] == "2019-12"           # MM-YYYY -> YYYY-MM
    assert t["rating"] == 2 and t["fuel"] == "e"
    assert t["power_kw"] == 225 and t["list_range"] == 409
    assert t["url"].startswith("https://www.autoscout24.de/")


def test_parse_empty():
    assert parse_autoscout24("<html>nix</html>") == []


def test_fetch_filters_fuel_range_and_damage():
    conn = init_db(":memory:"); SeedSource().collect(conn)
    mid = conn.execute("SELECT id FROM car_model WHERE model='Model 3'").fetchone()["id"]
    n, msg = AutoScout24Source(fetch=lambda u: FIX).fetch_model(conn, mid, "Tesla", "Model 3")
    # Benziner-Kona (Fuel), Mini-Reichweite 200km (Variante) und Unfaller raus -> nur a1,a2
    assert n == 2
    rows = conn.execute("SELECT price, price_rating, power_kw FROM listing WHERE model_id=? "
                        "AND source='autoscout24' ORDER BY price", (mid,)).fetchall()
    assert [r["price"] for r in rows] == [18490.0, 22900.0]
    assert rows[0]["price_rating"] == 2 and rows[0]["power_kw"] == 225


def test_repeated_fetch_tracks_price_history():
    conn = init_db(":memory:"); SeedSource().collect(conn)
    mid = conn.execute("SELECT id FROM car_model WHERE model='Model 3'").fetchone()["id"]
    src = AutoScout24Source(fetch=lambda u: FIX)
    src.fetch_model(conn, mid, "Tesla", "Model 3")
    lid = conn.execute("SELECT id FROM listing WHERE source_ref='a1'").fetchone()["id"]
    cnt = conn.execute("SELECT COUNT(*) c FROM price_point WHERE listing_id=?", (lid,)).fetchone()["c"]
    assert cnt == 1


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