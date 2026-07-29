"""KBA-Rueckruf-Adapter - Geruest.

Das Kraftfahrt-Bundesamt (KBA) veroeffentlicht Rueckrufe oeffentlich
(Rueckrufdatenbank). Auch die EU-Datenbank RAPEX/Safety Gate listet
Fahrzeug-Rueckrufe. Beides ist oeffentlich und damit ein "legal zuerst"-Weg.

Dieses Geruest zeigt den Ablauf: abrufen (robots-konform) -> parsen ->
je Modell in `recall` schreiben. Der eigentliche Parser haengt vom aktuellen
Seitenformat ab und ist als TODO markiert.
"""
from __future__ import annotations

import sqlite3

from .base import CollectResult, Source, polite_get

# Beispiel-Einstiegspunkte (Format aendert sich, daher hier nur als Referenz)
KBA_RUECKRUFE_URL = "https://www.kba.de/DE/Themen/Marktueberwachung/Rueckrufe/rueckrufe_node.html"


class KbaRecallSource(Source):
    name = "kba_recalls"
    live = False

    def collect(self, conn: sqlite3.Connection) -> CollectResult:
        res = CollectResult(source=self.name)
        resp = polite_get(KBA_RUECKRUFE_URL)
        if resp is None:
            res.notes = "Durch robots.txt blockiert oder requests fehlt - nichts geholt."
            return res
        if resp.status_code != 200:
            res.notes = f"HTTP {resp.status_code}"
            return res
        # TODO: HTML/JSON parsen und je (make,model,generation) in `recall` schreiben.
        # Struktur bewusst offengelassen, da das KBA-Seitenformat variiert.
        res.notes = ("Seite geladen, Parser noch nicht implementiert. "
                     "HTML-Struktur pruefen und Eintraege den Modellen zuordnen.")
        return res

    def _add_recall(self, conn, model_id, kba_code, date, description, url=None) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO recall(model_id,kba_code,date,description,url)"
            " VALUES (?,?,?,?,?)", (model_id, kba_code, date, description, url))
