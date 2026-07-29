"""Test: echte TUEV/ADAC-Daten ueberschreiben Seed-Schaetzungen korrekt."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.db import init_db
from autobewertung.sources.reliability_import import ReliabilityImportSource
from autobewertung.sources.seed import SeedSource


def build():
    conn = init_db(":memory:")
    SeedSource().collect(conn)
    ReliabilityImportSource().collect(conn)
    return conn


def _val(conn, model, metric):
    return conn.execute(
        "SELECT value, is_estimate FROM reliability_stat rs JOIN car_model cm ON cm.id=rs.model_id "
        "WHERE cm.model=? AND rs.metric=?", (model, metric)).fetchone()


def test_real_tuev_overrides_seed_tesla():
    conn = build()
    r = _val(conn, "Model 3", "maengelquote_pct")
    assert abs(r["value"] - 14.2) < 0.01     # echter TUEV-Wert statt Seed
    assert r["is_estimate"] == 0


def test_real_adac_pannen_imported():
    conn = build()
    r = _val(conn, "Model 3", "pannen_pro_1000")
    assert abs(r["value"] - 0.5) < 0.01
    assert r["is_estimate"] == 0


def test_no_duplicate_after_import():
    """Import darf keine Doppelzeilen (Seed + echt) hinterlassen."""
    conn = build()
    n = conn.execute(
        "SELECT COUNT(*) c FROM reliability_stat rs JOIN car_model cm ON cm.id=rs.model_id "
        "WHERE cm.model='Model 3' AND rs.metric='maengelquote_pct'").fetchone()["c"]
    assert n == 1


def test_unmatched_model_stays_estimate():
    """Modell ohne echten Wert (z.B. Golf) behaelt Seed-Schaetzung."""
    conn = build()
    r = _val(conn, "Golf", "maengelquote_pct")
    assert r is not None and r["is_estimate"] == 1


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