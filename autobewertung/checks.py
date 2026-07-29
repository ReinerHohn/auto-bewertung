"""Kauf-Check: Tacho-Betrug-/Unfall-Checkliste + km-Plausibilitaet (mit Verschleissabgleich).

- CHECKLIST: abhakbare Profi-Pruefpunkte mit Erklaerung (Tacho, Unfall, Technik).
- mileage_plausibility: km/Jahr aus Laufleistung + Erstzulassung + Verdikt.
- wear_status: welche Teile bei DIESEM km-Stand laengst faellig waren (Belege
  verlangen!) bzw. als naechstes anstehen (Budget).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .wear import _occurrences, load_items

CHECKLIST = [
    ("🔢 Tacho / Kilometerstand", [
        ("km in ALLEN Steuergeräten gelesen & identisch?",
         "Kombi, Motor-ECU, Getriebe, ABS/ESP, Gateway, BCM, Airbag, teils Schlüssel speichern km. "
         "Betrüger drehen meist nur das Kombi zurück – Abweichung = Tachobetrug. Tool: Hella Gutmann "
         "mega macs, Bosch KTS, Autel, ODIS/ISTA/Xentry."),
        ("Fehlerspeicher-km plausibel (kein Eintrag über aktuellem Stand)?",
         "Jeder Fehler/DPF-Regeneration hat einen km-Zeitstempel → zeigt den echten km-Verlauf. "
         "Eintrag „bei 210.000 km“ bei angeblich 130.000 = entlarvt."),
        ("HU-Bericht-Kilometer über die Jahre steigend?",
         "TÜV/DEKRA notieren km bei jeder HU. carVertical/Historie zeigt den Verlauf."),
        ("Abnutzung passt zu km (Pedalgummis, Lenkrad, Sitz, Schaltknauf)?",
         "Speckiges Lenkrad/abgefahrene Pedale bei 60.000 km = Verdacht."),
        ("Scheckheft lückenlos + Stempel-km steigend?",
         "Marken-Servicehistorie (VW ServiceNet, BMW, Xentry) je VIN gegenchecken."),
    ]),
    ("💥 Unfall / Vorschäden", [
        ("Lackschichtdicke rundum gemessen (gleichmäßig)?",
         "Schichtdickenmessgerät: dicke/ungleiche Werte = nachlackiert/gespachtelt."),
        ("Spaltmaße gleichmäßig, Schrauben an Kotflügel/Türen/Haube unberührt?",
         "Frische/verdrehte Schrauben = Unfallreparatur."),
        ("Schweißnähte/Unterboden original (keine nachträglichen Nähte)?",
         "Nachträgliche Nähte = Strukturschaden."),
        ("VIN-Historie geprüft (carVertical / AutoDNA)?",
         "Aggregiert Schäden, Auktionen, km aus mehreren Ländern (kostenpflichtig, aber Gold wert)."),
        ("Unfallfreiheit schriftlich im Kaufvertrag zusichern lassen?",
         "Rechtliche Absicherung gegen arglistige Täuschung."),
    ]),
    ("🔧 Technik / Probefahrt", [
        ("Fehlerspeicher komplett ausgelesen (keine aktiven Fehler)?",
         "Vor dem Kauf Diagnose – versteckte Fehler kosten später."),
        ("Kaltstart selbst gemacht (Kettenrasseln, blauer/weißer Rauch)?",
         "Warmer Motor verdeckt Steuerketten-/Öl-/Kopfdichtungsprobleme."),
        ("Modellspezifische Schwachstelle deines Untermodells geprüft?",
         "Siehe Tab „Verschleiß“ – z. B. Tesla Querlenker, BMW N47-Kette, Ford EcoBoost Kühlung."),
        ("E-Auto: Batterie-Gesundheit (SoH) ausgelesen?",
         "State of Health via Diagnose/App – <90 % mindert Reichweite & Wert deutlich."),
        ("Ölzustand, Kühlmittel, Bremsflüssigkeit, Reifen-DOT geprüft?",
         "Verschlepptes Service + altes Gummi = versteckte Folgekosten."),
    ]),
]


def mileage_plausibility(mileage: int | None, first_reg: str | None, ref=None) -> dict | None:
    """km/Jahr + Verdikt aus Laufleistung und Erstzulassung ('YYYY-MM')."""
    if not mileage or not first_reg:
        return None
    ref = ref or datetime.now(timezone.utc)
    try:
        y = int(str(first_reg)[:4])
        m = int(str(first_reg)[5:7]) if len(str(first_reg)) >= 7 else 1
    except ValueError:
        return None
    age = max(0.3, (ref.year - y) + (ref.month - m) / 12.0)
    kmy = mileage / age
    if kmy < 5000:
        verdict, level = "auffällig niedrig – Rückdreh/Standschäden prüfen!", "warn"
    elif kmy < 8000:
        verdict, level = "niedrig (Wenigfahrer möglich – trotzdem prüfen)", "info"
    elif kmy <= 22000:
        verdict, level = "normal", "ok"
    elif kmy <= 32000:
        verdict, level = "hoch (Vielfahrer/Langstrecke)", "info"
    else:
        verdict, level = "sehr hoch – Verschleiß genau prüfen", "warn"
    return {"km_per_year": kmy, "age_years": age, "verdict": verdict, "level": level}


def wear_status(conn: sqlite3.Connection, model_id: int, variant: str | None, mileage: int):
    """(schon_faellig, demnaechst): Teile, die bei diesem km-Stand bereits faellig
    waren (Belege verlangen) bzw. als naechstes anstehen."""
    items = load_items(conn, model_id)
    if variant and variant != "alle":
        items = [i for i in items if i["variant"] in (variant, "alle")]
    done, upcoming = [], []
    for it in items:
        occ = _occurrences(it["at_km"], it["interval_km"], mileage)
        if occ >= 1:
            done.append(it)
        if mileage < it["at_km"]:
            nxt = it["at_km"]
        elif it["interval_km"]:
            nxt = it["at_km"] + occ * it["interval_km"]
        else:
            nxt = None
        if nxt and nxt > mileage:
            upcoming.append({**it, "next_km": nxt})
    return (sorted(done, key=lambda x: -x["cost_eur"]),
            sorted(upcoming, key=lambda x: x["next_km"]))


def due_soon(conn: sqlite3.Connection, model_id: int, variant: str | None,
             mileage: int, horizon_km: int = 15000) -> list[dict]:
    """Teile, die INNERHALB der naechsten `horizon_km` faellig werden.

    Genau die Warnung 'Achtung, in ~X km ist Teil Y faellig'.
    """
    items = load_items(conn, model_id)
    if variant and variant != "alle":
        items = [i for i in items if i["variant"] in (variant, "alle")]
    out = []
    for it in items:
        occ = _occurrences(it["at_km"], it["interval_km"], mileage)
        if mileage < it["at_km"]:
            nxt = it["at_km"]
        elif it["interval_km"]:
            nxt = it["at_km"] + occ * it["interval_km"]
        else:
            nxt = None
        if nxt and 0 < (nxt - mileage) <= horizon_km:
            out.append({**it, "next_km": nxt, "km_until": nxt - mileage})
    return sorted(out, key=lambda x: x["km_until"])


def carvertical_url(vin: str | None = None) -> str:
    base = "https://www.carvertical.com/de"
    return f"{base}/history?vin={vin}" if vin else base
