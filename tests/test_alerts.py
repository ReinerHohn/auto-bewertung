"""Tests fuer den Schnaeppchen-Alarm (neue Top-Preise + Preissenkungen, Dedup)."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.alerts import scan_alerts
from autobewertung.db import init_db
from autobewertung.sources.inserate import _record_listing
from autobewertung.sources.seed import SeedSource


def _conn():
    conn = init_db(":memory:"); SeedSource().collect(conn)
    return conn


def _mid(conn):
    return conn.execute("SELECT id FROM car_model WHERE model='e-Niro'").fetchone()["id"]


def test_deal_alert_on_new_top_price():
    conn = _conn(); mid = _mid(conn)
    since = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _record_listing(conn, model_id=mid, source="autoscout24", source_ref="x1",
                    title="e-Niro", price=17900, price_rating=1)   # Sehr guter Preis
    al = scan_alerts(conn, since)
    assert any("Sehr guter Preis" in a and "e-Niro" in a for a in al)


def test_no_deal_without_good_rating():
    conn = _conn(); mid = _mid(conn)
    since = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _record_listing(conn, model_id=mid, source="autoscout24", source_ref="x2",
                    title="e-Niro", price=17900, price_rating=4)   # Erhoehter Preis
    assert scan_alerts(conn, since) == []


def test_dedup_no_repeat():
    conn = _conn(); mid = _mid(conn)
    since = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _record_listing(conn, model_id=mid, source="autoscout24", source_ref="x3",
                    title="e-Niro", price=17900, price_rating=1)
    assert len(scan_alerts(conn, since)) == 1
    assert scan_alerts(conn, since) == []      # zweiter Scan -> kein Doppel-Alarm


def test_price_drop_alert():
    conn = _conn(); mid = _mid(conn)
    since = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _record_listing(conn, model_id=mid, source="autoscout24", source_ref="x4",
                    title="e-Niro", price=20000)
    scan_alerts(conn, since)                    # erster Preis, kein Drop
    _record_listing(conn, model_id=mid, source="autoscout24", source_ref="x4",
                    title="e-Niro", price=18500)  # gefallen
    al = scan_alerts(conn, since)
    assert any("Preis gefallen" in a for a in al)


def test_seed_simulated_history_no_drop_alert():
    """Seed-Listings (simulierter Verlauf) duerfen keinen Drop-Alarm ausloesen."""
    conn = _conn()
    since = "2000-01-01T00:00:00+00:00"
    al = scan_alerts(conn, since)
    assert not any("Preis gefallen" in a for a in al)


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