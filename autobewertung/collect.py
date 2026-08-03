"""CLI-Orchestrierung: DB initialisieren, Quellen einsammeln, Ranking ausgeben.

Beispiele:
    python -m autobewertung.collect init
    python -m autobewertung.collect run                 # alle default-Quellen
    python -m autobewertung.collect run --only seed
    python -m autobewertung.collect rank --top 10
    python -m autobewertung.collect run --inserate-csv meine_merkliste.csv
    python -m autobewertung.collect discover --dry-run   # neue Modelle aus AS24
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
    from .sources import all_sources
    sources = all_sources() if args.only else default_sources()
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
    # Marktpreis-Snapshot je Modell -> Preisverlauf fuer ALLE Modelle
    from .tracking import snapshot_model_prices
    n = snapshot_model_prices(conn)
    print(f"[{'preis-snapshot':14}] {n} Modell-Preispunkte geschrieben")
    conn.close()


def cmd_assignments(args) -> None:
    """Verifikation: welchem Modell ist jedes Angebot zugeordnet?"""
    from .tracking import assignment_report
    conn = init_db(args.db)
    rows = assignment_report(conn)
    if not rows:
        print("Keine Angebote in der DB.")
        return
    print(f"{'Quelle':10} {'Preis':>9}  {'OK':2}  Modell  <-  Angebot")
    print("-" * 78)
    for r in rows:
        mark = "✓" if r["ok"] else "!!"
        price = f"{r['price']:,.0f}€".replace(",", ".") if r["price"] else "-"
        print(f"{r['source']:10} {price:>9}  {mark:2}  {r['assigned']:32}  <-  {r['title'] or ''}")
    bad = [r for r in rows if not r["ok"]]
    print(f"\n{len(rows)-len(bad)}/{len(rows)} korrekt zugeordnet"
          + (f", {len(bad)} unklar" if bad else ""))
    conn.close()


def _eur(v) -> str:
    return f"{v:,.0f}".replace(",", ".") + "€" if v is not None else "  -"


def cmd_track(args) -> None:
    """Preis-Tracking (fuer Cron): echte AS24-Preise je qualifiziertem Modell +
    verfolgte URLs + Modell-Preis-Snapshot -> Preistrend ueber die Zeit."""
    from datetime import datetime, timezone
    from pathlib import Path
    from .alerts import scan_alerts
    from .config import load_criteria
    from .scoring import score_models
    from .sources import default_sources
    from .sources.autoscout24 import AutoScout24Source
    from .sources.watchlist import WatchlistSource
    from .tracking import snapshot_model_prices

    conn = init_db(args.db)
    since_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")   # Alarm-Fenster
    # Datenbestand sicherstellen (Modelle/Specs), falls leer
    if not conn.execute("SELECT 1 FROM car_model LIMIT 1").fetchone():
        for s in default_sources():
            s.collect(conn)

    crit = load_criteria()
    ranked = score_models(conn, crit).ranked[: args.top]
    as24 = AutoScout24Source()
    got = 0
    for m in ranked:
        row = conn.execute("SELECT make, model FROM car_model WHERE id=?", (m.model_id,)).fetchone()
        try:
            n, _ = as24.fetch_model(conn, m.model_id, row["make"], row["model"])
            got += n
        except Exception:
            pass
    wres = WatchlistSource().collect(conn)
    snaps = snapshot_model_prices(conn)
    alerts = scan_alerts(conn, since_ts)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{ts}] track: {got} AS24-Angebote ({len(ranked)} Modelle), "
          f"Watchlist: {wres.notes}, {snaps} Modell-Preispunkte, {len(alerts)} Schnaeppchen-Alarm(e)")
    if alerts:
        logf = Path(args.db).resolve().parent.parent / "alerts.log"
        with open(logf, "a", encoding="utf-8") as f:
            for a in alerts:
                line = f"[{ts}] {a}"
                print("  🔔 " + a)
                f.write(line + "\n")
    conn.close()


def cmd_deals(args) -> None:
    """Aktuell unterbewertete Angebote laut Fair-Preis-Modell (Residual in EUR)."""
    from . import fairprice
    conn = init_db(args.db)
    model = fairprice.fit(conn)
    if model is None:
        print("Fair-Preis-Modell nicht verfuegbar (zu wenig Daten oder numpy fehlt).")
        return
    est = fairprice.estimate_listings(conn, model)
    band = [e for e in est.values()
            if -0.35 <= e.resid_pct <= -0.05 and e.resid_eur <= -500]
    band.sort(key=lambda e: e.resid_pct)
    print(f"Fair-Preis-Modell: {model.n} Angebote, {len(model.model_ids)} Modelle, "
          f"R2={model.r2:.2f}. Plausibel unter fairem Preis (Top {args.top}):\n")
    for e in band[: args.top]:
        r = conn.execute(
            "SELECT l.mileage_km, l.first_reg, l.url, cm.make||' '||cm.model AS model "
            "FROM listing l JOIN car_model cm ON cm.id=l.model_id WHERE l.id=?",
            (e.listing_id,)).fetchone()
        print(f"  {r['model'][:24]:24} {_eur(e.price):>9}  fair {_eur(e.fair_price):>9}  "
              f"{e.resid_eur:>+7.0f}€ ({e.resid_pct*100:>+3.0f}%)  "
              f"{r['mileage_km'] or '?'}km EZ{r['first_reg'] or '?'}")
        if e.resid_pct <= -0.15 and r["url"]:
            print(f"      {r['url']}")
    if not band:
        print("  (keine Angebote im plausiblen Deal-Band)")
    conn.close()


def cmd_discover(args) -> None:
    """Neue Modelle aus den AS24-Live-Angeboten erkennen und anlegen.

    Scannt die kanonischen Marken-Seiten, gruppiert nach Modell und legt noch
    nicht vorhandene Modelle mit abgeleiteter Minimal-Spec an. `--dry-run` zeigt
    nur, was angelegt wuerde.
    """
    from .sources.discover import DiscoverSource
    conn = init_db(args.db)
    src = DiscoverSource(min_listings=args.min, dry_run=args.dry_run)
    res = src.collect(conn)
    for line in src.report:
        print(("  ~ " if args.dry_run else "  + ") + line)
    print(f"[{res.source}] {res.notes}")
    if not args.dry_run and res.inserted:
        print("Tipp: `rank` neu ausfuehren; neue Modelle sind als "
              "generation='auto-entdeckt' markiert (Spec noch teils 🟡).")
    conn.close()


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
    def _tkm(km):
        return f"{round(km / 1000)}tkm" if km else "?"

    header = (f"{'#':>2}  {'Modell':24} {'Antr.':7} {'Score':>5} "
              f"{'guenstigst @ km':>17} {'typisch':>8} {'fair':>5} {'Ang':>3} {'TCO/J':>7}")
    print(header)
    print("-" * len(header))
    for i, m in enumerate(ranked, 1):
        cheap = f"{_eur(m.purchase_price)} @ {_tkm(m.purchase_km)}"
        fair = f"{m.fair_gap_pct:+.0f}%" if m.fair_gap_pct is not None else "-"
        print(f"{i:>2}  {m.label[:24]:24} {(m.drivetrain or '-'):7} {m.total:>5.1f} "
              f"{cheap:>17} {_eur(m.median_price):>8} {fair:>5} {m.n_listings:>3} "
              f"{_eur(m.annual_tco):>7}")

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

    tr = sub.add_parser("track", help="Preis-Tracking fuer Cron (AS24 + Watchlist + Snapshot)")
    tr.add_argument("--top", type=int, default=20, help="Anzahl Top-Modelle, die getrackt werden")
    tr.set_defaults(func=cmd_track)

    dl = sub.add_parser("deals", help="unterbewertete Angebote laut Fair-Preis-Modell")
    dl.add_argument("--top", type=int, default=15)
    dl.set_defaults(func=cmd_deals)

    dc = sub.add_parser("discover", help="neue Modelle aus AS24-Angeboten erkennen + anlegen")
    dc.add_argument("--min", type=int, default=2, help="Mindestzahl Angebote je neuem Modell")
    dc.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts anlegen")
    dc.set_defaults(func=cmd_discover)

    w = sub.add_parser("watch", help="Inserats-URL verfolgen (Preisverlauf)")
    w.add_argument("url")
    w.add_argument("--note", default=None)
    w.set_defaults(func=cmd_watch)

    sub.add_parser("assignments", help="Angebots-Modell-Zuordnung verifizieren"
                   ).set_defaults(func=cmd_assignments)

    rk = sub.add_parser("rank", help="Ranking ausgeben")
    rk.add_argument("--top", type=int, default=10)
    rk.set_defaults(func=cmd_rank)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
