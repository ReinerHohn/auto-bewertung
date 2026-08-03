"""Fehlercode-Deuter (OBD-II / DTC) fuer den Gebrauchtwagenkauf.

`interpret(code)` gibt Klartext, Schwere, moegliche Ursachen (guenstig -> teuer)
und Pruefschritte. Kuratierte Liste fuer die kaufrelevanten Codes; fuer alle
uebrigen ein Fallback ueber die Code-Familie (Buchstabe + Subsystem-Ziffer),
sodass jeder gueltige Code eine brauchbare Einordnung bekommt.

Standard-OBD-Codes sind ein oeffentlicher Standard – hier in eigenen Worten
eingeordnet. Fuer die exakte, modellspezifische Ursache immer die Hinweise des
Diagnosegeraets (z.B. Hella Gutmann) heranziehen.
"""
from __future__ import annotations

import re

# Schweregrade: 'info' = meist guenstig/harmlos, 'warn' = pruefen/mittel,
# 'danger' = teuer/ernst. Ursachen als (Text, Kostentier).
CURATED: dict[str, dict] = {
    "P0087": {"title": "Kraftstoff-Raildruck / Systemdruck zu niedrig", "severity": "danger",
              "causes": [("Kraftstofffilter verstopft", "günstig ~30–100 €"),
                         ("Raildrucksensor / Druckregelventil defekt", "mittel ~150–400 €"),
                         ("Vorförder-/Niederdruckpumpe schwach", "mittel ~300–600 €"),
                         ("Hochdruckpumpe verschlissen (z.B. Bosch CP4)", "teuer ~800–2.000 €"),
                         ("Pumpe zerlegt → Späne in Rail+Injektoren", "kritisch ~4.000–8.000 €")],
              "checks": ["Status statisch (jetzt) vs. sporadisch?",
                         "Istwerte: Raildruck Soll vs. Ist unter Last – bricht Ist ein?",
                         "Freeze-Frame: Last/Drehzahl beim Fehler",
                         "Kraftstofffilter-Wechsel im Serviceheft?"]},
    "P0088": {"title": "Kraftstoff-Raildruck zu hoch", "severity": "warn",
              "causes": [("Druckregelventil (DRV) klemmt", "mittel ~150–400 €"),
                         ("Raildrucksensor defekt", "mittel ~150–300 €")],
              "checks": ["Raildruck Soll/Ist im Leerlauf", "Status prüfen"]},
    "P0299": {"title": "Turbolader/Kompressor – Unterdruck (Underboost)", "severity": "danger",
              "causes": [("Undichte Ladeluftschläuche/Schellen", "günstig ~50–200 €"),
                         ("Ladedruck-Steller/Unterdruckdose (VTG) fest", "mittel ~200–600 €"),
                         ("Turbolader verschlissen", "teuer ~900–2.000 €")],
              "checks": ["Ladedruck Soll vs. Ist unter Last", "Blauer Rauch beim Gasgeben?",
                         "Ladeluftrohr auf Öl prüfen"]},
    "P0234": {"title": "Turbolader – Überdruck (Overboost)", "severity": "danger",
              "causes": [("VTG-Leitschaufeln verkokt/fest", "mittel ~300–700 €"),
                         ("Ladedruckregelung/Wastegate defekt", "mittel ~200–600 €")],
              "checks": ["Ladedruck Soll/Ist", "Notlauf?"]},
    "P0300": {"title": "Zündaussetzer – mehrere/zufällige Zylinder", "severity": "danger",
              "causes": [("Zündkerzen/Zündspulen", "günstig ~150–400 €"),
                         ("Einspritzdüsen/Injektoren", "mittel ~300–1.500 €"),
                         ("Kompression/Motor (Ventile, Kette)", "teuer ~1.000 €+")],
              "checks": ["Aussetzer-Zähler je Zylinder (Istwerte)", "Kaltstart: rauer Lauf?",
                         "wandert der Aussetzer?"]},
    "P0401": {"title": "Abgasrückführung (AGR) – Durchfluss zu gering", "severity": "warn",
              "causes": [("AGR-Ventil verkokt", "mittel ~200–600 €"),
                         ("AGR-Kühler zu/undicht", "mittel ~300–800 €")],
              "checks": ["Kurzstrecken-Diesel? (verkokt schneller)", "Status/Häufigkeit"]},
    "P0420": {"title": "Katalysator – Wirkungsgrad zu niedrig (Bank 1)", "severity": "warn",
              "causes": [("Lambdasonde nach Kat träge", "günstig ~100–300 €"),
                         ("Katalysator verbraucht/defekt", "teuer ~400–1.200 €")],
              "checks": ["Lambdasonden-Signale vor/nach Kat", "Öl-/Kühlwasserverbrauch (killt Kat)?"]},
    "P2002": {"title": "Dieselpartikelfilter (DPF) – Wirkungsgrad zu niedrig", "severity": "danger",
              "causes": [("DPF zugesetzt (Kurzstrecke)", "mittel ~300–800 € Reinigung"),
                         ("DPF verbraucht (hohe Aschemasse)", "teuer ~1.000–2.500 €")],
              "checks": ["Istwerte: DPF-Beladung, Aschemasse, Regenerationsabstand",
                         "viele Regenerationen = am Ende"]},
    "P0011": {"title": "Nockenwellen-Verstellung / Steuerzeiten (Bank 1)", "severity": "danger",
              "causes": [("Steuerkette gelängt / Kettenspanner", "teuer ~800–2.500 €"),
                         ("Nockenwellenversteller/Magnetventil", "mittel ~150–500 €"),
                         ("niedriger Öldruck/altes Öl", "günstig ~Ölservice")],
              "checks": ["Kaltstart: Kettenrasseln?", "Ölwechsel-Historie",
                         "oft bei VAG 1.4/1.8/2.0 TSI + BMW N47/N20"]},
    "P0016": {"title": "Kurbelwelle/Nockenwelle – Zuordnung falsch (Steuerzeiten)", "severity": "danger",
              "causes": [("Steuerkette übergesprungen/gelängt", "teuer ~800–2.500 €"),
                         ("Nockenwellenversteller/Sensor", "mittel ~150–500 €")],
              "checks": ["Kaltstart-Geräusch", "Ölservice-Historie"]},
    "P0128": {"title": "Kühlmitteltemperatur bleibt zu niedrig / Thermostat", "severity": "warn",
              "causes": [("Thermostat hängt offen", "günstig ~120–300 €")],
              "checks": ["Erreicht der Motor Betriebstemperatur? (Istwert Kühlmitteltemp)"]},
    "P0217": {"title": "Motor – Überhitzung", "severity": "danger",
              "causes": [("Kühlsystem (Pumpe, Thermostat, Luft)", "mittel ~150–600 €"),
                         ("Zylinderkopfdichtung (Folge)", "teuer ~1.000 €+")],
              "checks": ["Kühlwasserstand/-farbe", "Öldeckel: Schleim?"]},
    "P0171": {"title": "Gemisch zu mager (Bank 1)", "severity": "warn",
              "causes": [("Falschluft (undichte Ansaugung)", "günstig ~50–300 €"),
                         ("Luftmassenmesser / Lambda", "mittel ~150–400 €")],
              "checks": ["Langzeit-Kraftstofftrim (LTFT) stark positiv?", "Ansaugung abhören"]},
    "P0700": {"title": "Getriebesteuerung – Fehler hinterlegt", "severity": "danger",
              "causes": [("Untercode im Getriebe-STG lesen!", "mittel bis teuer"),
                         ("DSG-Mechatronik/Kupplung", "teuer ~1.500–2.500 €")],
              "checks": ["Getriebe-STG separat auslesen (Untercode)", "Ruckeln bei 10–30 km/h?"]},
    "P0562": {"title": "Bordnetzspannung zu niedrig", "severity": "warn",
              "causes": [("12V-Batterie schwach/alt", "günstig ~120–200 €"),
                         ("Lichtmaschine/Regler", "mittel ~250–600 €")],
              "checks": ["Spannung: Ruhe ~12,5 V, laufend ~14 V?"]},
    "P0335": {"title": "Kurbelwellensensor – Signalfehler", "severity": "warn",
              "causes": [("Sensor defekt", "günstig ~80–250 €")],
              "checks": ["Startprobleme/Aussetzer?", "sporadisch vs. statisch"]},
    "P0341": {"title": "Nockenwellensensor – Signalfehler", "severity": "warn",
              "causes": [("Sensor defekt", "günstig ~80–250 €"),
                         ("Steuerkette gelängt (Folge)", "teuer – prüfen")],
              "checks": ["zusammen mit P0016/P0011? → Kette", "Kaltstart-Geräusch"]},
}

# alle P030x (Einzelzylinder-Aussetzer) auf einen Nenner
for _n in range(1, 13):
    CURATED.setdefault(f"P030{_n}" if _n < 10 else f"P03{_n}", {
        "title": f"Zündaussetzer – Zylinder {_n}", "severity": "warn",
        "causes": [("Zündkerze/Zündspule Zyl. " + str(_n), "günstig ~50–150 €"),
                   ("Injektor Zyl. " + str(_n), "mittel ~250–600 €"),
                   ("Kompression Zyl. " + str(_n) + " (Ventile/Kolben)", "teuer ~1.000 €+")],
        "checks": ["Aussetzer-Zähler bestätigt Zyl. " + str(_n) + "?",
                   "Kerze/Spule tauschweise prüfen (billig)", "sonst Kompressionstest"]})

_FAMILY_P = {
    "0": ("Gemischbildung – Kraftstoff & Luft", "warn"),
    "1": ("Gemischbildung – Kraftstoff & Luft", "warn"),
    "2": ("Kraftstoff-Einspritzung (Injektor-Kreis)", "warn"),
    "3": ("Zündanlage / Zündaussetzer", "warn"),
    "4": ("Abgas – Kat / AGR / DPF / Lambda", "warn"),
    "5": ("Leerlauf / Geschwindigkeit / Nebenaggregate", "info"),
    "6": ("Steuergerät & Ein-/Ausgangssignale", "info"),
    "7": ("Getriebe", "danger"), "8": ("Getriebe", "danger"),
    "9": ("Getriebe / SCR-Abgasnachbehandlung", "warn"),
    "A": ("Hybrid- / Elektroantrieb", "warn"), "B": ("Hybrid- / Elektroantrieb", "warn"),
}
_FAMILY_LETTER = {
    "B": ("Karosserie/Komfort – u.a. Airbag/SRS", "warn"),
    "C": ("Fahrwerk – ABS/ESP/Lenkung", "warn"),
    "U": ("Netzwerk/Kommunikation (Bus-Ausfall)", "warn"),
}


def interpret(code: str) -> dict:
    """Deutet einen OBD-Fehlercode. Gibt {code, matched, title, severity, causes,
    checks, note}. matched: 'exact' | 'family' | 'invalid'."""
    c = (code or "").strip().upper().replace(" ", "")
    if not re.fullmatch(r"[PBCU][0-3][0-9A-F]{3}", c):
        return {"code": code, "matched": "invalid", "title": "Kein gültiges OBD-Code-Format",
                "severity": "info", "causes": [], "checks": [],
                "note": "Format ist z.B. P0087 (Buchstabe P/B/C/U + 4 Zeichen)."}
    if c in CURATED:
        d = CURATED[c]
        return {"code": c, "matched": "exact", "note": "", **d}
    if c[0] == "P":
        subsys, sev = _FAMILY_P.get(c[2], ("Antriebsstrang – allgemein", "warn"))
        title = f"Antriebsstrang – {subsys}"
    else:
        subsys, sev = _FAMILY_LETTER.get(c[0], ("Unbekannter Bereich", "warn"))
        title = subsys
    mfr = c[1] in ("1", "3")
    note = ("Herstellerspezifischer Code – die genaue Bedeutung ist modellabhängig. "
            if mfr else "")
    note += "Klartext + bekannte Ursachen zeigt dir dein Diagnosegerät zum Modell/Code."
    return {"code": c, "matched": "family", "title": title, "severity": sev,
            "causes": [], "checks": ["Status statisch vs. sporadisch prüfen",
                                      "Untercode/Freeze-Frame im Diagnosegerät ansehen"],
            "note": note}
