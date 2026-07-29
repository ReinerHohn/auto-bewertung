"""Tests fuer Scoring und Datenfluss gegen eine In-Memory-DB mit Seed-Daten."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.config import DIMENSIONS, Criteria
from autobewertung.db import init_db
from autobewertung.scoring import _minmax, score_models
from autobewertung.sources.seed import SeedSource


def seeded_conn():
    conn = init_db(":memory:")
    SeedSource().collect(conn)
    return conn


def test_minmax_basic_and_invert():
    vals = {1: 0.0, 2: 5.0, 3: 10.0}
    up = _minmax(vals)
    assert up[1] == 0.0 and up[3] == 100.0 and up[2] == 50.0
    inv = _minmax(vals, invert=True)
    assert inv[1] == 100.0 and inv[3] == 0.0


def test_minmax_constant_is_neutral():
    assert _minmax({1: 7.0, 2: 7.0}) == {1: 50.0, 2: 50.0}


def test_score_models_returns_all_and_sorted():
    conn = seeded_conn()
    ranked = score_models(conn, Criteria())
    assert len(ranked) == 6
    totals = [m.total for m in ranked]
    assert totals == sorted(totals, reverse=True)
    for m in ranked:
        assert 0 <= m.total <= 100
        assert set(m.dims) == set(DIMENSIONS)


def test_reliable_car_scores_well_on_reliability():
    """Toyota Corolla hat die niedrigste Pannenquote -> Top-Zuverlaessigkeit."""
    conn = seeded_conn()
    ranked = score_models(conn, Criteria())
    by_label = {m.label.split(" (")[0]: m for m in ranked}
    corolla = next(m for lbl, m in by_label.items() if "Corolla" in m.label)
    focus = next(m for m in ranked if "Focus" in m.label)
    assert corolla.dims["reliability"] > focus.dims["reliability"]


def test_weights_shift_ranking():
    """Volles Gewicht auf Zuverlaessigkeit -> Corolla ganz oben."""
    conn = seeded_conn()
    crit = Criteria(weights={d: (1.0 if d == "reliability" else 0.0) for d in DIMENSIONS})
    ranked = score_models(conn, crit)
    assert "Corolla" in ranked[0].label


def test_price_filter_excludes_expensive():
    conn = seeded_conn()
    crit = Criteria(max_price=9000)
    ranked = score_models(conn, crit)
    # nur Ford Focus hat ein Angebot < 9000 -> nur er hat n_listings > 0
    with_listings = [m for m in ranked if m.n_listings > 0]
    assert with_listings and all("Focus" in m.label for m in with_listings)


def test_best_deal_discount_computed():
    conn = seeded_conn()
    ranked = score_models(conn, Criteria())
    golf = next(m for m in ranked if "Golf" in m.label)
    assert golf.best_deal_eur is not None
    assert golf.best_deal_discount_pct is not None


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns)-failed}/{len(fns)} bestanden")
    sys.exit(1 if failed else 0)
