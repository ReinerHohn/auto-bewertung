"""Registry aller Datenquellen-Adapter.

`ALL_SOURCES` bestimmt, was `collect.py` standardmaessig ausfuehrt. Neue Quellen
hier eintragen. `live=False`-Quellen sind Gerueste und tun (ausser ggf. einem
Netz-Check) nichts, bis sie konfiguriert werden.
"""
from .base import CollectResult, Source
from .inserate import InserateSource
from .kba_recalls import KbaRecallSource
from .seed import SeedSource


def default_sources() -> list[Source]:
    return [SeedSource(), InserateSource(), KbaRecallSource()]


__all__ = [
    "Source", "CollectResult", "SeedSource", "InserateSource",
    "KbaRecallSource", "default_sources",
]
