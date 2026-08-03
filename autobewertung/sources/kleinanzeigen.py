"""Echte Inserate von kleinanzeigen.de (v.a. Privatverkaeufe – oft guenstiger).

Liest die KANONISCHE Modell-Seite /s-autos/{marke-modell}/k0c216 (Seite 1, ohne
Filter) und speichert die Angebote (Preis/km/EZ/PLZ/URL) je Modell. Diese Seite
ist laut robots.txt erlaubt; die gefilterten Suchen (:angebote, preis:, Umkreis,
Seiten ab 6) sind gesperrt und werden bewusst gemieden. Hoefliches Rate-Limit,
kein Login, keine Bot-Schutz-Umgehung – fuer persoenliche Recherche.

Kleinanzeigen liefert KEINE Generations-/Kraftstoff-Filter, daher filtern wir die
Treffer nach dem Baujahr-Bereich des Modells (wie beim AutoScout24-Adapter).
"""
from __future__ import annotations

import re
import sqlite3

from .base import CollectResult, Source
from .inserate import _record_listing

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
CAT = "k0c216"      # Kategorie "Autos"
MAX_PAGES = 5       # robots.txt sperrt seite:6+ -> nie darueber hinaus paginieren

# Slug-Overrides, wo make-model nicht direkt zur KA-Suche passt.
KA_SLUG = {
    ("VW", "ID.3 Pro"): "vw-id-3", ("VW", "ID.3 Pro S"): "vw-id-3",
    ("VW", "e-Golf"): "vw-e-golf", ("VW", "ID.4"): "vw-id-4",
    ("Hyundai", "Kona Elektro"): "hyundai-kona-elektro",
    ("Mercedes", "A-Klasse"): "mercedes-a-klasse",
    ("Opel", "Corsa-e"): "opel-corsa", ("Peugeot", "e-2008"): "peugeot-2008",
    ("Citroen", "e-C4"): "citroen-c4", ("Renault", "Megane E-Tech"): "renault-megane",
    ("Tesla", "Model 3"): "tesla-model-3", ("Tesla", "Model Y"): "tesla-model-y",
}


def _slug(make: str, model: str) -> str:
    if (make, model) in KA_SLUG:
        return KA_SLUG[(make, model)]
    return f"{make}-{model}".lower().replace(" ", "-").replace(".", "-").replace("--", "-")


def _price(text: str) -> float | None:
    m = re.search(r"(\d[\d.]*)\s*€", text or "")
    return float(m.group(1).replace(".", "")) if m else None


def _km(text: str) -> int | None:
    m = re.search(r"([\d.]+)\s*km", text or "")
    return int(m.group(1).replace(".", "")) if m else None


def _first_reg(text: str) -> str | None:
    m = re.search(r"(\d{2})/(\d{4})", text or "")      # "EZ 02/2011" -> "2011-02"
    return f"{m.group(2)}-{m.group(1)}" if m else None


def parse_kleinanzeigen(html: str) -> list[dict]:
    """Extrahiert Angebote aus der Inserate-Liste (article.aditem)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[dict] = []
    for a in soup.select(".aditem"):
        adid = a.get("data-adid")
        if not adid:
            continue
        price_el = a.select_one(".aditem-main--middle--price-shipping--price, "
                                ".aditem-main--middle--price")
        price = _price(price_el.get_text(" ", strip=True)) if price_el else None
        if not price:
            continue
        tags = [t.get_text(strip=True) for t in a.select(".simpletag")]
        km = next((_km(t) for t in tags if "km" in t.lower()), None)
        reg = next((_first_reg(t) for t in tags if "/" in t), None)
        title_el = a.select_one("h2 a, .text-module-begin a")
        loc = (a.select_one(".aditem-main--top--left") or a).get_text(" ", strip=True)
        mplz = re.search(r"\b(\d{5})\b", loc)
        href = a.get("data-href")
        out.append({
            "source_ref": adid, "price": price, "mileage_km": km, "first_reg": reg,
            "plz": mplz.group(1) if mplz else None,
            "location": re.sub(r"^.*?\d{5}\s*", "", loc).strip()[:40] or None,
            "url": ("https://www.kleinanzeigen.de" + href) if href else None,
            "title": title_el.get_text(strip=True) if title_el else "",
        })
    return out


class KleinanzeigenSource(Source):
    name = "kleinanzeigen"
    live = True

    def __init__(self, fetch=None, pages: int = 3):
        self._fetch = fetch
        self.pages = max(1, min(pages, MAX_PAGES))   # nie ueber die robots-Grenze

    def _page_url(self, slug: str, page: int) -> str:
        # Seite 1: /s-autos/{slug}/k0c216 ; Seite N: /s-autos/seite:N/{slug}/k0c216
        base = "https://www.kleinanzeigen.de/s-autos"
        return f"{base}/{slug}/{CAT}" if page <= 1 else f"{base}/seite:{page}/{slug}/{CAT}"

    def _get(self, url: str) -> str | None:
        if self._fetch:
            return self._fetch(url)
        import time
        import requests
        time.sleep(1.5)      # hoeflich
        r = requests.get(url, headers={"User-Agent": BROWSER_UA,
                                       "Accept-Language": "de-DE,de;q=0.9"}, timeout=25)
        return r.text if r.status_code == 200 else None

    def fetch_model(self, conn, model_id: int, make: str, model: str):
        """Holt Angebote fuer EIN Modell ueber Seite 1..pages (Baujahr-gefiltert)."""
        slug = _slug(make, model)
        by_ref: dict[str, dict] = {}
        for page in range(1, self.pages + 1):
            html = self._get(self._page_url(slug, page))
            if not html:
                break                              # kein Zugriff / keine weitere Seite
            page_items = parse_kleinanzeigen(html)
            if not page_items:
                break                              # leere Seite -> Ende
            for it in page_items:
                by_ref.setdefault(it["source_ref"], it)
        if not by_ref:
            return 0, "kein Zugriff (blockiert/robots) oder kein Treffer"
        items = list(by_ref.values())
        row = conn.execute("SELECT year_from, year_to FROM car_model WHERE id=?",
                           (model_id,)).fetchone()
        yf, yt = (row["year_from"], row["year_to"]) if row else (None, None)
        if yf and yt:
            def _yr(it):
                try:
                    return int(str(it.get("first_reg"))[:4])
                except (TypeError, ValueError):
                    return None
            items = [it for it in items if not _yr(it) or yf <= _yr(it) <= yt]
        n = 0
        for it in items:
            _record_listing(conn, model_id=model_id, source="kleinanzeigen",
                            source_ref=it["source_ref"], title=it["title"], price=it["price"],
                            mileage_km=it["mileage_km"], first_reg=it["first_reg"],
                            plz=it["plz"], location=it["location"], url=it["url"])
            n += 1
        conn.commit()
        return n, (f"{n} Angebote von Kleinanzeigen" if n else "keine (passenden) Angebote")

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        res = CollectResult(source=self.name)
        for m in conn.execute("SELECT id, make, model FROM car_model").fetchall():
            try:
                n, _ = self.fetch_model(conn, m["id"], m["make"], m["model"])
                res.inserted += n
            except Exception:
                pass
        res.notes = f"{res.inserted} Angebote (Kleinanzeigen)"
        return res
