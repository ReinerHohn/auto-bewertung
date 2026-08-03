"""Tests fuer den Kleinanzeigen-Parser + Baujahr-Filter (netzfrei via Fixture)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.db import init_db
from autobewertung.sources.kleinanzeigen import (
    KleinanzeigenSource, _first_reg, _price, _slug, parse_kleinanzeigen)
from autobewertung.sources.seed import SeedSource


def _ad(adid, title, price_txt, km, ez):
    return (f'<article class="aditem" data-adid="{adid}" data-href="/s-anzeige/x/{adid}">'
            f'<div class="aditem-main--top--left">79100 Freiburg</div>'
            f'<h2 class="text-module-begin"><a href="/s-anzeige/x/{adid}">{title}</a></h2>'
            f'<p class="aditem-main--middle--price-shipping--price">{price_txt}</p>'
            f'<span class="simpletag">{km}</span><span class="simpletag">EZ {ez}</span>'
            '</article>')


FIX = ("<html><body>"
       + _ad("111", "VW Golf VII 1.4 TSI", "9.900 € VB", "120.000 km", "06/2016")   # in range
       + _ad("222", "VW Golf I H-Zulassung", "8.000 €", "200.000 km", "05/1985")     # zu alt
       + _ad("333", "VW Golf VII Highline", "14.000 €", "80.000 km", "03/2018")       # in range
       + _ad("444", "VW Golf Bastler", "Zu verschenken", "300.000 km", "01/2003")     # kein Preis
       + "</body></html>")


def test_helpers():
    assert _price("9.900 € VB3.750 €") == 9900.0
    assert _price("Zu verschenken") is None
    assert _first_reg("EZ 02/2011") == "2011-02"
    assert _slug("VW", "Golf") == "vw-golf"
    assert _slug("Tesla", "Model 3") == "tesla-model-3"


def test_parse_fields():
    items = parse_kleinanzeigen(FIX)
    assert len(items) == 3                       # 444 (kein Preis) faellt raus
    it = next(i for i in items if i["source_ref"] == "111")
    assert it["price"] == 9900.0 and it["mileage_km"] == 120000
    assert it["first_reg"] == "2016-06" and it["plz"] == "79100"
    assert it["url"].startswith("https://www.kleinanzeigen.de/")


def test_parse_empty():
    assert parse_kleinanzeigen("<html>nix</html>") == []


def test_fetch_model_filters_by_year():
    conn = init_db(":memory:"); SeedSource().collect(conn)
    mid = conn.execute("SELECT id FROM car_model WHERE model='Golf'").fetchone()["id"]
    n, msg = KleinanzeigenSource(fetch=lambda u: FIX).fetch_model(conn, mid, "VW", "Golf")
    assert n == 2                                # nur die zwei Golf VII (2012-2019)
    refs = {r["source_ref"] for r in conn.execute(
        "SELECT source_ref FROM listing WHERE model_id=? AND source='kleinanzeigen'", (mid,))}
    assert refs == {"111", "333"}                # Oldtimer 222 raus


def test_source_kept_separate_from_as24():
    conn = init_db(":memory:"); SeedSource().collect(conn)
    mid = conn.execute("SELECT id FROM car_model WHERE model='Golf'").fetchone()["id"]
    KleinanzeigenSource(fetch=lambda u: FIX).fetch_model(conn, mid, "VW", "Golf")
    src = conn.execute("SELECT DISTINCT source FROM listing WHERE model_id=? AND source_ref='111'",
                       (mid,)).fetchone()["source"]
    assert src == "kleinanzeigen"


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
