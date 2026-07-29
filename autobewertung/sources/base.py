"""Gemeinsames Interface fuer alle Datenquellen-Adapter.

Jede Quelle (Pannenstatistik, Inserate, Rueckrufe, ...) implementiert `Source`
und schreibt normalisiert in die DB. So laesst sich jede Quelle einzeln
scharfschalten, testen und (per collect.py) orchestrieren.

Grundsatz "legal/pragmatisch zuerst":
- robots.txt respektieren (siehe `allowed_by_robots`)
- htoefliche Rate-Limits (siehe `polite_get`)
- klarer User-Agent, keine Anti-Bot-Umgehung
"""
from __future__ import annotations

import sqlite3
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

USER_AGENT = "auto-bewertung/0.1 (persoenliches Recherchetool)"
_last_request: dict[str, float] = {}
_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def allowed_by_robots(url: str) -> bool:
    """Prueft robots.txt der Ziel-Domain fuer unseren User-Agent."""
    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    rp = _robots_cache.get(root)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{root}/robots.txt")
        try:
            rp.read()
        except Exception:
            # kein robots erreichbar -> konservativ erlauben, aber langsam
            rp = None
        _robots_cache[root] = rp
    if rp is None:
        return True
    return rp.can_fetch(USER_AGENT, url)


def polite_get(url: str, min_interval_s: float = 3.0, timeout: float = 20.0):
    """HTTP GET mit robots-Check und Domain-Rate-Limit. Gibt `requests.Response` oder None."""
    try:
        import requests
    except ModuleNotFoundError as e:
        raise RuntimeError("`requests` nicht installiert (pip install requests)") from e

    if not allowed_by_robots(url):
        return None
    domain = urlparse(url).netloc
    wait = min_interval_s - (time.monotonic() - _last_request.get(domain, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_request[domain] = time.monotonic()
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)


@dataclass
class CollectResult:
    source: str
    inserted: int = 0
    updated: int = 0
    notes: str = ""


class Source:
    """Basisklasse. Unterklassen implementieren `collect`."""

    name: str = "base"
    #: True, sobald der Adapter echt Daten holt (nicht nur Stub)
    live: bool = False

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        raise NotImplementedError

    # Hilfen fuer Unterklassen -------------------------------------------------
    def _find_model(self, conn, make: str, model: str, generation: str | None = None) -> int | None:
        row = conn.execute(
            "SELECT id FROM car_model WHERE make=? AND model=? "
            "AND IFNULL(generation,'')=IFNULL(?, '')",
            (make, model, generation),
        ).fetchone()
        return row["id"] if row else None
