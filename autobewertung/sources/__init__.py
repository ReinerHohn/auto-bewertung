"""Registry aller Datenquellen-Adapter.

`default_sources()` bestimmt, was `collect.py` standardmaessig ausfuehrt. Neue
Quellen hier eintragen. `live=False`-Quellen sind Gerueste und tun (ausser ggf.
einem Netz-Check) nichts, bis sie konfiguriert werden.
"""
from .base import CollectResult, Source
from .inserate import InserateSource
from .kba_recalls import KbaRecallSource
from .seed import SeedSource
from .watchlist import WatchlistSource


def default_sources() -> list[Source]:
    return [SeedSource(), WatchlistSource(), InserateSource(), KbaRecallSource()]


__all__ = [
    "Source", "CollectResult", "SeedSource", "WatchlistSource",
    "InserateSource", "KbaRecallSource", "default_sources",
]
