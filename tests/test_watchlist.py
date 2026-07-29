"""Tests fuer den Watchlist-Parser und die Preisverfolgung (ohne Netz)."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.db import add_watch, init_db
from autobewertung.sources.watchlist import WatchlistSource, _to_price, parse_listing


def _fixture(price="12900", km="89000"):
    return f"""<html><head>
    <script type="application/ld+json">
    {{"@context":"https://schema.org","@type":"Car","name":"VW Golf VII 1.4 TSI",
      "brand":{{"@type":"Brand","name":"Volkswagen"}},"model":"Golf",
      "vehicleModelDate":"2016",
      "mileageFromOdometer":{{"@type":"QuantitativeValue","value":"{km}","unitCode":"KMT"}},
      "offers":{{"@type":"Offer","price":"{price}","priceCurrency":"EUR"}}}}
    </script></head><body>Angebot</body></html>"""


def _resp(html, code=200):
    return SimpleNamespace(status_code=code, text=html)


def test_to_price_variants():
    assert _to_price(12900) == 12900.0
    assert _to_price("12900") == 12900.0
    assert _to_price("12900.00") == 12900.0
    assert _to_price("12.900") == 12900.0          # deutscher Tausenderpunkt
    assert _to_price("12.900,50") == 12900.50      # deutsches Format
    assert _to_price("€ 12.900,-") == 12900.0
    assert _to_price("keine") is None


def test_parse_listing_jsonld():
    d = parse_listing(_fixture())
    assert d["price"] == 12900.0
    assert d["make"] == "VW"                        # aus "Volkswagen" normalisiert
    assert d["model"] == "Golf"
    assert d["mileage_km"] == 89000
    assert d["first_reg"] == "2016"
    assert "Golf" in d["title"]


def test_parse_listing_price_regex_fallback():
    html = '<html><body><span itemprop="price" content="8990">8.990 €</span></body></html>'
    assert parse_listing(html)["price"] == 8990.0


def test_parse_listing_empty_when_no_data():
    assert parse_listing("<html><body>nichts</body></html>") == {}


def test_watchlist_records_and_tracks_price():
    conn = init_db(":memory:")
    url = "https://example.com/inserat/1"
    add_watch(conn, url)

    # 1. Lauf: Preis 12900 -> Angebot + 1 Preispunkt
    prices = iter([_fixture("12900"), _fixture("12490")])
    src = WatchlistSource(fetch=lambda u: _resp(next(prices)))
    res = src.collect(conn)
    assert res.inserted == 1
    lid = conn.execute("SELECT id FROM listing WHERE source_ref=?", (url,)).fetchone()["id"]
    assert conn.execute("SELECT COUNT(*) c FROM price_point WHERE listing_id=?", (lid,)).fetchone()["c"] == 1

    # 2. Lauf: neuer Preis 12490 -> selbes Angebot, 2. Preispunkt, Preis aktualisiert
    src.collect(conn)
    pts = conn.execute("SELECT price FROM price_point WHERE listing_id=? ORDER BY ts", (lid,)).fetchall()
    assert len(pts) >= 2
    cur_price = conn.execute("SELECT price FROM listing WHERE id=?", (lid,)).fetchone()["price"]
    assert cur_price == 12490.0


def test_watchlist_model_matching():
    """Angebot wird dem vorhandenen Seed-Modell VW Golf zugeordnet."""
    from autobewertung.sources.seed import SeedSource
    conn = init_db(":memory:")
    SeedSource().collect(conn)
    url = "https://example.com/inserat/golf"
    add_watch(conn, url)
    WatchlistSource(fetch=lambda u: _resp(_fixture())).collect(conn)
    row = conn.execute(
        "SELECT cm.make, cm.model FROM listing l JOIN car_model cm ON cm.id=l.model_id "
        "WHERE l.source_ref=?", (url,)).fetchone()
    assert row["make"] == "VW" and row["model"] == "Golf"


def test_bmw_trim_maps_to_series():
    from autobewertung.sources.seed import SeedSource
    from autobewertung.sources.watchlist import _match_or_create_model
    conn = init_db(":memory:")
    SeedSource().collect(conn)
    mid = _match_or_create_model(conn, "BMW", "320d")
    row = conn.execute("SELECT make, model FROM car_model WHERE id=?", (mid,)).fetchone()
    assert (row["make"], row["model"]) == ("BMW", "3er")


def test_watch_model_binding_overrides_parser():
    """Feste model_id in der Watchlist hat Vorrang vor der Namens-Zuordnung."""
    from autobewertung.sources.seed import SeedSource
    conn = init_db(":memory:")
    SeedSource().collect(conn)
    focus_id = conn.execute("SELECT id FROM car_model WHERE model='Focus'").fetchone()["id"]
    url = "https://example.com/x"
    add_watch(conn, url, model_id=focus_id)          # obwohl HTML einen VW Golf beschreibt
    WatchlistSource(fetch=lambda u: _resp(_fixture())).collect(conn)
    mid = conn.execute("SELECT model_id FROM listing WHERE source_ref=?", (url,)).fetchone()["model_id"]
    assert mid == focus_id


def test_snapshot_model_prices():
    from autobewertung.sources.seed import SeedSource
    from autobewertung.tracking import snapshot_model_prices
    conn = init_db(":memory:")
    SeedSource().collect(conn)
    n = snapshot_model_prices(conn)
    assert n > 0
    total = conn.execute("SELECT COUNT(*) c FROM model_price_snapshot").fetchone()["c"]
    assert total == n
    # zweiter Lauf ohne Preisaenderung -> keine neuen Punkte
    assert snapshot_model_prices(conn) == 0


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