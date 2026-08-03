"""Hoehere Kaufberatung – verdichtet die Einzel-Checks zu Handlung.

- model_watchpoints: Top-Schwachstellen dieses Modells zum gezielt Pruefen.
- negotiation_ammo: konkrete Verhandlungs-Argumente + geschaetzter Nachlass.
- buy_dossier: alles zusammen als druck-/kopierbare Zusammenfassung.
"""
from __future__ import annotations

import sqlite3

from .checks import (age_service_checks, due_soon, emission_note, mileage_plausibility,
                     next_hu, warranty_note, zahnriemen_time_status)
from .wear import load_items


def _eur(v) -> str:
    return f"{v:,.0f} €".replace(",", ".") if v is not None else "–"


def model_watchpoints(conn: sqlite3.Connection, model_id: int,
                      variant: str | None = None, top: int = 5) -> tuple[list[dict], int]:
    """Worauf man bei DIESEM Modell zuerst achten sollte (Schwachstellen + teurer
    modellspezifischer Verschleiss), nach Schwere/Kosten priorisiert.
    Gibt (Top-Punkte, Anzahl Rueckrufe)."""
    out: list[dict] = []
    for r in conn.execute(
        "SELECT component, description, severity, cost_eur FROM weak_point "
        "WHERE model_id=? ORDER BY severity DESC, IFNULL(cost_eur,0) DESC", (model_id,)):
        out.append({"label": r["component"] or "Schwachstelle", "detail": r["description"],
                    "severity": r["severity"] or 2, "cost": r["cost_eur"]})
    # teurer, modellspezifischer Verschleiss (Variante) ergaenzen, falls noch nicht dabei
    items = load_items(conn, model_id)
    if variant and variant != "alle":
        items = [i for i in items if i["variant"] in (variant, "alle")]
    have = {o["label"].split()[0].lower() for o in out if o["label"]}
    for i in sorted(items, key=lambda x: -(x["cost_eur"] or 0)):
        key = i["component"].split()[0].lower() if i["component"] else ""
        if (i["variant"] != "alle" or (i["cost_eur"] or 0) >= 800) and key and key not in have:
            out.append({"label": i["component"], "detail": f"typ. ~{i['at_km']:,} km".replace(",", "."),
                        "severity": 2, "cost": i["cost_eur"]})
            have.add(key)
    n_recalls = conn.execute("SELECT COUNT(*) c FROM recall WHERE model_id=?", (model_id,)).fetchone()["c"]
    out.sort(key=lambda o: (-o["severity"], -(o["cost"] or 0)))
    return out[:top], n_recalls


def negotiation_ammo(conn: sqlite3.Connection, model_id: int, variant: str | None,
                     price: float | None, mileage: int | None, first_reg: str | None,
                     fair_price: float | None, days_online: int | None,
                     drivetrain: str | None, ref=None) -> dict:
    """Konkrete Verhandlungs-Argumente + geschaetzte Verhandlungsmasse (EUR).

    Bündelt anstehende/ueberfaellige Kosten (Reparaturen, HU, Zahnriemen, Service),
    offene Rueckrufe, hohe km und lange Standzeit. Gibt {args, context, reduction,
    target}. 'reduction' = Summe konkret bezifferbarer Posten (Verhandlungsmasse)."""
    args: list[dict] = []     # {text, eur}
    context: list[str] = []   # Hebel ohne festen EUR-Betrag

    # 1) anstehende Reparaturen im naechsten Fenster
    if mileage is not None:
        for s in due_soon(conn, model_id, variant, mileage, horizon_km=20000):
            args.append({"text": f"{s['component']} steht in ~{s['km_until']:,} km an".replace(",", "."),
                         "eur": s["cost_eur"]})
    # 2) Zahnriemen nach Zeit ueberfaellig
    zr = zahnriemen_time_status(conn, model_id, variant, first_reg, ref)
    if zr and zr["due"]:
        args.append({"text": f"Zahnriemen zeitlich fällig ({zr['age_years']:.0f} J) – Wechselbeleg?",
                     "eur": zr["cost"]})
    # 3) HU bald faellig
    hu = next_hu(first_reg, ref)
    if hu and hu["months_until"] <= 6:
        args.append({"text": f"HU fällig ~{hu['due']} (Kosten + Mängelrisiko)", "eur": 120})
    # 4) alters-/zeitbasierte Faelligkeiten (Bremsfluessigkeit, 12V ...), ohne
    #    das zu doppeln, was der km-Verschleiss oben schon nennt
    _seen = " ".join(a["text"].lower() for a in args)
    for c in age_service_checks(first_reg, mileage, drivetrain, ref):
        if c["level"] != "warn":
            continue
        topic = "12v" if "12V" in c["text"] else ("bremsfl" if "Bremsflüssigkeit" in c["text"] else None)
        if topic and topic in _seen:
            continue                        # schon ueber km-Verschleiss abgedeckt
        eur = 150 if topic == "12v" else (80 if topic == "bremsfl" else 0)
        if eur:
            args.append({"text": c["text"].split("**")[1], "eur": eur})
    # 5) Kontext-Hebel ohne festen Betrag
    n_recalls = conn.execute("SELECT COUNT(*) c FROM recall WHERE model_id=?", (model_id,)).fetchone()["c"]
    if n_recalls:
        context.append(f"{n_recalls} offene(r) Rückruf(e) – nachweisen lassen, dass erledigt")
    mp = mileage_plausibility(mileage, first_reg, ref)
    if mp and mp["km_per_year"] > 25000:
        context.append(f"hohe Laufleistung (~{mp['km_per_year']:.0f} km/J) drückt den Wert")
    if days_online is not None and days_online >= 45:
        context.append(f"seit {days_online} Tagen online – Verkäufer ist verhandlungsbereit")
    if fair_price and price and price > fair_price * 1.02:
        context.append(f"{(price/fair_price-1)*100:.0f} % über fairem Marktwert (~{_eur(fair_price)})")
    em = emission_note(drivetrain, int(str(first_reg)[:4]) if first_reg else None)
    if em and em[0] == "warn":
        context.append("alter Diesel – Umweltzonen/Fahrverbot-Risiko drückt den Preis")

    reduction = sum(a["eur"] or 0 for a in args)
    target = round(price - reduction) if price else None
    return {"args": args, "context": context, "reduction": reduction, "target": target}


def buy_dossier(conn: sqlite3.Connection, model_id: int, label: str, variant: str | None,
                price: float | None, mileage: int | None, first_reg: str | None,
                fair_price: float | None, days_online: int | None, source: str | None,
                drivetrain: str | None, url: str | None = None, ref=None) -> str:
    """Alles Wichtige zu EINEM Auto als kopierbares Markdown-Dossier (mitnehmen!)."""
    L = [f"# Kauf-Dossier: {label}", ""]
    L.append(f"- Preis: **{_eur(price)}**"
             + (f" · fair ~{_eur(fair_price)}" if fair_price else "")
             + (f" · {mileage:,} km".replace(",", ".") if mileage else "")
             + (f" · EZ {first_reg}" if first_reg else ""))
    if url:
        L.append(f"- Inserat: {url}")
    wn = warranty_note(source)
    if wn:
        L.append(f"- Gewährleistung: {wn.replace('**','')}")
    hu = next_hu(first_reg, ref)
    if hu:
        L.append(f"- HU/TÜV: planmäßig ~{hu['due']} (in {hu['months_until']} Mon.) – Bericht zeigen lassen")
    L.append("")

    wp, n_recalls = model_watchpoints(conn, model_id, variant)
    L.append("## Worauf achten (modellspezifisch)")
    for w in wp:
        L.append(f"- **{w['label']}**: {w['detail']}" + (f" (~{_eur(w['cost'])})" if w["cost"] else ""))
    if n_recalls:
        L.append(f"- ⚠️ **{n_recalls} Rückruf(e)** – per FIN prüfen, ob erledigt")
    L.append("")

    amm = negotiation_ammo(conn, model_id, variant, price, mileage, first_reg,
                           fair_price, days_online, drivetrain, ref)
    L.append("## Verhandlung")
    if amm["args"]:
        L.append(f"Verhandlungsmasse ~**{_eur(amm['reduction'])}** → Zielpreis ~**{_eur(amm['target'])}**:")
        for a in amm["args"]:
            L.append(f"- {a['text']}" + (f" (~{_eur(a['eur'])})" if a["eur"] else ""))
    for c in amm["context"]:
        L.append(f"- {c}")
    L.append("")

    checks = age_service_checks(first_reg, mileage, drivetrain, ref)
    if checks:
        L.append("## Alters-/Zeit-Checks")
        for c in checks:
            L.append(f"- {c['text'].replace('**','')}")
    return "\n".join(L)
