"""Watchlist: einzelne Inserate per URL verfolgen und Preisverlauf mitschreiben.

Ablauf (robots-konform, hoefliches Rate-Limit):
  1. URLs stehen in der `watch`-Tabelle (per `collect watch <url>` oder Dashboard).
  2. Bei jedem `collect run` wird jede URL EINZELN abgerufen (kein Massen-Scraping).
  3. `parse_listing` extrahiert Preis/Marke/Modell/km aus schema.org-JSON-LD
     (viele Portale liefern das) mit Regex-Fallbacks.
  4. Angebot + Preispunkt landen in der DB -> pro Angebot entsteht ein Verlauf.

Der Parser ist ohne Netz unittestbar (fixtures). Der Abruf selbst ist
domain-gedrosselt und respektiert robots.txt (siehe base.polite_get).
"""
from __future__ import annotations

import json
import re
import sqlite3

from .base import CollectResult, Source, polite_get
from .inserate import _record_listing

# Portal-Markennamen -> interne Kurzform (Angebot dem richtigen Modell zuordnen)
BRAND_NORMALIZE = {
    "volkswagen": "VW", "vw": "VW", "mercedes-benz": "Mercedes", "mercedes": "Mercedes",
    "skoda": "Skoda", "škoda": "Skoda", "bmw": "BMW", "audi": "Audi", "seat": "Seat",
    "opel": "Opel", "ford": "Ford", "toyota": "Toyota", "mazda": "Mazda", "kia": "Kia",
    "hyundai": "Hyundai", "honda": "Honda", "peugeot": "Peugeot", "renault": "Renault",
    "tesla": "Tesla",
}


# ---------------------------------------------------------------------------
# Parser (netzfrei testbar)
# ---------------------------------------------------------------------------

def _walk(obj):
    """Rekursiv alle dicts in einem JSON-Baum liefern."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _to_price(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if not v:
        return None
    s = str(v).strip()
    # reine Maschinenzahl: Punkt = Dezimaltrenner, max. 2 Nachkommastellen
    if re.fullmatch(r"\d+(\.\d{1,2})?", s):
        return float(s)
    s = re.sub(r"[^\d.,]", "", s)
    if "," in s:                              # deutsch "12.900,50" -> Punkt=Tausender, Komma=Dezimal
        s = s.replace(".", "").replace(",", ".")
    else:                                     # nur Punkte = Tausenderpunkte ("12.900", "1.234.567")
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(v) -> int | None:
    if v is None:
        return None
    digits = re.sub(r"[^\d]", "", str(v))
    return int(digits) if digits else None


def parse_listing(html: str, url: str = "") -> dict:
    """Extrahiert {price, title, make, model, mileage_km, first_reg} aus HTML.

    Primaer aus schema.org-JSON-LD (Car/Vehicle/Product/Offer), mit Regex-Fallback
    fuer den Preis. Fehlende Felder bleiben weg.
    """
    out: dict = {}
    for m in re.finditer(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.S | re.I):
        raw = m.group(1).strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in _walk(obj):
            types = node.get("@type", "")
            types = types if isinstance(types, list) else [types]
            if any(t in ("Car", "Vehicle", "Product", "IndividualProduct") for t in types):
                if node.get("name") and "title" not in out:
                    out["title"] = node["name"]
                brand = node.get("brand") or node.get("manufacturer")
                if isinstance(brand, dict):
                    brand = brand.get("name")
                if brand and "make" not in out:
                    out["make"] = str(brand)
                mdl = node.get("model")
                if isinstance(mdl, str) and "model" not in out:
                    out["model"] = mdl
                odo = node.get("mileageFromOdometer")
                if isinstance(odo, dict):
                    odo = odo.get("value")
                if odo and "mileage_km" not in out:
                    out["mileage_km"] = _to_int(odo)
                mdate = node.get("vehicleModelDate") or node.get("productionDate")
                if mdate and "first_reg" not in out:
                    out["first_reg"] = str(mdate)[:7]
            # Preis aus Offer(s)
            if "price" not in out:
                if "price" in node:
                    p = _to_price(node.get("price"))
                    if p:
                        out["price"] = p
                offers = node.get("offers")
                for off in (offers if isinstance(offers, list) else [offers]):
                    if isinstance(off, dict) and "price" not in out:
                        p = _to_price(off.get("price") or (off.get("priceSpecification") or {}).get("price"))
                        if p:
                            out["price"] = p

    if "price" not in out:                    # Regex-Fallback (meta / data-Attribute)
        m = (re.search(r'"price"\s*:\s*"?([\d.,]{3,})', html)
             or re.search(r'itemprop=["\']price["\'][^>]*content=["\']([\d.,]+)', html))
        if m:
            p = _to_price(m.group(1))
            if p:
                out["price"] = p

    # Marke aus dict-freien Modellnamen ableiten
    if out.get("make"):
        out["make"] = BRAND_NORMALIZE.get(out["make"].strip().lower(), out["make"].strip())
    return out


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

def _match_or_create_model(conn, make: str | None, model: str | None) -> int | None:
    """Ordnet ein Angebot einem vorhandenen Modell zu oder legt eins an."""
    if not make and not model:
        return None
    if make and model:
        # bestes vorhandenes Modell per Marke + Modell-Praefix
        row = conn.execute(
            "SELECT id FROM car_model WHERE lower(make)=lower(?) "
            "AND lower(?) LIKE lower(model)||'%' ORDER BY length(model) DESC LIMIT 1",
            (make, model)).fetchone()
        if row:
            return row["id"]
    from ..db import upsert_model
    return upsert_model(conn, make or "Unbekannt", model or "Unbekannt")


class WatchlistSource(Source):
    name = "watchlist"
    live = True

    def __init__(self, fetch=None):
        # fetch(url) -> Objekt mit .status_code/.text; Default = polite_get.
        self._fetch = fetch or (lambda u: polite_get(u, min_interval_s=3.0))

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        res = CollectResult(source=self.name)
        urls = [r["url"] for r in conn.execute("SELECT url FROM watch")]
        if not urls:
            res.notes = "keine verfolgten URLs (per `collect watch <url>` oder Dashboard hinzufuegen)"
            return res
        ok = fail = 0
        for url in urls:
            try:
                resp = self._fetch(url)
            except Exception as e:  # Netzfehler soll den Lauf nicht abbrechen
                fail += 1
                res.notes += f"[{url}: {e}] "
                continue
            if resp is None or getattr(resp, "status_code", 0) != 200:
                fail += 1
                continue
            data = parse_listing(resp.text, url)
            if not data.get("price"):
                fail += 1
                continue
            mid = _match_or_create_model(conn, data.get("make"), data.get("model"))
            action = _record_listing(
                conn, model_id=mid, source="watch", source_ref=url,
                title=data.get("title") or url, price=data["price"],
                mileage_km=data.get("mileage_km"), first_reg=data.get("first_reg"),
                url=url)
            setattr(res, action, getattr(res, action) + 1)
            ok += 1
        conn.commit()
        res.notes = f"{ok} aktualisiert, {fail} fehlgeschlagen" + (" " + res.notes if res.notes else "")
        return res
