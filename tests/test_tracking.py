"""Tests fuer die Angebots-Deaktivierung (verkauft/vom Markt) im Tracking."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.db import init_db
from autobewertung.tracking import deactivate_stale, price_floor


def _add(conn, ref, source, days_ago):
    ls = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO listing(model_id,source,source_ref,price,active,first_seen,last_seen) "
        "VALUES (1,?,?,10000,1,?,?)", (source, ref, ls, ls))


def _conn():
    conn = init_db(":memory:")
    conn.execute("INSERT INTO car_model(id,make,model) VALUES (1,'VW','Golf')")
    return conn


def test_deactivates_only_stale_scraper_listings():
    conn = _conn()
    _add(conn, "fresh", "autoscout24", 1)        # frisch -> bleibt
    _add(conn, "old_as", "autoscout24", 20)      # alt -> raus
    _add(conn, "old_ka", "kleinanzeigen", 15)    # alt -> raus
    _add(conn, "watch_old", "watch", 30)         # verfolgte URL bleibt
    _add(conn, "seed_old", "seed", 30)           # Seed bleibt
    conn.commit()
    n = deactivate_stale(conn, max_age_days=10)
    assert n == 2
    active = {r["source_ref"] for r in conn.execute("SELECT source_ref FROM listing WHERE active=1")}
    assert active == {"fresh", "watch_old", "seed_old"}


def test_nothing_deactivated_when_all_fresh():
    conn = _conn()
    _add(conn, "a", "autoscout24", 0); _add(conn, "b", "kleinanzeigen", 3)
    conn.commit()
    assert deactivate_stale(conn, max_age_days=10) == 0


def test_threshold_configurable():
    conn = _conn()
    _add(conn, "x", "autoscout24", 8)
    conn.commit()
    assert deactivate_stale(conn, max_age_days=5) == 1     # 8 > 5 -> raus
    conn.execute("UPDATE listing SET active=1")            # zuruecksetzen
    assert deactivate_stale(conn, max_age_days=14) == 0    # 8 < 14 -> bleibt


def test_price_floor_from_snapshots():
    conn = _conn()
    now = datetime.now(timezone.utc)
    for i, (med, mn) in enumerate([(12000, 11000), (11000, 10000), (11500, 10500)]):
        ts = (now - timedelta(days=10 - i * 3)).isoformat()
        conn.execute("INSERT INTO model_price_snapshot(model_id,ts,median_price,min_price,max_price,n) "
                     "VALUES (1,?,?,?,?,5)", (ts, med, mn, med + 2000))
    conn.commit()
    pf = price_floor(conn, 1, days=90)
    assert pf["current"] == 11500 and pf["low"] == 11000 and pf["low_min"] == 10000
    assert abs(pf["pct_above_low"] - (11500 - 11000) / 11000 * 100) < 1e-6
    assert price_floor(conn, 1, days=1) is None      # nichts im 1-Tage-Fenster
    assert price_floor(conn, 2) is None              # Modell ohne Historie


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
