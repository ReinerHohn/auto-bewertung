"""Tests fuer den Rueckruf-Import (CSV) und den NHTSA-Adapter (Mock)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.db import init_db
from autobewertung.sources.recalls import NhtsaRecallSource, RecallImportSource
from autobewertung.sources.seed import SeedSource


def built():
    conn = init_db(":memory:"); SeedSource().collect(conn)
    RecallImportSource().collect(conn)
    return conn


def _recalls(conn, model):
    return conn.execute(
        "SELECT kba_code, description FROM recall rc JOIN car_model cm ON cm.id=rc.model_id "
        "WHERE cm.model=?", (model,)).fetchall()


def test_import_replaces_seed_with_real():
    conn = built()
    focus = _recalls(conn, "Focus")
    assert any("EcoBoost" in r["description"] or "Kupplung" in r["description"] for r in focus)


def test_kba_code_present_where_known():
    conn = built()
    kona = _recalls(conn, "Kona Elektro")
    assert any(r["kba_code"] == "16242R" for r in kona)   # echte KBA-Nummer


def test_id3_variants_both_get_recalls():
    conn = built()
    for model in ("ID.3 Pro", "ID.3 Pro S"):
        assert len(_recalls(conn, model)) >= 1


def test_empty_kba_code_allows_multiple_per_model():
    """Mehrere Rueckrufe ohne KBA-Nummer je Modell (NULL != NULL im UNIQUE)."""
    conn = built()
    tesla = _recalls(conn, "Model 3")
    assert len([r for r in tesla if r["kba_code"] is None]) >= 2


def test_nhtsa_mock_adds_recall():
    conn = init_db(":memory:"); SeedSource().collect(conn)
    payload = json.dumps({"results": [
        {"NHTSACampaignNumber": "21V835000", "Component": "SUSPENSION",
         "Summary": "Lateral link fastener may loosen", "ReportReceivedDate": "2021-11-01"}]})
    NhtsaRecallSource(fetch=lambda url: payload).collect(conn)
    tesla = _recalls(conn, "Model 3")
    assert any(r["kba_code"] == "21V835000" for r in tesla)


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