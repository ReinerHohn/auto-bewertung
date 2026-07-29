"""CLI-Orchestrierung: DB initialisieren, Quellen einsammeln, Ranking ausgeben.

Beispiele:
    python -m autobewertung.collect init
    python -m autobewertung.collect run                 # alle default-Quellen
    python -m autobewertung.collect run --only seed
    python -m autobewertung.collect rank --top 10
    python -m autobewertung.collect run --inserate-csv meine_merkliste.csv
"""
from __future__ import annotations

import argparse

from .config import DIMENSIONS, load_criteria
from .db import DEFAULT_DB, init_db
from .scoring import score_models
from .sources import default_sources
from .sources.inserate import InserateSource


def cmd_init(args) -> None:
    init_db(args.db)
    print(f"DB initialisiert: {args.db}")


def cmd_run(args) -> None:
    conn = init_db(args.db)
    sources = default_sources()
    if args.inserate_csv:
        # Inserate-Quelle mit CSV-Pfad ersetzen
        sources = [s for s in sources if not isinstance(s, InserateSource)]
        sources.append(InserateSource(csv_path=args.inserate_csv))
    if args.only:
        sources = [s for s in sources if s.name in args.only]
    for s in sources:
        res = s.collect(conn)
        flag = "" if s.live or res.inserted or res.updated else "  [Geruest]"
        print(f"[{res.source:14}] +{res.inserted} ~{res.updated}{flag}  {res.notes}")
    conn.close()


def _eur(v) -> str:
    return f"{v:,.0f}".replace(",", ".") + "€" if v is not None else "  -"


def cmd_watch(args) -> None:
    from .db import add_watch
    conn = init_db(args.db)
    add_watch(conn, args.url, args.note)
    n = conn.execute("SELECT COUNT(*) c FROM watch").fetchone()["c"]
    print(f"Verfolgt: {args.url}\n{n} URL(s) in der Watchlist. Preis wird bei `run` erfasst.")
    conn.close()


def cmd_rank(args) -> None:
    conn = init_db(args.db)
    crit = load_criteria()
    result = score_models(conn, crit)
    ranked = result.ranked[: args.top]
    if not ranked and not result.excluded:
        print("Keine Modelle in der DB. Erst `run` ausfuehren.")
        return
    w = crit.normalized_weights()
    print("Gewichte:", ", ".join(f"{d}={w[d]:.0%}" for d in DIMENSIONS))
    print(f"TCO-Annahmen: {crit.tco.annual_km} km/Jahr, {crit.tco.holding_years} Jahre Haltedauer")
    print()
    header = (f"{'#':>2}  {'Modell':26} {'Antrieb':8} {'Score':>6} "
              f"{'Kaufpreis':>10} {'TCO/Jahr':>9}  Dimensionen")
    print(header)
    print("-" * len(header))
    for i, m in enumerate(ranked, 1):
        dims = " ".join(f"{d[:4]}:{m.dims[d]:.0f}" for d in DIMENSIONS)
        print(f"{i:>2}  {m.label[:26]:26} {(m.drivetrain or '-'):8} {m.total:>6.1f} "
              f"{_eur(m.purchase_price):>10} {_eur(m.annual_tco):>9}  {dims}")

    if result.excluded:
        print("\nAusgeschlossen (harte Kriterien):")
        for e in result.excluded:
            print(f"  - {e.label:26} {e.reason}")
    conn.close()


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="autobewertung", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DEFAULT_DB), help="Pfad zur SQLite-DB")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="DB-Schema anlegen").set_defaults(func=cmd_init)

    r = sub.add_parser("run", help="Datenquellen einsammeln")
    r.add_argument("--only", nargs="*", help="nur diese Quellen (Name)")
    r.add_argument("--inserate-csv", help="CSV mit Angeboten importieren")
    r.set_defaults(func=cmd_run)

    w = sub.add_parser("watch", help="Inserats-URL verfolgen (Preisverlauf)")
    w.add_argument("url")
    w.add_argument("--note", default=None)
    w.set_defaults(func=cmd_watch)

    rk = sub.add_parser("rank", help="Ranking ausgeben")
    rk.add_argument("--top", type=int, default=10)
    rk.set_defaults(func=cmd_rank)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
