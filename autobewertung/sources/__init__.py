"""Registry aller Datenquellen-Adapter.

`default_sources()` bestimmt, was `collect.py` standardmaessig ausfuehrt. Neue
Quellen hier eintragen. `live=False`-Quellen sind Gerueste und tun (ausser ggf.
einem Netz-Check) nichts, bis sie konfiguriert werden.
"""
from .base import CollectResult, Source
from .inserate import InserateSource
from .recalls import NhtsaRecallSource, RecallImportSource
from .reliability_import import ReliabilityImportSource
from .seed import SeedSource
from .watchlist import WatchlistSource
from .wear_import import WearImportSource


def default_sources() -> list[Source]:
    # Import-Quellen NACH Seed: echte TUEV/ADAC-, Verschleiss- + Rueckruf-Daten.
    # NhtsaRecallSource (Netz, US-Markt) nicht im Default -> via --only nhtsa_recalls.
    return [SeedSource(), ReliabilityImportSource(), WearImportSource(),
            RecallImportSource(), WatchlistSource(), InserateSource()]


def all_sources() -> list[Source]:
    return default_sources() + [NhtsaRecallSource()]


__all__ = [
    "Source", "CollectResult", "SeedSource", "ReliabilityImportSource",
    "WearImportSource", "RecallImportSource", "NhtsaRecallSource",
    "WatchlistSource", "InserateSource", "default_sources", "all_sources",
]
