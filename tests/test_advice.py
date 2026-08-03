"""Tests fuer die Kaufberatung: Achtungsliste, Verhandlungs-Munition, Dossier."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.advice import buy_dossier, model_watchpoints, negotiation_ammo
from autobewertung.db import init_db
from autobewertung.sources.seed import SeedSource
from autobewertung.sources.wear_import import WearImportSource


def _conn():
    conn = init_db(":memory:"); SeedSource().collect(conn); WearImportSource().collect(conn)
    return conn


def _golf(conn):
    return conn.execute("SELECT id FROM car_model WHERE model='Golf'").fetchone()["id"]


def test_watchpoints_prioritised():
    conn = _conn()
    wp, nr = model_watchpoints(conn, _golf(conn))
    assert wp and wp[0]["severity"] == 3          # schwerste zuerst
    assert isinstance(nr, int)
    # keine Erst-Wort-Duplikate durch Verschleiss-Ergaenzung
    firsts = [w["label"].split()[0].lower() for w in wp]
    assert len(firsts) == len(set(firsts))


def test_negotiation_ammo_sums_and_targets():
    conn = _conn(); golf = _golf(conn)
    amm = negotiation_ammo(conn, golf, None, price=9899, mileage=150000, first_reg="2017-03",
                           fair_price=13000, days_online=60, drivetrain="benzin")
    assert amm["args"] and amm["reduction"] > 0
    assert amm["target"] == round(9899 - amm["reduction"])
    # 12V darf nicht doppelt gezaehlt werden (km-Verschleiss + Alters-Check)
    n_12v = sum(1 for a in amm["args"] if "12v" in a["text"].lower())
    assert n_12v <= 1
    # lange Standzeit als Kontext-Hebel
    assert any("verhandlungsbereit" in c for c in amm["context"])


def test_ammo_empty_for_fresh_low_km_car():
    conn = _conn(); golf = _golf(conn)
    amm = negotiation_ammo(conn, golf, None, price=20000, mileage=20000, first_reg="2024-06",
                           fair_price=20000, days_online=2, drivetrain="benzin")
    assert amm["reduction"] == 0                  # nichts faellig, keine Masse


def test_dossier_has_sections():
    conn = _conn(); golf = _golf(conn)
    d = buy_dossier(conn, golf, "VW Golf VII", None, 9899, 150000, "2017-03",
                    13000, 60, "kleinanzeigen", "benzin", url="https://x")
    assert "# Kauf-Dossier: VW Golf VII" in d
    assert "## Worauf achten" in d and "## Verhandlung" in d
    assert "KEINE Gewährleistung" in d            # Privatverkauf-Hinweis
    assert "https://x" in d


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
