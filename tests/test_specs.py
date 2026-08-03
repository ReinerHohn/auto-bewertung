"""Tests fuer den Spec-Import (partielles Update von vehicle_spec)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.db import init_db, upsert_model, upsert_spec
from autobewertung.sources.spec_import import SpecImportSource

CSV = (
    "make,model,vehicle_class,cons_l_100km,cons_kwh_100km,battery_kwh,range_km,"
    "dc_charge_kw,km_per_30min,insurance_eur,tax_eur,depr_pct_year,length_mm,width_mm,turning_m,note\n"
    "# comment ignoriert\n"
    "Testo,Kombi,mittelklasse,5.0,,,,,,600,250,0.12,4700,1830,11.2,Diesel\n"
    "Testo,Stromer,suv,,17.0,64,400,150,220,560,0,0.18,4200,1830,10.5,EV\n"
    "Fehl,Marke,kompakt,6.0,,,,,,,,,,,,\n"        # kein passendes Modell -> unmatched
)


def _run():
    conn = init_db(":memory:")
    m1 = upsert_model(conn, "Testo", "Kombi", "auto-entdeckt")
    upsert_spec(conn, m1, drivetrain="diesel", typical_price=15000, vehicle_class=None,
                cons_l_100km=None, insurance_eur=None)
    m2 = upsert_model(conn, "Testo", "Stromer", "auto-entdeckt")
    upsert_spec(conn, m2, drivetrain="elektro", typical_price=28000, range_km=None,
                km_per_30min=None)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        f.write(CSV); path = f.name
    res = SpecImportSource(csv_path=path).collect(conn)
    os.unlink(path)
    return conn, m1, m2, res


def test_sets_class_and_numeric_fields():
    conn, m1, m2, res = _run()
    r = conn.execute("SELECT vehicle_class, cons_l_100km, insurance_eur, tax_eur, "
                     "length_mm, turning_m FROM vehicle_spec WHERE model_id=?", (m1,)).fetchone()
    assert r["vehicle_class"] == "mittelklasse"
    assert r["cons_l_100km"] == 5.0 and r["insurance_eur"] == 600.0
    assert r["length_mm"] == 4700 and abs(r["turning_m"] - 11.2) < 1e-6


def test_ev_charge_fields_enable_ranking():
    conn, m1, m2, res = _run()
    r = conn.execute("SELECT range_km, km_per_30min, battery_kwh FROM vehicle_spec "
                     "WHERE model_id=?", (m2,)).fetchone()
    assert r["range_km"] == 400.0 and r["km_per_30min"] == 220.0 and r["battery_kwh"] == 64.0


def test_preserves_untouched_fields_and_reports_unmatched():
    conn, m1, m2, res = _run()
    # typical_price/drivetrain (nicht in CSV) bleiben erhalten
    r = conn.execute("SELECT drivetrain, typical_price FROM vehicle_spec WHERE model_id=?",
                     (m1,)).fetchone()
    assert r["drivetrain"] == "diesel" and r["typical_price"] == 15000
    assert res.updated == 2
    assert "Fehl Marke" in res.notes


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
