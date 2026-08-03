"""Tests fuer den Fehlercode-Deuter (OBD/DTC)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autobewertung.dtc import interpret


def test_exact_code_with_causes():
    r = interpret("P0087")
    assert r["matched"] == "exact" and r["severity"] == "danger"
    assert r["causes"] and any("Hochdruckpumpe" in c[0] for c in r["causes"])


def test_input_is_normalised():
    assert interpret("  p 0087 ")["code"] == "P0087"


def test_family_fallback_transmission():
    r = interpret("P0741")                       # nicht kuratiert
    assert r["matched"] == "family" and "Getriebe" in r["title"] and r["severity"] == "danger"


def test_misfire_single_cylinder():
    assert "Zylinder 4" in interpret("P0304")["title"]


def test_letter_families():
    assert "Airbag" in interpret("B1234")["title"]
    assert "ABS" in interpret("C1201")["title"]
    assert "Netzwerk" in interpret("U0100")["title"]


def test_manufacturer_specific_note():
    assert "erstellerspezifisch" in interpret("P1601")["note"]   # 2. Ziffer 1 -> mfr


def test_invalid_format():
    assert interpret("HELLO")["matched"] == "invalid"
    assert interpret("")["matched"] == "invalid"


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
