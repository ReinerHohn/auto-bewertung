"""Tests fuer den AutoScout24-Parser + Import (netzfrei via Fixture)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.db import init_db
from autobewertung.sources.autoscout24 import AutoScout24Source, parse_autoscout24
from autobewertung.sources.seed import SeedSource

FIX = '''<html><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"listings":[
 {"id":"a1","url":"/angebote/x","price":{"priceRaw":18490,"priceEvaluation":2},
  "vehicle":{"make":"Tesla","model":"Model 3"},
  "tracking":{"firstRegistration":"12-2019","mileage":"155000","fuelType":"e"},
  "location":{"zip":"16244","city":"Schorfheide"}},
 {"id":"a2","url":"/angebote/y","price":{"priceRaw":22900,"priceEvaluation":3},
  "vehicle":{"make":"Tesla","model":"Model 3"},
  "tracking":{"firstRegistration":"03-2020","mileage":"90000","fuelType":"e"},
  "location":{"zip":"79100","city":"Freiburg"}},
 {"id":"a3","url":"/angebote/z","price":{"priceRaw":9990,"priceEvaluation":1},
  "vehicle":{"make":"Hyundai","model":"Kona"},
  "tracking":{"firstRegistration":"05-2018","mileage":"120000","fuelType":"b"},
  "location":{"zip":"79100","city":"Freiburg"}}
]}}}
</script></body></html>'''


def test_parse_fields():
    items = parse_autoscout24(FIX)
    assert len(items) == 3
    t = items[0]
    assert t["price"] == 18490.0 and t["mileage_km"] == 155000
    assert t["first_reg"] == "2019-12"           # MM-YYYY -> YYYY-MM
    assert t["rating"] == 2 and t["fuel"] == "e"
    assert t["url"].startswith("https://www.autoscout24.de/")


def test_parse_empty():
    assert parse_autoscout24("<html>nix</html>") == []


def test_fetch_stores_and_filters_by_fuel():
    conn = init_db(":memory:"); SeedSource().collect(conn)
    mid = conn.execute("SELECT id FROM car_model WHERE model='Model 3'").fetchone()["id"]
    n, msg = AutoScout24Source(fetch=lambda u: FIX).fetch_model(conn, mid, "Tesla", "Model 3")
    # nur die beiden E-Angebote (Benziner-Kona rausgefiltert)
    assert n == 2
    rows = conn.execute("SELECT price, price_rating FROM listing WHERE model_id=? AND source='autoscout24' "
                        "ORDER BY price", (mid,)).fetchall()
    assert [r["price"] for r in rows] == [18490.0, 22900.0]
    assert rows[0]["price_rating"] == 2           # Portal-Preisbewertung gespeichert


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