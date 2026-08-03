"""Auto-Discovery: neue Modelle aus den AS24-Live-Angeboten erkennen + anlegen.

Holt die KANONISCHEN Marken-Seiten /lst/{marke} (robots-erlaubt, ohne die
gesperrten Query-Parameter), gruppiert die Angebote nach Modell und legt Modelle,
die noch NICHT in der DB sind, mit abgeleiteter Minimal-Spec an:

    Antrieb  <- Kraftstoff der Angebote (Mehrheit)
    typ.Preis<- Median der Angebotspreise
    Baujahr  <- min/max Erstzulassung
    Reichweite/Leistung <- Median aus den Angeboten (E-Auto)

So waechst die Abdeckung ohne Handarbeit. Neue Modelle sind ueber
generation='auto-entdeckt' klar markiert und koennen spaeter ueber Seed/CSV mit
Echtdaten (Verbrauch, Klasse, Zuverlaessigkeit) vertieft werden.

Nur oeffentliche Suchseiten, hoefliches Rate-Limit, keine Bot-Schutz-Umgehung.
Nicht im Default-Lauf (Netz, langsam) -> `collect discover` bzw. `run --only discover`.
"""
from __future__ import annotations

import sqlite3
import statistics

from .autoscout24 import BROWSER_UA, parse_autoscout24
from .base import CollectResult, Source
from .inserate import _record_listing

# (kanonische Marke, AS24-Slug) - welche Marken abgescannt werden. Marken mit
# sauberen Modellnamen; BMW/Mercedes ausgelassen (Modellfeld = Trim/Motor-Kuerzel).
SCAN_MAKES = [
    ("VW", "vw"), ("Skoda", "skoda"), ("Seat", "seat"), ("Audi", "audi"),
    ("Opel", "opel"), ("Ford", "ford"), ("Toyota", "toyota"), ("Mazda", "mazda"),
    ("Hyundai", "hyundai"), ("Kia", "kia"), ("Renault", "renault"),
    ("Peugeot", "peugeot"), ("Citroen", "citroen"), ("Nissan", "nissan"),
    ("Dacia", "dacia"), ("Fiat", "fiat"), ("Cupra", "cupra"), ("MG", "mg"),
    ("Honda", "honda"), ("Suzuki", "suzuki"), ("Volvo", "volvo"),
    ("Mini", "mini"), ("Tesla", "tesla"), ("Polestar", "polestar"),
]

# AS24 haengt Karosserie-Varianten an den Modellnamen -> auf den Kern kuerzen,
# damit z.B. "Passat Variant" und "Passat" nicht zwei Modelle werden.
BODY_SUFFIXES = (" Sportstourer", " Sports Tourer", " Sporttourer", " Variant",
                 " Grandtour", " Grand Coupe", " Sportback", " Avant", " Estate",
                 " Tourer", " Kombi", " Limousine", " Fastback", " SW", " ST")

# Nutzfahrzeuge/Transporter/GrossVANs raus (kein Pkw-Fokus).
SKIP_HINTS = ("transporter", "caddy", "crafter", "caravelle", "multivan", "amarok",
              "sprinter", "vito", "citan", "tourneo", "transit", "proace", "expert",
              "jumpy", "jumper", "boxer", "ducato", "doblo", "scudo", "partner",
              "berlingo", "kangoo", "trafic", "master", "movano", "nv200", "nv300")

FUEL_TO_DRIVETRAIN = {"b": "benzin", "d": "diesel", "e": "elektro", "2": "hybrid", "3": "hybrid"}
DRIVETRAIN_TO_FUEL = {"benzin": "Benzin", "diesel": "Diesel", "elektro": "Elektro", "hybrid": "Hybrid"}


def _norm_model(model: str | None) -> str:
    m = (model or "").strip()
    for suf in BODY_SUFFIXES:
        if m.lower().endswith(suf.lower()):
            m = m[: -len(suf)].strip()
    return m.replace("!", "").strip()   # "up!" -> "up"


def _year(first_reg: str | None) -> int | None:
    try:
        return int(str(first_reg)[:4])
    except (TypeError, ValueError):
        return None


class DiscoverSource(Source):
    name = "discover"
    live = True

    def __init__(self, fetch=None, makes=None, min_listings: int = 2, dry_run: bool = False):
        self._fetch = fetch
        self.makes = makes if makes is not None else SCAN_MAKES
        self.min_listings = min_listings
        self.dry_run = dry_run
        self.report: list[str] = []   # menschliche Zusammenfassung je neuem Modell

    def _get(self, url: str) -> str | None:
        if self._fetch:
            return self._fetch(url)
        import time
        import requests
        time.sleep(1.5)   # hoeflich
        r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=25)
        return r.text if r.status_code == 200 else None

    def _model_exists(self, conn, make: str, model: str) -> bool:
        # exakt ODER Praefix in beide Richtungen (deckt "ID.3" vs "ID.3 Pro" ab)
        row = conn.execute(
            "SELECT 1 FROM car_model WHERE lower(make)=lower(?) AND ("
            " lower(model)=lower(?) OR lower(model) LIKE lower(?)||'%'"
            " OR lower(?) LIKE lower(model)||'%')",
            (make, model, model, model)).fetchone()
        return row is not None

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        res = CollectResult(source=self.name)
        # (make, model) -> Liste der Angebote
        groups: dict[tuple[str, str], list[dict]] = {}
        makes_scanned = 0
        for make, slug in self.makes:
            html = self._get(f"https://www.autoscout24.de/lst/{slug}")
            if not html:
                continue
            makes_scanned += 1
            for it in parse_autoscout24(html):
                if it.get("damaged") or not it.get("price") or not it.get("model"):
                    continue
                model = _norm_model(it["model"])
                if not model or any(h in model.lower() for h in SKIP_HINTS):
                    continue
                groups.setdefault((make, model), []).append(it)

        skipped_existing = 0
        below_threshold = 0
        for (make, model), items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            if self._model_exists(conn, make, model):
                skipped_existing += 1
                continue
            if len(items) < self.min_listings:
                below_threshold += 1
                continue

            prices = [it["price"] for it in items]
            years = [y for y in (_year(it.get("first_reg")) for it in items) if y]
            fuels = [FUEL_TO_DRIVETRAIN.get(it.get("fuel")) for it in items]
            fuels = [f for f in fuels if f]
            drivetrain = statistics.mode(fuels) if fuels else None
            ranges = [it["list_range"] for it in items
                      if drivetrain == "elektro" and it.get("list_range")]
            typical = round(statistics.median(prices))
            yf = min(years) if years else None
            yt = max(years) if years else None

            self.report.append(
                f"{make} {model}: {len(items)} Angebote, ~{typical} EUR, "
                f"{drivetrain or '?'}, Bj {yf or '?'}-{yt or '?'}")
            if self.dry_run:
                res.updated += 1
                continue

            from ..db import upsert_model, upsert_spec
            mid = upsert_model(conn, make, model, "auto-entdeckt",
                               year_from=yf, year_to=yt,
                               fuel=DRIVETRAIN_TO_FUEL.get(drivetrain))
            upsert_spec(conn, mid, drivetrain=drivetrain, typical_price=typical,
                        range_km=round(statistics.median(ranges)) if ranges else None)
            for it in items:
                _record_listing(conn, model_id=mid, source="autoscout24",
                                source_ref=it["source_ref"], title=it["title"],
                                price=it["price"], mileage_km=it.get("mileage_km"),
                                first_reg=it.get("first_reg"), plz=it.get("plz"),
                                location=it.get("location"), url=it.get("url"),
                                power_kw=it.get("power_kw"), price_rating=it.get("rating"))
            res.inserted += 1

        conn.commit()
        verb = "wuerde anlegen" if self.dry_run else "neu angelegt"
        n = res.updated if self.dry_run else res.inserted
        res.notes = (f"{makes_scanned} Marken gescannt, {n} Modelle {verb} "
                     f"(uebersprungen: {skipped_existing} bekannt, "
                     f"{below_threshold} < {self.min_listings} Angebote)")
        return res
