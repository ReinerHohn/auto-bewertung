"""Echte Inserate von AutoScout24 (robots erlaubt /lst/; hoefliches Rate-Limit).

Liest die eingebettete __NEXT_DATA__-JSON der Suchergebnisseite und speichert die
Angebote (Preis/km/EZ/PLZ/URL) je Modell in die listing-Tabelle. Wiederholte
Aufrufe schreiben den Preisverlauf fort.

Hinweis: nur oeffentliche Suchseiten, kein Login/Umgehung von Bot-Schutz; fuer
persoenliche Recherche. mobile.de blockt automatisierte Abrufe (403).
"""
from __future__ import annotations

import json
import re
import sqlite3

from .base import CollectResult, Source
from .inserate import _record_listing

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"

# AS24-Slugs, wo unser Modellname nicht direkt passt (Motor-/Varianten-Suffixe)
AS24_SLUG = {
    ("VW", "ID.3 Pro"): ("vw", "id.3"), ("VW", "ID.3 Pro S"): ("vw", "id.3"),
    ("VW", "e-Golf"): ("vw", "e-golf"),
    ("Hyundai", "Kona Elektro"): ("hyundai", "kona"),
    ("Hyundai", "Ioniq 5"): ("hyundai", "ioniq-5"),
    ("Kia", "e-Niro"): ("kia", "e-niro"), ("Kia", "EV6"): ("kia", "ev6"),
    ("Kia", "Ceed"): ("kia", "ceed"),
    ("MG", "MG4"): ("mg", "mg4"), ("MG", "5 EV"): ("mg", "mg5"), ("MG", "ZS EV"): ("mg", "zs"),
    ("Renault", "Megane E-Tech"): ("renault", "megane-e-tech-electric"),
    ("Renault", "Megane"): ("renault", "megane"), ("Renault", "Zoe"): ("renault", "zoe"),
    ("Peugeot", "e-2008"): ("peugeot", "e-2008"), ("Peugeot", "308"): ("peugeot", "308"),
    ("Citroen", "e-C4"): ("citroen", "e-c4"),
    ("Mazda", "MX-30"): ("mazda", "mx-30"), ("Mazda", "3"): ("mazda", "3"),
    ("Opel", "Corsa-e"): ("opel", "corsa-e"), ("Opel", "Astra"): ("opel", "astra"),
    ("Nissan", "Leaf e+"): ("nissan", "leaf"), ("BMW", "i3"): ("bmw", "i3"),
    ("BMW", "3er"): ("bmw", "3er"), ("Tesla", "Model 3"): ("tesla", "model-3"),
    ("Cupra", "Born"): ("cupra", "born"), ("Fiat", "600e"): ("fiat", "600e"),
    ("Polestar", "2"): ("polestar", "2"),
}


def _slug(make: str, model: str) -> tuple[str, str]:
    if (make, model) in AS24_SLUG:
        return AS24_SLUG[(make, model)]
    return (make.lower().replace(" ", "-"),
            model.lower().replace(" ", "-").replace(".", "-"))


def parse_autoscout24(html: str) -> list[dict]:
    """Extrahiert Angebote aus dem __NEXT_DATA__-JSON."""
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    listings = (data.get("props", {}).get("pageProps", {}) or {}).get("listings") or []
    out = []
    for l in listings:
        if not isinstance(l, dict):
            continue
        pr = l.get("price") or {}
        price = pr.get("priceRaw")
        if not price:
            continue
        rating = pr.get("priceEvaluation") or None   # 1=sehr gut .. 5=hoch (0/None=keine)
        tr = l.get("tracking") or {}
        veh = l.get("vehicle") or {}
        loc = l.get("location") or {}
        fr = tr.get("firstRegistration")            # "MM-YYYY"
        first_reg = f"{fr[3:]}-{fr[:2]}" if fr and len(fr) == 7 and "-" in fr else None
        try:
            mileage = int(tr.get("mileage")) if tr.get("mileage") else None
        except (TypeError, ValueError):
            mileage = None
        out.append({
            "source_ref": l.get("id"),
            "price": float(price),
            "mileage_km": mileage,
            "first_reg": first_reg,
            "plz": loc.get("zip"),
            "location": loc.get("city"),
            "url": ("https://www.autoscout24.de" + l["url"]) if l.get("url") else None,
            "title": f"{veh.get('make', '')} {veh.get('model', '')}".strip(),
            "fuel": (tr.get("fuelType") or "").lower(),   # 'e'=Elektro, 'd'=Diesel, ...
            "rating": rating,
        })
    return out


class AutoScout24Source(Source):
    name = "autoscout24"
    live = True

    def __init__(self, fetch=None):
        self._fetch = fetch

    def _get(self, url: str) -> str | None:
        if self._fetch:
            return self._fetch(url)
        # robots von AS24 sperrt die FILTER-Suche (/lst?...&) - wir holen bewusst
        # nur die KANONISCHE, oeffentlich indexierte Modellseite /lst/marke/modell
        # (ohne die gesperrten Query-Parameter). Pythons robotparser interpretiert
        # '/lst/?' zu breit; wir meiden die gesperrten Muster explizit.
        import time
        import requests
        time.sleep(1.5)  # hoeflich
        r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=25)
        return r.text if r.status_code == 200 else None

    def fetch_model(self, conn, model_id: int, make: str, model: str, size: int = 20):
        """Holt echte Angebote fuer EIN Modell. Gibt (anzahl, hinweis)."""
        mk, md = _slug(make, model)
        url = f"https://www.autoscout24.de/lst/{mk}/{md}"   # kanonisch, ohne Query
        html = self._get(url)
        if not html:
            return 0, "kein Zugriff (blockiert/robots) oder kein Treffer"
        items = parse_autoscout24(html)
        # nach Kraftstoff filtern (Slug wie /hyundai/kona mischt Benziner + Elektro)
        spec = conn.execute("SELECT drivetrain FROM vehicle_spec WHERE model_id=?", (model_id,)).fetchone()
        dt = (spec["drivetrain"] if spec else "") or ""
        if dt == "elektro":
            items = [it for it in items if it["fuel"] == "e"]
        elif dt in ("benzin", "diesel", "hybrid"):
            items = [it for it in items if it["fuel"] != "e"]
        n = 0
        for it in items:
            _record_listing(conn, model_id=model_id, source="autoscout24",
                            source_ref=it["source_ref"], title=it["title"], price=it["price"],
                            mileage_km=it["mileage_km"], first_reg=it["first_reg"],
                            plz=it["plz"], location=it["location"], url=it["url"],
                            price_rating=it.get("rating"))
            n += 1
        conn.commit()
        return n, (f"{n} echte Angebote von AutoScout24" if n else "keine Angebote gefunden")

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        """Holt echte Angebote fuer ALLE Modelle (langsam, nur explizit)."""
        res = CollectResult(source=self.name)
        for m in conn.execute("SELECT id, make, model FROM car_model").fetchall():
            n, _ = self.fetch_model(conn, m["id"], m["make"], m["model"])
            res.inserted += n
        res.notes = f"{res.inserted} echte Angebote (AutoScout24)"
        return res
