"""Tests fuer den Schwachstellen-Import (weak_real.csv)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.db import init_db, upsert_model
from autobewertung.sources.seed import SeedSource
from autobewertung.sources.weak_import import WeakPointImportSource

CSV = """make,model,component,description,severity,cost_eur,source_url,note
VW,Polo,Steuerkette,Steuerkettenlaengung 1.2 TSI (EA111),3,1500,https://example.com/polo-kette,frueh
VW,Polo,Kupplung,DSG DQ200 Trockenkupplung,2,1400,https://example.com/polo-dsg,
VW,Golf,Testschwachstelle,soll Seed-Eintraege NICHT loeschen,1,100,https://example.com/golf,
"""


def _weak(conn, model):
    return conn.execute(
        "SELECT component, description, severity, cost_eur, source, url FROM weak_point wp "
        "JOIN car_model cm ON cm.id=wp.model_id WHERE cm.model=?", (model,)).fetchall()


def built():
    conn = init_db(":memory:")
    SeedSource().collect(conn)
    # auto-entdecktes Modell (kein Seed) simulieren
    upsert_model(conn, "VW", "Polo", "auto-entdeckt", year_from=2005, year_to=2023)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(CSV); path = f.name
    WeakPointImportSource(csv_path=path).collect(conn)
    os.unlink(path)
    return conn


def test_import_adds_weak_points_to_auto_model():
    conn = built()
    polo = _weak(conn, "Polo")
    assert len(polo) == 2
    assert any("Steuerkette" == r["component"] and r["severity"] == 3 for r in polo)
    assert all(r["source"] == "real" for r in polo)


def test_source_url_and_cost_preserved():
    conn = built()
    kette = [r for r in _weak(conn, "Polo") if r["component"] == "Steuerkette"][0]
    assert kette["cost_eur"] == 1500
    assert kette["url"] == "https://example.com/polo-kette"


def test_real_does_not_delete_seed_weak_points():
    """weak_real ersetzt nur source='real', Seed-Schwachstellen bleiben erhalten."""
    conn = built()
    golf = _weak(conn, "Golf")
    assert any(r["source"] == "seed" for r in golf)   # DSG/Steuerkette aus Seed
    assert any(r["source"] == "real" for r in golf)   # Testschwachstelle


def test_reimport_is_idempotent():
    conn = built()
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(CSV); path = f.name
    WeakPointImportSource(csv_path=path).collect(conn)
    os.unlink(path)
    assert len(_weak(conn, "Polo")) == 2   # kein Doppeln nach zweitem Lauf


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
