"""Tests fuer km-Plausibilitaet und Verschleiss-Abgleich im Kauf-Check."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.checks import due_soon, mileage_plausibility, wear_status
from autobewertung.db import init_db
from autobewertung.sources.seed import SeedSource
from autobewertung.sources.wear_import import WearImportSource

REF = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_plausibility_rollback_flag():
    """20.000 km bei EZ 2016 -> ~2.000 km/Jahr -> Warnung."""
    p = mileage_plausibility(20000, "2016-01", ref=REF)
    assert p["level"] == "warn" and p["km_per_year"] < 5000


def test_plausibility_normal():
    p = mileage_plausibility(150000, "2016-01", ref=REF)
    assert p["level"] == "ok"


def test_plausibility_none_without_data():
    assert mileage_plausibility(None, "2016-01") is None
    assert mileage_plausibility(50000, None) is None


def test_wear_status_done_and_upcoming():
    conn = init_db(":memory:"); SeedSource().collect(conn); WearImportSource().collect(conn)
    mid = conn.execute("SELECT id FROM car_model WHERE model='Model 3'").fetchone()["id"]
    done, upcoming = wear_status(conn, mid, None, 100000)
    # Querlenker (ab 40k) sollte bei 100k schon 'erledigt gewesen sein'
    assert any("Querlenker" in d["component"] for d in done)
    # jedes 'upcoming' liegt in der Zukunft
    assert all(u["next_km"] > 100000 for u in upcoming)


def test_wear_status_variant_filter():
    conn = init_db(":memory:"); SeedSource().collect(conn); WearImportSource().collect(conn)
    mid = conn.execute("SELECT id FROM car_model WHERE model='3er'").fetchone()["id"]
    done_n47, _ = wear_status(conn, mid, "N47 Diesel (318d/320d)", 200000)
    comps = " ".join(d["component"] for d in done_n47)
    assert "Steuerkette" in comps               # N47-Kette dabei
    # N20-spezifische Posten sollten NICHT auftauchen
    assert not any("Spanner" in d["component"] for d in done_n47)


def test_due_soon_warns_before_zahnriemen():
    """Golf-Zahnriemen faellig bei 210k -> bei 200k Warnung 'in ~10k km'."""
    conn = init_db(":memory:"); SeedSource().collect(conn); WearImportSource().collect(conn)
    mid = conn.execute("SELECT id FROM car_model WHERE model='Golf'").fetchone()["id"]
    soon = due_soon(conn, mid, "1.2/1.4 TSI (EA211)", 200000, horizon_km=15000)
    zr = [s for s in soon if "Zahnriemen" in s["component"]]
    assert zr and zr[0]["km_until"] == 10000


def test_due_soon_empty_when_nothing_close():
    conn = init_db(":memory:"); SeedSource().collect(conn); WearImportSource().collect(conn)
    mid = conn.execute("SELECT id FROM car_model WHERE model='Golf'").fetchone()["id"]
    # frisch nach Bremsen/Reifen -> in den naechsten 1000 km nichts
    assert due_soon(conn, mid, None, 46000, horizon_km=1000) == []


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