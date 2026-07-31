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
        # Varianten-Infos aus vehicleDetails (Leistung, Reichweite)
        vd = l.get("vehicleDetails") or []

        def _detail(icon):
            return next((e.get("data") for e in vd if e.get("iconName") == icon), None)

        power_kw = None
        pp = _detail("speedometer")                 # "225 kW (306 PS)"
        if pp:
            mm = re.search(r"(\d+)\s*kW", pp)
            power_kw = int(mm.group(1)) if mm else None
        list_range = None
        rr = _detail("distance")                    # "409 km Reichweite"
        if rr:
            mm = re.search(r"(\d+)\s*km", rr.replace(".", ""))
            list_range = int(mm.group(1)) if mm else None
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
            "title": (veh.get("modelVersionInput")
                      or f"{veh.get('make', '')} {veh.get('model', '')}").strip(),
            "fuel": (tr.get("fuelType") or "").lower(),   # 'e'=Elektro, 'd'=Diesel, ...
            "rating": rating,
            "power_kw": power_kw,
            "list_range": list_range,
            "damaged": bool(veh.get("isCurrentlyDamaged")),
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
        spec = conn.execute(
            "SELECT vs.drivetrain, vs.range_km, cm.year_from, cm.year_to "
            "FROM vehicle_spec vs JOIN car_model cm ON cm.id=vs.model_id WHERE vs.model_id=?",
            (model_id,)).fetchone()
        dt = (spec["drivetrain"] if spec else "") or ""
        spec_range = spec["range_km"] if spec else None
        # Unfallwagen raus
        items = [it for it in items if not it.get("damaged")]
        # nur die richtige Generation (Slug /vw/golf liefert Golf II..VIII!)
        yf, yt = (spec["year_from"], spec["year_to"]) if spec else (None, None)
        if yf and yt:
            def _yr(it):
                try:
                    return int(str(it.get("first_reg"))[:4])
                except (TypeError, ValueError):
                    return None
            items = [it for it in items if not _yr(it) or yf <= _yr(it) <= yt]
        # nach Kraftstoff exakt zum modellierten Antrieb (AS24: b=Benzin, d=Diesel,
        # 2=Hybrid Elektro/Benzin, 3=Hybrid Elektro/Diesel, e=Elektro).
        FUEL_MAP = {"benzin": {"b"}, "diesel": {"d"}, "hybrid": {"2", "3"}, "elektro": {"e"}}
        allowed = FUEL_MAP.get(dt)
        if allowed:
            items = [it for it in items if not it["fuel"] or it["fuel"] in allowed]
        # variantengenau: E-Auto-Reichweite muss zur modellierten Akkugroesse passen
        # (blendet kleinere/groessere Akku-Varianten aus, z.B. 44 vs 72 kWh)
        if dt == "elektro" and spec_range:
            lo, hi = 0.72 * spec_range, 1.35 * spec_range
            items = [it for it in items
                     if not it.get("list_range") or lo <= it["list_range"] <= hi]
        n = 0
        for it in items:
            _record_listing(conn, model_id=model_id, source="autoscout24",
                            source_ref=it["source_ref"], title=it["title"], price=it["price"],
                            mileage_km=it["mileage_km"], first_reg=it["first_reg"],
                            plz=it["plz"], location=it["location"], url=it["url"],
                            power_kw=it.get("power_kw"), price_rating=it.get("rating"))
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
