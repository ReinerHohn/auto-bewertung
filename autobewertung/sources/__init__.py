"""Registry aller Datenquellen-Adapter.

`default_sources()` bestimmt, was `collect.py` standardmaessig ausfuehrt. Neue
Quellen hier eintragen. `live=False`-Quellen sind Gerueste und tun (ausser ggf.
einem Netz-Check) nichts, bis sie konfiguriert werden.
"""
from .base import CollectResult, Source
from .inserate import InserateSource
from .kba_recalls import KbaRecallSource
from .reliability_import import ReliabilityImportSource
from .seed import SeedSource
from .watchlist import WatchlistSource
from .wear_import import WearImportSource


def default_sources() -> list[Source]:
    # Import-Quellen NACH Seed: echte TUEV/ADAC- + Verschleiss-Daten ergaenzen Seeds.
    return [SeedSource(), ReliabilityImportSource(), WearImportSource(),
            WatchlistSource(), InserateSource(), KbaRecallSource()]


__all__ = [
    "Source", "CollectResult", "SeedSource", "ReliabilityImportSource",
    "WearImportSource", "WatchlistSource", "InserateSource", "KbaRecallSource",
    "default_sources",
]
