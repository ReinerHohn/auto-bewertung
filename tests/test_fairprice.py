"""Tests fuer das statistische Fair-Preis-Modell (synthetisch, deterministisch)."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung import fairprice
from autobewertung.db import init_db

# bekanntes Preisgesetz: log(preis) = log(base) - 0.09*alter - 0.05*log1p(km)
B_AGE, B_KM = -0.09, -0.05


def _law(base, age, km):
    return base * math.exp(B_AGE * age + B_KM * math.log1p(km))


def _model(conn, make, model):
    return conn.execute("INSERT INTO car_model(make,model) VALUES (?,?)", (make, model)).lastrowid


def _listing(conn, mid, ref, price, age, km, kw=100):
    reg = f"{2026 - age:04d}-06"
    conn.execute(
        "INSERT INTO listing(model_id,source,source_ref,title,price,mileage_km,"
        "first_reg,power_kw,active,first_seen,last_seen) "
        "VALUES (?,?,?,?,?,?,?,?,1,'t0','t0')",
        (mid, "autoscout24", ref, "x", round(price), km, reg, kw))
    return conn.execute("SELECT id FROM listing WHERE source_ref=?", (ref,)).fetchone()["id"]


def _seed_clean(conn, mid, base, tag, n=10):
    """n saubere Angebote entlang des Preisgesetzes (leichte Variation)."""
    for i in range(n):
        age = 2 + i
        km = age * 12000 + i * 4000            # km variiert unabhaengig vom Alter
        var = 1.0 + 0.01 * (1 if i % 2 else -1)  # +-1% Rauschen -> sigma>0
        _listing(conn, mid, f"{tag}{i}", _law(base, age, km) * var, age, km)


def _build():
    conn = init_db(":memory:")
    a = _model(conn, "Marke", "A"); _seed_clean(conn, a, 22000, "a")
    b = _model(conn, "Marke", "B"); _seed_clean(conn, b, 12000, "b")
    c = _model(conn, "Marke", "C")                 # nur 2 Angebote -> kein Basiswert
    _listing(conn, c, "c0", 9000, 4, 60000); _listing(conn, c, "c1", 8000, 6, 90000)
    # injizierte Ausreisser bei Modell A (age 5, km 60000)
    under = _listing(conn, a, "a_under", _law(22000, 5, 60000) * 0.75, 5, 60000)
    over = _listing(conn, a, "a_over", _law(22000, 5, 60000) * 1.30, 5, 60000)
    conn.commit()
    return conn, a, b, c, under, over


def test_fit_recovers_depreciation_signs():
    conn, *_ = _build()
    m = fairprice.fit(conn)
    assert m is not None
    assert m.b_age < 0 and m.b_logkm < 0          # aelter/mehr km -> guenstiger
    assert m.r2 > 0.9                              # sauberes Gesetz -> hoher Fit


def test_flags_underpriced_and_overpriced():
    conn, a, b, c, under, over, *_ = _build()
    est = fairprice.estimate_listings(conn)
    assert est[under].resid_pct < -0.15           # ~-25% unter fair
    assert est[under].resid_eur < 0
    assert est[over].resid_pct > 0.15             # ~+30% ueber fair


def test_sparse_model_has_no_estimate():
    conn, a, b, c, *_ = _build()
    m = fairprice.fit(conn)
    assert c not in m.base                          # < MIN_PER_MODEL -> kein Basiswert
    est = fairprice.estimate_listings(conn, m)
    c_listings = [r["id"] for r in conn.execute("SELECT id FROM listing WHERE model_id=?", (c,))]
    assert all(lid not in est for lid in c_listings)


def test_prediction_matches_law_for_clean_point():
    conn, a, *_ = _build()
    m = fairprice.fit(conn)
    # Vorhersage fuer einen sauberen Punkt sollte nahe am Gesetz liegen
    pred = m.predict(a, age=5, km=60000, kw=100)
    assert pred is not None
    assert abs(pred - _law(22000, 5, 60000)) / _law(22000, 5, 60000) < 0.10


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
