"""Streamlit-Dashboard mit Drill-down-Tabelle.

Starten:
    streamlit run autobewertung/dashboard.py

Bedienung:
  - Zeile anklicken  -> Auto waehlen
  - Spalte anklicken -> Kategorie waehlen (Schwachstellen, TCO, Preis, ...)
  -> darunter erscheint die vollstaendige Liste; jeder Eintrag ist fuer
     Detail-Infos aufklappbar. Ohne Spaltenauswahl werden alle Kategorien
     als Reiter gezeigt.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from urllib.parse import quote_plus

# Streamlit legt beim Start nur das Skriptverzeichnis auf sys.path, nicht das
# Projekt-Root -> Paket-Import sicherstellen, bevor autobewertung importiert wird.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

from autobewertung import geo
from autobewertung.config import DEFAULT_WEIGHTS, DIMENSIONS, Criteria
from autobewertung.db import DEFAULT_DB, init_db
from autobewertung.scoring import score_models
from autobewertung.tracking import price_floor
from autobewertung.tco import CLASS_RANK, TcoAssumptions

# Spaltenlabels der sechs Score-Dimensionen (0..100)
DIM_LABELS = {
    "tco": "Kosten-Score",
    "value_stability": "Wertstabilität",
    "equipment": "Ausstattung",
    "price_value": "Preis/Deal",
    "reliability": "Zuverlaessigkeit",
    "weak_points": "Schwachstellen",
    "parts_availability": "Ersatzteile",
    "workshop_access": "Werkstaetten",
}
FEATURE_LABELS = {
    "einparkhilfe": "Einparkhilfe (PDC)", "rueckfahrkamera": "Rückfahrkamera",
    "notbremsassistent": "Notbremsassistent (AEB)", "spurhalteassistent": "Spurhalteassistent",
}
# Klick auf DIESE Spalte -> DIESE Detail-Kategorie
COLUMN_TO_CATEGORY = {
    "TCO/Jahr": "tco", "Kosten-Score": "tco",
    "Kaufpreis": "price", "Preis/Deal": "price", "Rabatt %": "price", "Angebote": "price",
    "Zuverlaessigkeit": "reliability",
    "Schwachstellen": "weak_points",
    "Ersatzteile": "parts",
    "Werkstaetten": "workshop",
}
SEV = {1: "gering", 2: "mittel", 3: "schwer"}
# AutoScout24-Preisbewertung
PRICE_RATING = {1: "🟢 Sehr guter Preis", 2: "🟢 Guter Preis", 3: "🟡 Fairer Preis",
                4: "🟠 Erhöhter Preis", 5: "🔴 Hoher Preis"}
TCO_LABELS = {
    "wertverlust": "Wertverlust", "energie": "Energie", "versicherung": "Versicherung",
    "steuer": "Kfz-Steuer", "wartung": "Wartung (Service)",
    "verschleiss_reparatur": "Verschleiß/Reparaturen", "sonstiges": "Sonstiges",
}
TCO_EXPLAIN = {
    "wertverlust": "Kaufpreis minus geschaetzter Restwert nach der Haltedauer, auf ein Jahr umgelegt.",
    "energie": "Verbrauch x Jahreskilometer x Energiepreis (Sprit bzw. Strom-Mischpreis).",
    "versicherung": "Angesetzte Versicherungspraemie pro Jahr (Teilkasko-Groessenordnung).",
    "steuer": "Kfz-Steuer pro Jahr (E-Autos meist befreit).",
    "wartung": "Regelmaessige Inspektion/Service pro Jahr.",
    "verschleiss_reparatur": "Erwartete Teile-/Reparaturkosten im eigenen km-Fenster "
                             "(Bremsen, Reifen, Zahnriemen, modellspezifische Defekte – siehe Tab Verschleiß).",
    "sonstiges": "Pauschale fuer HU/AU und Kleinkram.",
}

# AutoScout24 nutzt Marken-Slugs (z.B. VW -> volkswagen)
AS24_MAKE_SLUG = {"VW": "volkswagen", "Mercedes": "mercedes-benz"}


def pkw_trend_url(make: str, model: str) -> str:
    """pkw.de-Preistrend-Seite fuer dieses Modell, z.B. .../tesla/model-3."""
    mk = make.lower().replace(" ", "-")
    md = model.lower().replace(" ", "-").replace(".", "-")
    return f"https://www.pkw.de/preistrends/{mk}/{md}"


def portal_links(make: str, model: str) -> list[tuple[str, str]]:
    """Direkt-Links zu den Gebrauchtwagen-Portalen fuer genau dieses Modell."""
    as24_make = AS24_MAKE_SLUG.get(make, make.lower().replace(" ", "-"))
    as24_model = model.lower().replace(" ", "-").replace(".", "")
    q = quote_plus(f"{make} {model}")
    return [
        ("🔗 AutoScout24 – Angebote für dieses Modell",
         f"https://www.autoscout24.de/lst/{as24_make}/{as24_model}?sort=price&desc=0&ustate=N%2CU"),
        ("🔗 mobile.de – Angebote suchen",
         f"https://suchen.mobile.de/fahrzeuge/search.html?dam=false&isSearchRequest=true&s=Car&vc=Car&q={q}"),
        ("🔗 kleinanzeigen.de – Angebote suchen",
         f"https://www.kleinanzeigen.de/s-autos/{q}/k0c216"),
    ]


st.set_page_config(page_title="Auto-Bewertung", layout="wide", initial_sidebar_state="collapsed")
st.title("🚗 Auto-Bewertung – Gebrauchtwagen mit Total Cost of Ownership")


@st.cache_resource
def get_conn():
    return init_db(DEFAULT_DB)


conn = get_conn()


# ---------------------------------------------------------------------------
# Detail-Rendering je Kategorie (Level 2 = Liste, Level 3 = aufklappbare Details)
# ---------------------------------------------------------------------------

def _make_of(model_id):
    r = conn.execute("SELECT make FROM car_model WHERE id=?", (model_id,)).fetchone()
    return r["make"] if r else None


def real_metrics(model_id):
    """Echte, interpretierbare Kennzahlen je Modell (nicht die 0..100-Scores)."""
    def one(sql, params):
        r = conn.execute(sql, params).fetchone()
        return r[0] if r and r[0] is not None else None
    make = _make_of(model_id)
    feats = one("SELECT features FROM vehicle_spec WHERE model_id=?", (model_id,)) or ""
    avail = set(f for f in feats.split(",") if f)
    return {
        "maengel_pct": one("SELECT value FROM reliability_stat WHERE model_id=? AND metric='maengelquote_pct'", (model_id,)),
        "pannen": one("SELECT value FROM reliability_stat WHERE model_id=? AND metric='pannen_pro_1000'", (model_id,)),
        "parts": one("SELECT score FROM parts_availability WHERE model_id=?", (model_id,)),
        "workshops": one("SELECT COUNT(*) FROM workshop WHERE make=? OR make IS NULL", (make,)) or 0,
        "n_weak": one("SELECT COUNT(*) FROM weak_point WHERE model_id=?", (model_id,)) or 0,
        "depr": one("SELECT depr_pct_year FROM vehicle_spec WHERE model_id=?", (model_id,)),
        "features": avail,
        "has_matrix": one("SELECT has_matrix FROM vehicle_spec WHERE model_id=?", (model_id,)) or 0,
        "insurance": one("SELECT insurance_eur FROM vehicle_spec WHERE model_id=?", (model_id,)),
        "recalls": one("SELECT COUNT(*) FROM recall WHERE model_id=?", (model_id,)) or 0,
        "length_mm": one("SELECT length_mm FROM vehicle_spec WHERE model_id=?", (model_id,)),
        "width_mm": one("SELECT width_mm FROM vehicle_spec WHERE model_id=?", (model_id,)),
        "alu_body": one("SELECT alu_body FROM vehicle_spec WHERE model_id=?", (model_id,)) or 0,
        "turning_m": one("SELECT turning_m FROM vehicle_spec WHERE model_id=?", (model_id,)),
    }


def render_category(model, cat: str) -> None:
    mid = model.model_id

    if cat == "weak_points":
        st.markdown("#### 🔧 Schwachstellen & Rückrufe")
        mk = _make_of(mid)
        mdl = conn.execute("SELECT model FROM car_model WHERE id=?", (mid,)).fetchone()["model"]
        wp = conn.execute(
            "SELECT component,description,severity,cost_eur,source,url FROM weak_point "
            "WHERE model_id=? ORDER BY severity DESC", (mid,)).fetchall()
        rc = conn.execute(
            "SELECT kba_code,date,description,url FROM recall WHERE model_id=?", (mid,)).fetchall()
        if not wp and not rc:
            st.info("Keine erfasst."); return
        for w in wp:
            sev = SEV.get(w["severity"], "?")
            bar = {1: "🟡", 2: "🟠", 3: "🔴"}.get(w["severity"], "⚪")
            cost = f" · ~{w['cost_eur']:,.0f} €".replace(",", ".") if w["cost_eur"] else ""
            with st.expander(f"{bar} {w['component']} · Schwere: {sev}{cost}"):
                st.markdown(f"**{w['component']}** — {w['description']}")
                if w["cost_eur"]:
                    st.markdown(f"**Typische Reparaturkosten: ~{w['cost_eur']:,.0f} €**".replace(",", "."))
                st.caption(f"Schweregrad {w['severity']}/3 ({sev}) · Quelle: {w['source'] or '-'}")
                # Recherche-Links speziell zu diesem Defekt
                q = quote_plus(f"{mk} {mdl} {w['component']} Problem")
                links = [
                    ("🔎 Motor-Talk (Forum durchsuchen)",
                     f"https://www.google.com/search?q=site:motor-talk.de+{q}"),
                    ("🔎 Google-Suche zum Defekt",
                     f"https://www.google.com/search?q={q}"),
                    ("🔎 YouTube (Reparatur/Diagnose)",
                     f"https://www.youtube.com/results?search_query={q}"),
                ]
                if w["url"]:
                    links.insert(0, ("📄 Hinterlegte Quelle", w["url"]))
                for label, url in links:
                    st.markdown(f"- [{label}]({url})")
        for r in rc:
            with st.expander(f"📢 Rückruf {r['kba_code'] or ''} · {r['date'] or ''}"):
                st.write(r["description"])
                if r["url"]:
                    st.markdown(f"[KBA-Eintrag]({r['url']})")

    elif cat == "reliability":
        st.markdown("#### 📊 Zuverlässigkeit (Pannen & Mängel)")
        rows = conn.execute(
            "SELECT source,metric,value,vehicle_age,year,is_estimate,source_url,note "
            "FROM reliability_stat WHERE model_id=? ORDER BY source", (mid,)).fetchall()
        if not rows:
            st.info("Keine erfasst."); return
        for r in rows:
            unit = "Pannen/1000 Fzg (ADAC)" if r["metric"].startswith("pannen") else "% Mängel HU (TÜV)"
            badge = "🟢 echte Quelle" if not r["is_estimate"] else "🟡 Schätzung"
            with st.expander(f"{badge} · {r['source']}: {r['value']:.1f}  ({unit})"):
                st.write(f"**Wert:** {r['value']:.1f} — {unit}")
                age = f"{r['vehicle_age']}–{r['vehicle_age']+1} J" if r["vehicle_age"] else "n/a"
                st.caption(f"Altersklasse: {age} · Berichtsjahr: {r['year'] or 'n/a'}"
                           + (f" · {r['note']}" if r["note"] else ""))
                if r["source_url"]:
                    st.markdown(f"[📄 Quelle]({r['source_url']})")
                elif r["is_estimate"]:
                    st.caption("⚠️ Platzhalter – keine öffentliche Zahl gefunden (z. B. ADAC druckt "
                               "für diese Klasse keine absoluten Werte, oder Modell zu jung).")

    elif cat == "parts":
        st.markdown("#### 🧩 Ersatzteil-Verfügbarkeit")
        rows = conn.execute(
            "SELECT score,avg_price_idx,notes,source FROM parts_availability WHERE model_id=?",
            (mid,)).fetchall()
        if not rows:
            st.info("Keine erfasst."); return
        for r in rows:
            with st.expander(f"Verfügbarkeit {r['score']:.0f}/100 · Preisindex {r['avg_price_idx']:.0f}"):
                st.write(f"**Verfügbarkeits-Score:** {r['score']:.0f}/100 (100 = beste Verfügbarkeit)")
                st.write(f"**Preisindex:** {r['avg_price_idx']:.0f} (100 = Marktdurchschnitt, <100 günstiger)")
                if r["notes"]:
                    st.caption(r["notes"])
                st.caption(f"Quelle: {r['source'] or '-'}")

    elif cat == "workshop":
        make = _make_of(mid)
        st.markdown(f"#### 🛠️ Werkstätten für {make}")
        rows = conn.execute(
            "SELECT name,plz,location,specialized,url FROM workshop "
            "WHERE make=? OR make IS NULL ORDER BY (make IS NULL), specialized DESC", (make,)).fetchall()
        if not rows:
            st.info("Keine erfasst."); return
        for r in rows:
            kind = "Marken-Spezialist" if r["specialized"] else "frei/allgemein"
            with st.expander(f"{'⭐ ' if r['specialized'] else ''}{r['name']} · {r['location'] or ''}"):
                st.write(f"**Typ:** {kind}")
                st.write(f"**Ort:** {r['location'] or '-'} (PLZ {r['plz'] or '-'})")
                if r["url"]:
                    st.markdown(f"[Website]({r['url']})")

    elif cat == "tco":
        st.markdown("#### 💶 Total Cost of Ownership – Aufschlüsselung")
        if not model.tco_breakdown:
            st.info("Keine TCO-Daten."); return
        st.caption(f"Kaufpreis {model.purchase_price:,.0f} € · Restwert nach Haltedauer "
                   f"{model.resale_value:,.0f} € · Summe {model.annual_tco:,.0f} €/Jahr".replace(",", "."))
        for k, v in sorted(model.tco_breakdown.items(), key=lambda x: -x[1]):
            with st.expander(f"{TCO_LABELS.get(k, k)}: {v:,.0f} €/Jahr".replace(",", ".")):
                st.write(TCO_EXPLAIN.get(k, ""))
                st.metric(f"{TCO_LABELS.get(k, k)} pro Jahr", f"{v:,.0f} €".replace(",", "."))
                if k == "versicherung":
                    tk = conn.execute("SELECT tk_kh, tk_vk, tk_tk FROM vehicle_spec WHERE model_id=?",
                                      (mid,)).fetchone()
                    if tk and tk["tk_kh"]:
                        st.markdown(
                            f"**Typklassen (GDV):** Haftpflicht **{tk['tk_kh']}** · Vollkasko "
                            f"**{tk['tk_vk']}** · Teilkasko **{tk['tk_tk']}** — niedriger = günstiger. "
                            "Die tatsächliche Prämie hängt zusätzlich von SF-Klasse, PLZ, Fahrer & Tarif "
                            "ab; die angesetzte Zahl ist nur eine Größenordnung.")
                    else:
                        st.caption("Keine Typklasse hinterlegt – Prämie ist eine grobe Schätzung.")
                    st.markdown("🔗 [Typklasse deiner exakten Motorvariante prüfen (typklasse.de)]"
                                "(https://www.typklasse.de/)")

    elif cat == "price":
        st.markdown("#### 💰 Angebote in Portalen")
        make = _make_of(mid)
        model_name = conn.execute("SELECT model FROM car_model WHERE id=?", (mid,)).fetchone()["model"]

        # Echte AS24-Angebote automatisch laden, wenn fuer dieses Modell noch keine da
        # sind (einmal pro Modell je Session; spaeter Refresh per Button unten).
        _tried = st.session_state.setdefault("_as24_tried", set())
        _has = conn.execute("SELECT 1 FROM listing WHERE model_id=? AND source='autoscout24' "
                            "AND active=1 LIMIT 1", (mid,)).fetchone()
        if not _has and mid not in _tried:
            _tried.add(mid)
            from autobewertung.sources.autoscout24 import AutoScout24Source
            with st.spinner(f"Lade echte Angebote für {make} {model_name} von AutoScout24 …"):
                try:
                    AutoScout24Source().fetch_model(conn, mid, make, model_name)
                except Exception:
                    pass

        # manueller Refresh
        if st.button("🔄 Angebote neu laden (AutoScout24)", key=f"as24_{mid}", width="stretch"):
            from autobewertung.sources.autoscout24 import AutoScout24Source
            with st.spinner("Lade echte Angebote von AutoScout24 …"):
                try:
                    n, msg = AutoScout24Source().fetch_model(conn, mid, make, model_name)
                    (st.success if n else st.warning)(msg)
                except Exception as e:
                    st.error(f"Fehler: {e}")
            st.rerun()

        st.markdown("**Direkt zu den Portalen (Suche nach diesem Modell):**")
        for label, url in portal_links(make, model_name):
            st.markdown(f"- [{label}]({url})")

        # pkw.de-Preistrend direkt eingebettet
        pkw = pkw_trend_url(make, model_name)
        st.markdown(f"**📈 Preistrend & Baujahre (pkw.de)** – [Seite öffnen ↗]({pkw})")
        try:
            if hasattr(st, "iframe"):
                st.iframe(pkw, height=600, scrolling=True)
            else:
                import streamlit.components.v1 as components
                components.iframe(pkw, height=600, scrolling=True)
        except Exception:
            st.caption("Einbettung blockiert – Link oben nutzen.")

        st.divider()
        st.markdown("**➕ Konkretes Angebot verfolgen** (URL einfügen – Preis wird ab jetzt mitgeschrieben)")
        with st.form(key=f"watch_{mid}", clear_on_submit=True):
            watch_url = st.text_input("Inserats-URL (mobile.de / AutoScout24 / kleinanzeigen …)")
            submitted = st.form_submit_button("Verfolgen & Preis holen")
        if submitted and watch_url.strip():
            from autobewertung.db import add_watch
            from autobewertung.sources.watchlist import WatchlistSource
            add_watch(conn, watch_url.strip(), model_id=mid)   # fest an DIESES Modell binden
            with st.spinner("Angebot wird abgerufen …"):
                res = WatchlistSource().collect(conn)
            st.success(f"Aufgenommen & {model_name} zugeordnet. {res.notes}")
            st.rerun()

        # Marktpreis-Verlauf des Modells (aus den Snapshots je Lauf)
        snaps = pd.read_sql_query(
            "SELECT ts AS Zeit, median_price AS Median, min_price AS Minimum "
            "FROM model_price_snapshot WHERE model_id=? ORDER BY ts", conn, params=(mid,))
        st.divider()
        st.markdown("**📉 Markt-Preisverlauf (Modell)**")
        if len(snaps) >= 2:
            st.line_chart(snaps.set_index("Zeit"))
        else:
            st.caption("Entsteht ab dem 2. Datenlauf – je öfter `run` läuft (mit neuen "
                       "Angeboten), desto aussagekräftiger.")

        st.divider()
        rows = conn.execute(
            "SELECT id,title,price,mileage_km,first_reg,location,plz,url,source,price_rating,"
            "power_kw,first_seen FROM listing WHERE model_id=? AND active=1 ORDER BY price", (mid,)).fetchall()
        if not rows:
            st.info("Keine echten Angebote gefunden (evtl. Variante/Baujahr aktuell nicht inseriert). "
                    "Button „Neu laden“ versuchen oder Portal-Link oben nutzen.")
            if model.purchase_price:
                st.metric("Geschätzter Marktpreis", f"{model.purchase_price:,.0f} €".replace(",", "."))
            return

        # Preis-Boden: aktueller Marktpreis vs. 90-Tage-Tief (aus dem Snapshot-Verlauf)
        _pf = price_floor(conn, mid)
        if _pf and _pf["n"] >= 3:
            tief = ("🔻 **auf dem 90-Tage-Tief!**" if _pf["pct_above_low"] < 2
                    else f"**{_pf['pct_above_low']:.0f} %** über dem 90-Tage-Tief "
                         f"({_pf['low']:,.0f} €)".replace(",", "."))
            st.caption(f"📉 Marktpreis (Median) aktuell {_pf['current']:,.0f} € · {tief}".replace(",", "."))

        # Klickbare Angebotsliste: jeder Button oeffnet DAS Inserat auf AutoScout24
        n_as24 = sum(1 for r in rows if r["source"] == "autoscout24")
        st.markdown(f"**🚗 {len(rows)} Angebote** ({n_as24} live von AutoScout24) – "
                    "**Angebot anklicken = direkt zum Inserat ↗**")
        for r in rows[:20]:
            rating = PRICE_RATING.get(r["price_rating"], "")
            label = (f"{rating + '  ' if rating else ''}{r['price']:,.0f} €".replace(",", ".")
                     + f"  ·  {r['mileage_km'] or '?'} km  ·  EZ {r['first_reg'] or '?'}"
                     + (f"  ·  {r['power_kw']} kW" if r["power_kw"] else "")
                     + (f"  ·  {r['location']}" if r["location"] else ""))
            if r["url"]:
                st.link_button(label + "   ↗", r["url"], width="stretch")
            else:
                st.button(label + "  (kein Link)", key=f"nolink_{r['id']}", width="stretch", disabled=True)

        st.divider()
        st.markdown("**Details & Kauf-Check je Angebot:**")
        from autobewertung import fairprice
        from autobewertung.checks import listing_age_days, negotiation_hint, scam_flags
        fair_by_lid = fairprice.estimate_listings(conn)
        for r in rows:
            rating = PRICE_RATING.get(r["price_rating"], "")
            title = (f"{r['price']:,.0f} € · {r['mileage_km'] or '?'} km · "
                     f"EZ {r['first_reg'] or '?'}").replace(",", ".")
            kw = f" · {r['power_kw']} kW" if r["power_kw"] else ""
            with st.expander(f"{rating + ' · ' if rating else ''}{title} · {r['location'] or ''}"):
                if r["source"] == "autoscout24" and r["title"]:
                    st.write(f"**Version:** {r['title']}{kw}")
                st.write(f"**Preis:** {r['price']:,.0f} €".replace(",", ".")
                         + (f" — {rating}" if rating else ""))
                fe = fair_by_lid.get(r["id"])
                if fe:
                    gap = fe.resid_pct * 100
                    tag = "🟢 unter fair" if gap < -3 else ("🔴 über fair" if gap > 3 else "⚪ ~fair")
                    st.write(f"**Fair-Preis (Modell):** ~{fe.fair_price:,.0f} € → dieses Angebot "
                             f"**{gap:+.0f} %** ({tag})".replace(",", "."))
                    for fl in scam_flags(r["price"], fe.fair_price, r["mileage_km"], r["first_reg"]):
                        {"danger": st.error, "warn": st.warning, "info": st.info}[fl["level"]]("🚨 " + fl["text"])
                _days = listing_age_days(r["first_seen"])
                if _days is not None:
                    st.write(f"**Online seit:** {_days} Tagen"
                             + (" 🕰️ Ladenhüter → Verhandlungshebel" if _days >= 45 else ""))
                _neg = negotiation_hint(r["price"], fe.fair_price if fe else None, _days)
                if _neg and _neg["room_eur"] >= 200:
                    st.write(f"**💬 Verhandeln:** Zielpreis ~{_neg['target']:,.0f} € "
                             f"(Spielraum ~{_neg['room_eur']:,.0f} €)".replace(",", "."))
                    if _neg["args"]:
                        st.caption("Argumente: " + " · ".join(_neg["args"]))
                st.write(f"**Laufleistung:** {r['mileage_km'] or '?'} km")
                st.write(f"**Erstzulassung:** {r['first_reg'] or '?'}")
                st.write(f"**Ort:** {r['location'] or '-'} (PLZ {r['plz'] or '-'})"
                         + (f" · {r['power_kw']} kW" if r["power_kw"] else ""))
                st.caption(f"Quelle: {r['source']}")
                if r["url"]:
                    st.markdown(f"[Zum Inserat]({r['url']})")
                # Konkretes Auto in den Kauf-Check schicken (km/EZ vorbelegt + Warnungen)
                if st.button("🔍 Dieses konkrete Auto prüfen", key=f"chkbtn_{r['id']}", width="stretch"):
                    st.session_state._sel_listing_km = r["mileage_km"]
                    st.session_state._sel_listing_reg = r["first_reg"]
                    st.session_state._sel_listing_price = r["price"]
                    st.session_state._sel_listing_source = r["source"]
                    st.session_state._sel_listing_first_seen = r["first_seen"]
                    st.session_state._sel_listing_url = r["url"]
                    st.session_state._sel_listing_model = mid
                    st.session_state.cat = "check"
                    st.rerun()
                hist = pd.read_sql_query(
                    "SELECT ts AS Zeit, price AS Preis FROM price_point "
                    "WHERE listing_id=? ORDER BY ts", conn, params=(r["id"],))
                if len(hist) > 1:
                    st.markdown("**Preisverlauf**")
                    st.line_chart(hist.set_index("Zeit"))

    elif cat == "value":
        st.markdown("#### 📉 Wertstabilität & Restwert")
        mt = real_metrics(mid)
        depr = mt["depr"]
        loss = (model.purchase_price - model.resale_value) if (model.purchase_price and model.resale_value) else None
        a, b = st.columns(2)
        a.metric("Wertverlust / Jahr", f"{depr*100:.0f} %" if depr is not None else "–",
                 help="Kleiner = wertstabiler.")
        b.metric("Restwert nach Haltedauer", f"{model.resale_value:,.0f} €".replace(",", ".")
                 if model.resale_value else "–")
        if loss:
            st.caption(f"Geschätzter Gesamt-Wertverlust über die Haltedauer: "
                       f"{loss:,.0f} €".replace(",", "."))
        make = _make_of(mid)
        model_name = conn.execute("SELECT model FROM car_model WHERE id=?", (mid,)).fetchone()["model"]
        pkw_url = pkw_trend_url(make, model_name)
        st.markdown(f"**📈 Preistrend & Restwerte je Baujahr (pkw.de)** – [Seite öffnen ↗]({pkw_url})")
        try:
            if hasattr(st, "iframe"):
                st.iframe(pkw_url, height=500, scrolling=True)
            else:
                import streamlit.components.v1 as components
                components.iframe(pkw_url, height=500, scrolling=True)
        except Exception:
            st.info("Einbettung blockiert – nutze den Link oben.")
        st.caption("Falls der Rahmen leer bleibt (Cookie-Banner/Blockade): Link oben nutzen.")

    elif cat == "equipment":
        st.markdown("#### ⭐ Ausstattung & Assistenz")
        mt = real_metrics(mid)
        st.caption("Verfügbarkeit je Modell (Serie/Option). **Beim konkreten Angebot prüfen** – "
                   "Ausstattung variiert pro Fahrzeug!")
        for f, lbl in FEATURE_LABELS.items():
            ok = f in mt["features"]
            st.markdown(f"- {'✅' if ok else '❌'} {lbl}")
        if mt["has_matrix"]:
            st.warning("⚠️ Dieses Modell gibt es oft mit **Matrix-/Voll-LED-Scheinwerfern** – "
                       "teuer in der Reparatur. Im Inserat gezielt ein Fahrzeug **ohne** wählen.")
        else:
            st.success("Meist ohne teure Matrix-Scheinwerfer.")

        st.markdown("#### 🅿️ Parken & Dellen")
        lm, wm = mt.get("length_mm"), mt.get("width_mm")
        if lm and wm:
            a, b, c, d = st.columns(4)
            a.metric("Länge", f"{lm/1000:.2f} m".replace(".", ","))
            b.metric("Breite (o. Spiegel)", f"{wm/1000:.2f} m".replace(".", ","))
            c.metric("mit Spiegeln ~", f"{(wm+380)/1000:.2f} m".replace(".", ","))
            tc = mt.get("turning_m")
            d.metric("Wendekreis", f"{tc:.1f} m".replace(".", ",") if tc else "–",
                     help="Kleiner = wendiger beim engen Rangieren.")
            if ref_w and ref_l and ref_choice and ref_choice not in model.label:
                dw, dl = (wm - ref_w) / 10, (lm - ref_l) / 10
                wtxt = (f"**{dw:+.0f} cm** {'breiter' if dw >= 0 else 'schmaler'}").replace("+", "")
                ltxt = (f"**{dl:+.0f} cm** {'länger' if dl >= 0 else 'kürzer'}").replace("+", "")
                verdict = "🟢 leichter zu parken" if dw <= 0 else ("🟠 etwas breiter" if dw <= 6 else "🔴 deutlich breiter")
                st.info(f"📐 Vs. dein **{ref_choice}**: {wtxt}, {ltxt} → {verdict}")
            if park_cm:
                if (wm + 380) > park_cm * 10:
                    st.error(f"📏 Passt schlecht: {(wm+380)/10:.0f} cm (mit Spiegeln) > dein Parkplatz {park_cm} cm.")
                else:
                    rest = park_cm - (wm + 380) / 10
                    st.success(f"✅ Passt: ~{rest:.0f} cm Luft (mit Spiegeln) in deinem {park_cm}-cm-Platz.")
        if mt.get("alu_body"):
            st.warning("⚠️ **Alu-/CFK-Karosserie** (z. B. Tesla, i3): Dellen sind teuer – oft Teiltausch "
                       "statt Ausbeulen, ~2–3× normaler Preis.")
        else:
            st.caption("Stahlkarosserie – Parkdelle günstig: Smart-Repair ~150–250 €, "
                       "Teil lackieren ~300–500 €.")
        got = [f for f in ("einparkhilfe", "rueckfahrkamera") if f in mt["features"]]
        st.caption("Park-Hilfen (senken Dellen-Risiko): "
                   + (", ".join(FEATURE_LABELS[f] for f in got) if got else "keine erfasst")
                   + " – beim konkreten Angebot prüfen.")

    elif cat == "wear":
        from autobewertung.wear import cumulative_cost, upcoming_from_items, load_items
        st.markdown("#### 🔩 Verschleiß – welche Teile bei wie viel km + Kosten")
        items = load_items(conn, mid)
        if not items:
            st.info("Keine Verschleißdaten."); return

        # Untermodell / Motor / Hardware wählen – zeigt EXAKT die passenden Schäden
        variants = sorted({it["variant"] for it in items if it["variant"] and it["variant"] != "alle"})
        opts = ["— alle Varianten —"] + variants
        vv = st.session_state.get("_vin_variant")
        idx = opts.index(vv) if vv in variants else 0
        choice = st.selectbox("Untermodell / Motor / Hardware", opts, index=idx,
                              help="Schäden hängen am Motor/Getriebe/Baujahr. Wähle dein konkretes Untermodell "
                                   "(oder VIN links dekodieren).")
        if choice != "— alle Varianten —":
            items = [it for it in items if it["variant"] in (choice, "alle")]
            st.caption(f"Zeigt: **{choice}** + für alle geltende Teile.")

        # Kostenkurve (für die gewählte Variante)
        curve = [(km, cumulative_cost(items, km)) for km in range(0, 250001, 5000)]
        cdf = pd.DataFrame(curve, columns=["km", "Kumulierte Reparaturkosten €"]).set_index("km")
        st.markdown("**Kostenkurve: kumulierte Reparaturkosten über die Laufleistung**")
        st.line_chart(cdf)

        st.markdown("**Dein km-Fenster** (einstellbar):")
        cS, cP = st.columns(2)
        start = cS.slider("Start-Laufleistung (km)", 0, 250000,
                          int(st.session_state.get("_start_km", 80000)), 5000, key=f"wstart_{mid}")
        span = cP.slider("Fahrleistung im Zeitraum (km)", 5000, 200000,
                         int(st.session_state.get("_span_km", 75000)), 5000, key=f"wspan_{mid}")
        up = upcoming_from_items(items, start, span)
        st.markdown(f"**Fällig zwischen ca. {start:,.0f} und {start+span:,.0f} km:**".replace(",", "."))
        if up:
            st.dataframe(pd.DataFrame([{
                "Teil": u["component"], "Untermodell": u["variant"],
                "typ. bei km": f"{u['at_km']:,}".replace(",", "."),
                "Intervall km": (f"{u['interval_km']:,}".replace(",", ".") if u["interval_km"] else "einmalig"),
                "Einzelkosten €": f"{u['cost_eur']:,.0f}".replace(",", "."),
                "× fällig": u["faellig_im_fenster"],
                "Kosten Fenster €": f"{u['kosten_im_fenster']:,.0f}".replace(",", "."),
            } for u in up]), hide_index=True, width="stretch")
            st.metric("Summe erwartete Reparaturen im Fenster",
                      f"{sum(u['kosten_im_fenster'] for u in up):,.0f} €".replace(",", "."))
        else:
            st.caption("In diesem km-Fenster fällt nichts Größeres an.")

        st.caption("Alle Teile dieser Auswahl (mit Untermodell + Quelle):")
        st.dataframe(pd.DataFrame([{
            "Teil": it["component"], "Untermodell": it["variant"],
            "typ. bei km": f"{it['at_km']:,}".replace(",", "."),
            "Intervall": (f"alle {it['interval_km']:,} km".replace(",", ".") if it["interval_km"] else "einmalig"),
            "Kosten €": f"{it['cost_eur']:,.0f}".replace(",", "."),
            "Quelle": "echt" if it.get("source") == "real" else "Schätzung",
        } for it in items]), hide_index=True, width="stretch")

    elif cat == "check":
        from autobewertung import fairprice
        from autobewertung.advice import (ADAC_KAUFVERTRAG_URL, buy_dossier, kaufvertrag,
                                          model_watchpoints, negotiation_ammo)
        from autobewertung.checks import (CHECKLIST, DIAGNOSE_INTERPRETATION, GOLDEN_RULES,
                                          OBD_CHECKS, PRO_INSPECTION, SCAM_PATTERNS,
                                          age_service_checks, carvertical_url, due_soon,
                                          emission_note, listing_age_days, mileage_plausibility,
                                          next_hu, scam_flags, warranty_note, wear_status,
                                          zahnriemen_time_status)
        from autobewertung.wear import load_items
        st.markdown("#### 🕵️ Kauf-Check – Betrug, Tacho & Plausibilität")
        with st.expander("🏆 Goldene Regeln vom Profi – immer im Kopf behalten"):
            for _grp, _rules in GOLDEN_RULES:
                st.markdown(f"**{_grp}**")
                for _r in _rules:
                    st.markdown(f"- {_r}")

        # Vorbelegung: konkret angeklicktes Inserat, sonst guenstigstes Angebot
        sel_km = st.session_state.get("_sel_listing_km")
        sel_reg = st.session_state.get("_sel_listing_reg")
        sel_price = st.session_state.get("_sel_listing_price")
        if st.session_state.get("_sel_listing_model") == mid and sel_km:
            st.info(f"🚗 Konkretes Angebot: {sel_km:,.0f} km · EZ {sel_reg or '?'}".replace(",", "."))
            def_km, def_reg = int(sel_km), sel_reg or ""
            def_price = int(sel_price) if sel_price else int(model.purchase_price or 10000)
        else:
            lst = conn.execute("SELECT mileage_km, first_reg, price FROM listing WHERE model_id=? "
                               "AND active=1 ORDER BY price LIMIT 1", (mid,)).fetchone()
            def_km = int(lst["mileage_km"]) if lst and lst["mileage_km"] else 100000
            def_reg = (lst["first_reg"] if lst else "") or ""
            def_price = int(lst["price"]) if lst and lst["price"] else int(model.purchase_price or 10000)
        c1, c2, c3 = st.columns(3)
        km = c1.number_input("Laufleistung (km)", 0, 400000, def_km, 5000, key=f"chk_km_{mid}")
        reg = c2.text_input("Erstzulassung (YYYY-MM)", value=def_reg, key=f"chk_reg_{mid}")
        price = c3.number_input("Angebotspreis (€)", 0, 300000, def_price, 500, key=f"chk_price_{mid}")

        # 🚨 Automatischer Betrugs-/Risiko-Check (Fair-Preis-Abgleich + km-Plausibilität)
        try:
            _fm = fairprice.fit(conn)
            _yr = int(str(reg)[:4]) if reg else None
            _age = (datetime.now(timezone.utc).year - _yr) if _yr else None
            _fair = _fm.predict(mid, _age, km, None) if (_fm and _age is not None) else None
        except Exception:
            _fair = None
        _flags = scam_flags(price or None, _fair, km, reg)
        if _fair:
            _gap = (price - _fair) / _fair * 100 if price else 0
            st.caption(f"Fair-Preis (Modell) ~{_fair:,.0f} € → dieses Angebot {_gap:+.0f} %".replace(",", "."))
        if _flags:
            for fl in _flags:
                {"danger": st.error, "warn": st.warning, "info": st.info}[fl["level"]]("🚨 " + fl["text"])
        else:
            st.success("✅ Preis & km unauffällig. Trotzdem: Maschen unten kennen und Checkliste abarbeiten.")

        # 📋 Pflicht-Checks: HU-Fälligkeit · Gewährleistung · Umweltzone
        _hu = next_hu(reg)
        if _hu:
            (st.warning if _hu["level"] == "warn" else st.info)(
                f"🔧 **HU/TÜV** planmäßig fällig ~{_hu['due']} (in {_hu['months_until']} Monaten) – "
                "HU-Bericht + Plakette zeigen lassen (nennt auch den echten km-Stand!).")
        _src = (st.session_state.get("_sel_listing_source")
                if st.session_state.get("_sel_listing_model") == mid else None)
        _wn = warranty_note(_src)
        if _wn:
            st.info("📜 " + _wn)
        _yr = int(str(reg)[:4]) if reg and str(reg)[:4].isdigit() else None
        _em = emission_note(model.drivetrain, _yr)
        if _em:
            (st.warning if _em[0] == "warn" else st.info)("🌍 " + _em[1])

        with st.expander("📖 Typische Betrugsmaschen erkennen (Kleinanzeigen & Co.)"):
            st.caption("Ein Auto weit unter Marktwert ist selten ein Schnäppchen – meist "
                       "verschwiegener Mangel oder Betrug. So erkennst du die Maschen:")
            for _title, _signal, _protect in SCAM_PATTERNS:
                st.markdown(f"**{_title}**")
                st.markdown(f"- 🔍 *Erkennen:* {_signal}")
                st.markdown(f"- 🛡️ *Schutz:* {_protect}")

        # Untermodell (VIN-vorgewaehlt) – wird auch fuer die Warnungen gebraucht
        _vars = sorted({i["variant"] for i in load_items(conn, mid) if i["variant"] != "alle"})
        _vopts = ["— alle —"] + _vars
        _vv = st.session_state.get("_vin_variant")
        _vsel = st.selectbox("Untermodell / Motor", _vopts,
                             index=_vopts.index(_vv) if _vv in _vars else 0, key=f"chk_var_{mid}")
        variant = None if _vsel == "— alle —" else _vsel

        # ⚠️ Nähe-Warnungen: JEDE Reparatur, die in den nächsten km ansteht
        horizon = st.slider("Vorwarnung – prüfe die nächsten … km", 5000, 60000, 20000, 5000,
                            key=f"chk_hz_{mid}")
        soon = due_soon(conn, mid, variant, km, horizon_km=horizon)
        if soon:
            st.markdown(f"**Anstehende Reparaturen zwischen {km:,.0f} und {km+horizon:,.0f} km:**"
                        .replace(",", "."))
            for s in soon:
                st.warning(f"⚠️ **Achtung:** in ~{s['km_until']:,.0f} km (bei {s['next_km']:,.0f} km) "
                           f"**{s['component']}** fällig – ~{s['cost_eur']:,.0f} €".replace(",", "."))
            st.metric("Summe anstehend in diesem Fenster",
                      f"{sum(s['cost_eur'] for s in soon):,.0f} €".replace(",", "."))
        else:
            st.success(f"✅ In den nächsten {horizon:,.0f} km steht nichts Größeres an.".replace(",", "."))

        pl = mileage_plausibility(km, reg)
        if pl:
            msg = f"**{pl['km_per_year']:.0f} km/Jahr** ({pl['age_years']:.1f} J) → {pl['verdict']}"
            {"warn": st.error, "info": st.info, "ok": st.success}[pl["level"]](msg)

        # Zeitbasierter Zahnriemen: alter Wagen mit wenig km -> nach ZEIT faellig
        _zr = zahnriemen_time_status(conn, mid, variant, reg)
        if _zr and _zr["age_years"] >= _zr["years_interval"] - 1:
            _msg = (f"🔩 **Zahnriemen** ({_zr['component']}): Wagen ist {_zr['age_years']:.0f} Jahre alt – "
                    f"der Riemen ist auch ZEITabhängig fällig (~alle {_zr['years_interval']} J, "
                    f"herstellerabhängig), unabhängig von der Laufleistung. **Wechselbeleg verlangen!** "
                    f"Reißt er → Motorschaden. ~{_zr['cost']:,.0f} €".replace(",", "."))
            (st.warning if _zr["due"] else st.info)(_msg)

        # ⏳ Alters-/Zeit-Checks: was der km-Verschleiss verpasst (Bremsfl., 12V, Reifen ...)
        _asc = age_service_checks(reg, km, model.drivetrain)
        if _asc:
            _nwarn = sum(1 for c in _asc if c["level"] == "warn")
            with st.expander(f"⏳ Alters-/Zeit-Checks ({len(_asc)}{f', {_nwarn} wichtig' if _nwarn else ''})",
                             expanded=_nwarn > 0):
                for c in _asc:
                    (st.warning if c["level"] == "warn" else st.info)(c["text"])

        # 🤝 Verhandeln & Dossier – die Analyse zu Handlung verdichtet
        st.divider()
        st.markdown("### 🤝 Verhandeln & Kauf-Dossier")
        _wp, _nr = model_watchpoints(conn, mid, variant)
        if _wp:
            st.markdown("**🎯 Bei genau diesem Modell zuerst prüfen:**")
            for w in _wp:
                st.markdown(f"- **{w['label']}**: {w['detail']}"
                            + (f" (~{w['cost']:,.0f} €)".replace(",", ".") if w["cost"] else ""))
        _sel = st.session_state.get("_sel_listing_model") == mid
        _dsel = listing_age_days(st.session_state.get("_sel_listing_first_seen")) if _sel else None
        _amm = negotiation_ammo(conn, mid, variant, price, km, reg, _fair, _dsel, model.drivetrain)
        if _amm["args"] or _amm["context"]:
            tgt = f" → Zielpreis ~{_amm['target']:,.0f} €".replace(",", ".") if _amm["target"] else ""
            st.markdown(f"**💬 Verhandlungs-Munition** – Masse ~{_amm['reduction']:,.0f} €{tgt}".replace(",", "."))
            for a in _amm["args"]:
                st.markdown(f"- {a['text']}" + (f" (~{a['eur']:,.0f} €)".replace(",", ".") if a["eur"] else ""))
            for cc in _amm["context"]:
                st.markdown(f"- {cc}")
        _doss = buy_dossier(conn, mid, model.label, variant, price, km, reg, _fair, _dsel,
                            _src, model.drivetrain,
                            url=st.session_state.get("_sel_listing_url") if _sel else None)
        with st.expander("📄 Kauf-Dossier – kopieren/mitnehmen zum Besichtigungstermin"):
            st.code(_doss, language="markdown")
            st.download_button("⬇️ Als .md herunterladen", _doss,
                               file_name=f"dossier_{model.label[:30]}.md", key=f"doss_{mid}")
        _kv = kaufvertrag(model.label, reg, km, price, mid)
        with st.expander("📝 Kaufvertrag (vorausgefüllt) – ausdrucken & mitnehmen"):
            st.caption("Privatverkauf, voller Sachmängel-Ausschluss (ohne riskante "
                       f"Besichtigungsklausel). Offiziell: [ADAC Muster-Kaufvertrag ↗]({ADAC_KAUFVERTRAG_URL})")
            st.code(_kv)
            st.download_button("⬇️ Kaufvertrag als .txt", _kv,
                               file_name=f"kaufvertrag_{model.label[:30]}.txt", key=f"kv_{mid}")

        # 🚨 Offizielle Rückrufe (sicherheitskritisch – prüfen ob erledigt!)
        rc = conn.execute("SELECT kba_code, date, description, url FROM recall "
                          "WHERE model_id=? ORDER BY date DESC", (mid,)).fetchall()
        if rc:
            st.error(f"🚨 **{len(rc)} offizielle(r) Rückruf(e)** – am Inserat per FIN prüfen, ob erledigt!")
            for r in rc:
                code = f" · KBA {r['kba_code']}" if r["kba_code"] else ""
                link = f" · [Quelle]({r['url']})" if r["url"] else ""
                st.markdown(f"- **{r['date'] or ''}**{code}: {r['description']}{link}")
        else:
            st.success("✅ Keine Rückrufe erfasst.")

        done, upcoming = wear_status(conn, mid, variant, km)
        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown("**Sollte bei dieser km schon erledigt sein → Belege verlangen!**")
            if done:
                st.dataframe(pd.DataFrame([{"Teil": d["component"], "fällig ab km": f"{d['at_km']:,}".replace(",", "."),
                                            "Kosten €": f"{d['cost_eur']:,.0f}".replace(",", ".")} for d in done]),
                             hide_index=True, width="stretch")
            else:
                st.caption("nichts Größeres")
        with cc2:
            st.markdown("**Steht als Nächstes an → einplanen/Budget**")
            if upcoming:
                st.dataframe(pd.DataFrame([{"Teil": u["component"], "nächste bei km": f"{u['next_km']:,}".replace(",", "."),
                                            "Kosten €": f"{u['cost_eur']:,.0f}".replace(",", ".")} for u in upcoming[:8]]),
                             hide_index=True, width="stretch")
            else:
                st.caption("nichts absehbar")

        vin = st.session_state.get("_vin_raw")
        with st.expander("📜 Fahrzeughistorie prüfen (FIN/VIN) – was sie aufdeckt", expanded=bool(vin)):
            st.markdown(
                f"🔗 [carVertical]({carvertical_url(vin)}) · 🔗 [AutoDNA](https://www.autodna.de/) · "
                "🔗 [Carfax EU](https://www.carfax.eu/)"
                + (f"  \n*FIN vorbelegt: `{vin}`*" if vin else
                   "  \n*Tipp: FIN im Tab „VIN-Decoder“ eingeben – dann wird der carVertical-Link vorbelegt.*"))
            st.markdown(
                "Eine kostenpflichtige Historie (~10–20 €) aggregiert über Ländergrenzen und deckt auf:\n"
                "- **km-Verlauf** über die Jahre → entlarvt Tacho-Rückdreh\n"
                "- **Unfall-/Schadensmeldungen**, Auktions-/Totalschaden-Historie\n"
                "- **Vorbesitzer-Anzahl** und Nutzung (Miet-/Firmenwagen)\n"
                "- **Diebstahl-/Ausland-Status**, offene Finanzierung\n"
                "Bei > ~50 % unter Marktwert oder Auslandsbezug ist das fast Pflicht.")

        st.divider()
        with st.expander("🔬 Profi-Wissen – worauf Mechaniker WIRKLICH schauen (die letzten %)"):
            st.caption("Konkrete „Tells“ zur Zustandsbewertung – Beobachtung → was sie bedeutet.")
            for _sys, _entries in PRO_INSPECTION:
                st.markdown(f"**{_sys}**")
                for _tell, _mean in _entries:
                    st.markdown(f"- **{_tell}** → {_mean}")

        with st.expander("🔌 Digital prüfen mit OBD-Diagnose – der Röntgenblick"):
            st.caption("Ein Diagnosegerät vor dem Warmfahren einstecken verrät mehr als jede Sichtprüfung "
                       "– und ist dein stärkstes Anti-Tacho-Betrug-Werkzeug.")
            for _cat, _entries in OBD_CHECKS:
                st.markdown(f"**{_cat}**")
                for _what, _reveals in _entries:
                    st.markdown(f"- **{_what}** → {_reveals}")

        with st.expander("🧠 Diagnose-Ergebnisse DEUTEN (Gutmann/mega macs & Co.)"):
            st.caption("Du hast einen Profi-Tester, aber der Ausdruck sagt dir nichts? "
                       "So liest du ihn beim Gebrauchtwagenkauf – Beobachtung → Deutung.")
            for _step, _entries in DIAGNOSE_INTERPRETATION:
                st.markdown(f"**{_step}**")
                for _obs, _mean in _entries:
                    st.markdown(f"- **{_obs}** → {_mean}")

        # 🔧 Fehlercode-Deuter: Code vom Diagnosegeraet eingeben -> Klartext/Kosten
        _code = st.text_input("🔧 Fehlercode-Deuter – Code vom Diagnosegerät eingeben",
                              key=f"dtc_{mid}", placeholder="z. B. P0087")
        if _code.strip():
            from autobewertung.dtc import interpret
            _r = interpret(_code)
            _lbl = {"danger": "🔴 ernst / teuer", "warn": "🟡 prüfen",
                    "info": "🟢 meist harmlos"}.get(_r["severity"], "")
            {"danger": st.error, "warn": st.warning, "info": st.info}.get(_r["severity"], st.info)(
                f"**{_r['code']} — {_r['title']}**  ·  {_lbl}")
            if _r["causes"]:
                st.markdown("**Mögliche Ursachen (günstig → teuer):**")
                for _t, _tier in _r["causes"]:
                    st.markdown(f"- {_t}  ·  *{_tier}*")
            if _r["checks"]:
                st.markdown("**So grenzt du es ein:**")
                for _c in _r["checks"]:
                    st.markdown(f"- {_c}")
            if _r["note"]:
                st.caption(_r["note"])

        st.markdown("### ✅ Profi-Prüf-Checkliste")
        total = checked = 0
        for section, entries in CHECKLIST:
            st.markdown(f"**{section}**")
            for i, (q, expl) in enumerate(entries):
                total += 1
                if st.checkbox(q, key=f"chk_{mid}_{section[:4]}_{i}"):
                    checked += 1
                st.caption(expl)
        st.progress(checked / total, text=f"{checked}/{total} Punkte geprüft")

    else:
        st.info("Klicke z. B. Kauf-Check, Verschleiß, Ausstattung, Wertstabilität, "
                "Schwachstellen, Zuverlässigkeit, TCO, Angebote, Ersatzteile oder Werkstätten.")


# ---------------------------------------------------------------------------
# Seitenleiste (Kriterien/Filter/TCO)
# ---------------------------------------------------------------------------
st.sidebar.header("🔎 VIN-Decoder (optional)")
st.sidebar.caption("VIN aus Fahrzeugschein/beim Händler. In Inseraten meist nicht öffentlich.")
vin_in = st.sidebar.text_input("VIN / FIN (17 Zeichen)", key="vin_in")
if st.sidebar.button("VIN dekodieren", width="stretch"):
    from autobewertung.vin import valid_vin, decode_vin, match_model, guess_variant
    if not valid_vin(vin_in):
        st.sidebar.error("Ungültige VIN (17 Zeichen, ohne I/O/Q).")
    else:
        try:
            with st.spinner("Dekodiere über NHTSA …"):
                dec = decode_vin(vin_in)
            st.session_state._vin_decoded = dec
            st.session_state._vin_raw = vin_in.strip().upper()
            mid = match_model(conn, dec)
            st.session_state._vin_matched = mid
            if mid:
                st.session_state.model_id = mid
                st.session_state.cat = "wear"
                var = guess_variant(conn, mid, dec)
                st.session_state._vin_variant = var
                st.rerun()
        except Exception as e:
            st.sidebar.error(f"Fehler beim Dekodieren: {e}")
_dec = st.session_state.get("_vin_decoded")
if _dec:
    line = f"**{_dec.get('make_norm','?')} {_dec.get('Model','?')}** · {_dec.get('ModelYear','?')} · {_dec.get('FuelTypePrimary','?')}"
    if _dec.get("DisplacementL"):
        line += f" · {_dec['DisplacementL']} L"
    st.sidebar.markdown(line)
    if st.session_state.get("_vin_matched"):
        vv = st.session_state.get("_vin_variant")
        st.sidebar.success(f"→ erkannt{' · ' + vv if vv else ''} (Modell unten ausgewählt)")
    else:
        st.sidebar.warning("Modell nicht in der Liste – Eckdaten oben nutzen.")

st.sidebar.header("Gewichtung der Kriterien")
weights = {d: st.sidebar.slider(DIM_LABELS[d], 0.0, 1.0, float(DEFAULT_WEIGHTS[d]), 0.01)
           for d in DIMENSIONS}

st.sidebar.header("Harte Kriterien")
max_price = st.sidebar.number_input("Budget Verbrenner (€)", 0, 200000, 15000, step=1000)
classes = list(CLASS_RANK.keys())
min_class = st.sidebar.selectbox("Mindest-Klasse", classes, index=classes.index("kompakt"))
ev_exc = st.sidebar.checkbox("EV-Ausnahme (darf teurer sein, wenn es spart)", True)
ev_km30 = st.sidebar.number_input("EV: min. km nachladbar in 30 min", 0, 800, 250, step=25)
ev_lr = st.sidebar.number_input("EV: Langstrecke ab Reichweite (km)", 0, 800, 400, step=25,
                                help="Ab dieser Reichweite reicht langsameres Laden.")
ev_km30_lr = st.sidebar.number_input("EV: min. km/30 min bei Langstrecke", 0, 500, 180, step=10)
max_km = st.sidebar.number_input("Max. km (0 = egal)", 0, 400000, 0, step=10000)
park_cm = st.sidebar.number_input("🅿️ Parkplatz-Breite (cm, 0 = egal)", 0, 400, 0, step=5,
                                  help="Autos, die mit Spiegeln nicht bequem reinpassen, werden mit 📏 markiert.")
# Referenzauto fuer den Groessen-Vergleich ("so viel breiter/laenger als …")
_refrows = conn.execute("SELECT cm.make||' '||cm.model AS label, vs.length_mm, vs.width_mm "
                        "FROM car_model cm JOIN vehicle_spec vs ON vs.model_id=cm.id "
                        "WHERE vs.width_mm IS NOT NULL ORDER BY cm.make, cm.model").fetchall()
_ropts = [r["label"] for r in _refrows]
_rdims = {r["label"]: (r["length_mm"], r["width_mm"]) for r in _refrows}
_rdef = "Toyota Auris" if "Toyota Auris" in _ropts else (_ropts[0] if _ropts else None)
ref_choice = st.sidebar.selectbox("Dein aktuelles Auto (Größen-Vergleich)", _ropts,
                                  index=_ropts.index(_rdef) if _rdef else 0) if _ropts else None
ref_l, ref_w = _rdims.get(ref_choice, (None, None))
home_plz = st.sidebar.text_input("Deine PLZ (Werkstattnaehe / Anfahrt)", "79100")
max_dist = st.sidebar.number_input("🔥 Schnäppchen max. Entfernung (km, 0 = egal)", 0, 1000, 0, step=25,
                                   help="Blendet weit entfernte Deals aus. Anfahrt wird ohnehin "
                                        "in den Netto-Vorteil eingerechnet (Sprit/Zeit).")

st.sidebar.header("TCO-Annahmen")
annual_km = st.sidebar.number_input("km / Jahr", 1000, 60000, 15000, step=1000)
years = st.sidebar.number_input("Haltedauer (Jahre)", 1, 15, 5)
p_benzin = st.sidebar.number_input("Benzin €/l", 0.0, 4.0, 1.80, step=0.05)
p_diesel = st.sidebar.number_input("Diesel €/l", 0.0, 4.0, 1.70, step=0.05)

st.sidebar.markdown("**Lade-Mix E-Auto** – Anteile in %")
sh_work = st.sidebar.slider("… Firma (kostenlos)", 0, 100, 95)
sh_home = st.sidebar.slider("… zuhause", 0, 100, 3)
sh_solar = st.sidebar.slider("… eigener Solarstrom", 0, 100, 0)
sh_public = st.sidebar.slider("… öffentl. Schnelllader", 0, 100, 2)
p_strom_home = st.sidebar.number_input("Strom Heim €/kWh", 0.0, 2.0, 0.30, step=0.01)
p_strom_pub = st.sidebar.number_input("Strom Schnelllader €/kWh", 0.0, 2.0, 0.55, step=0.01)
p_strom_work = st.sidebar.number_input("Strom Firma €/kWh", 0.0, 2.0, 0.0, step=0.01)
p_strom_solar = st.sidebar.number_input("Strom Solar €/kWh", 0.0, 2.0, 0.10, step=0.01)

_tco = TcoAssumptions(
    annual_km=annual_km, holding_years=years, price_benzin=p_benzin, price_diesel=p_diesel,
    price_strom_home=p_strom_home, price_strom_public=p_strom_pub,
    price_strom_work=p_strom_work, price_strom_solar=p_strom_solar,
    share_work=sh_work, share_home=sh_home, share_solar=sh_solar, share_public=sh_public)
st.sidebar.caption(f"→ Strom-Mischpreis: {_tco.price_strom_blend:.3f} €/kWh")

crit = Criteria(
    weights=weights, max_price=max_price or None, max_mileage_km=max_km or None,
    min_vehicle_class=min_class, home_plz=home_plz or None,
    ev_price_exception=ev_exc, ev_min_charge_km_30min=ev_km30 or None,
    ev_long_range_km=ev_lr or None, ev_min_charge_km_30min_longrange=ev_km30_lr or None,
    tco=_tco,
)

result = score_models(conn, crit)
ranked = result.ranked
if not ranked and not result.excluded:
    st.warning("Keine Daten. Erst `python -m autobewertung.collect run` ausführen.")
    st.stop()

# 🔔 Schnäppchen-Alarm (neue Top-Preise / Preissenkungen aus dem Tracking)
_alerts = conn.execute("SELECT id, message FROM alert WHERE seen=0 ORDER BY ts DESC LIMIT 25").fetchall()
if _alerts:
    with st.expander(f"🔔 **Schnäppchen-Alarm ({len(_alerts)} neu)**", expanded=True):
        for a in _alerts:
            st.markdown(f"- {a['message']}")
        if st.button("Als gelesen markieren", key="alerts_seen"):
            conn.execute("UPDATE alert SET seen=1 WHERE seen=0")
            conn.commit()
            st.rerun()

# 🔥 Top-Schnäppchen JETZT – modellübergreifend alle Live-Angebote unter fairem
# Marktwert (Fair-Preis-Modell), sortiert nach Abstand, mit Standzeit + Zielpreis.
from autobewertung import fairprice as _fp
from autobewertung.checks import listing_age_days as _lad, negotiation_hint as _nh
_deals = _fp.bargains(conn)
if _deals:
    _by_model = {m.model_id: m for m in ranked}          # nur qualifizierte Modelle

    def _render_deal(e, ms):
        r = conn.execute(
            "SELECT l.mileage_km, l.first_reg, l.url, l.first_seen, l.plz, l.source, l.price_rating, "
            "cm.make||' '||cm.model AS model FROM listing l "
            "JOIN car_model cm ON cm.id=l.model_id WHERE l.id=?", (e.listing_id,)).fetchone()
        # 2. Meinung: AS24s eigene, neutrale Preisbewertung als Gegen-Check
        rt = r["price_rating"] if r["source"] == "autoscout24" else None
        second = (" · ✓ **AS24: günstig**" if rt in (1, 2)
                  else " · ⚠️ AS24: eher teuer – prüfen" if rt in (4, 5) else "")
        days = _lad(r["first_seen"])
        stand = (f" · 🕰️ {days} T online" if days is not None and days >= 45
                 else (f" · {days} T online" if days is not None else ""))
        neg = _nh(e.price, e.fair_price, days)
        room = (f" · 💬 Ziel ~{neg['target']:,.0f} €".replace(",", ".")
                if neg and neg["room_eur"] >= 200 else "")
        qual = (f"Score **{ms.total:.0f}** · Zuverl {ms.dims['reliability']:.0f} · " if ms else "")
        pf = price_floor(conn, e.model_id)
        tief = (" 🔻**Tiefstand**" if pf and pf["n"] >= 8 and pf["low_min"]
                and e.price <= pf["low_min"] * 1.03 else "")
        dist = geo.distance_km(home_plz, r["plz"])
        if dist is not None:
            net = geo.net_saving_eur(e.resid_eur, r["plz"], home_plz)
            near = (f" · 📍 {dist:.0f} km → **netto ~{net:,.0f} €** nach Anfahrt".replace(",", ".")
                    if net is not None else f" · 📍 {dist:.0f} km")
        else:
            near = ""
        lbl = (f"{r['model']} – **{e.price:,.0f} €** · {e.resid_pct*100:+.0f} % vs. fair{tief}{second} · "
               f"{qual}{r['mileage_km'] or '?'} km · EZ {r['first_reg'] or '?'}{near}{stand}{room}").replace(",", ".")
        st.markdown(f"- [{lbl}]({r['url']})" if r["url"] else f"- {lbl}")

    def _within(deals):
        if not max_dist:
            return deals
        keep = []
        for e in deals:
            p = conn.execute("SELECT plz FROM listing WHERE id=?", (e.listing_id,)).fetchone()
            d = geo.distance_km(home_plz, p["plz"] if p else None)
            if d is None or d <= max_dist:      # unbekannte Entfernung durchlassen
                keep.append(e)
        return keep

    # Kombi-Rang: Modell-Qualitaet (Score) + Preisvorteil -> billig UND geil
    _good = _within(sorted((e for e in _deals if e.model_id in _by_model),
                           key=lambda e: _by_model[e.model_id].total + (-e.resid_pct * 100) * 0.8,
                           reverse=True))
    _rest = _within([e for e in _deals if e.model_id not in _by_model])
    if _good:
        with st.expander(f"🔥 **Beste Angebote – günstig UND gutes Auto ({len(_good)})**", expanded=True):
            st.caption("Schnäppchen (unter fairem Preis) auf Modellen, die deine Kriterien erfüllen – "
                       "sortiert nach Modell-Score + Preisvorteil, max. 2 je Modell. "
                       "⚠️ Sehr weit unter fair → 🕵️ Kauf-Check.")
            for e in _fp.top_per_model(_good, per_model=2, limit=15):
                _render_deal(e, _by_model[e.model_id])
    if _rest:
        with st.expander(f"💸 Weitere Schnäppchen außerhalb deiner Kriterien ({len(_rest)}) "
                         "– z. B. über Budget / andere Klasse"):
            for e in _rest[:12]:
                _render_deal(e, None)

# --- Gesamtkosten fuer 5 UND 10 Jahre (jeweils exakt mit eigener Wertverlust-/
#     Verschleiss-Rechnung ueber die Haltedauer) --------------------------------
import dataclasses as _dc


def _totals_for(n: int) -> dict:
    crit_n = _dc.replace(crit, tco=_dc.replace(crit.tco, holding_years=n))
    r = score_models(conn, crit_n)
    return {m.model_id: (m.annual_tco * n) for m in r.ranked if m.annual_tco}


T5, T10 = _totals_for(5), _totals_for(10)
SORTS = {
    "🏆 Gesamtscore": lambda m: -m.total,
    "💶 Gesamtkosten 5 J": lambda m: T5.get(m.model_id, 10**12),
    "💶 Gesamtkosten 10 J": lambda m: T10.get(m.model_id, 10**12),
    "🏷️ Kaufpreis": lambda m: m.purchase_price or 10**12,
    "📈 Wertstabilität (beste)": lambda m: -m.dims.get("value_stability", 0),
}
sort_choice = st.radio("Sortieren nach", list(SORTS), index=1, horizontal=True, key="sortby")
ranked = sorted(ranked, key=SORTS[sort_choice])

with st.expander("🔄 Echte AutoScout24-Angebote für ALLE Modelle laden (~1–2 min)"):
    st.caption("Holt reale Preise/km/Bewertung je Modell. Bei Modellen mit wenigen "
               "Treffern kann ein Ausreißer den Preis verzerren – Bewertung (🟢/🟡) beachten.")
    if st.button("Jetzt alle laden"):
        from autobewertung.sources.autoscout24 import AutoScout24Source
        src = AutoScout24Source()
        models = conn.execute("SELECT id, make, model FROM car_model").fetchall()
        prog = st.progress(0.0)
        for i, mm in enumerate(models, 1):
            try:
                src.fetch_model(conn, mm["id"], mm["make"], mm["model"])
            except Exception:
                pass
            prog.progress(i / len(models), text=f"{mm['make']} {mm['model']}")
        st.success("Fertig – echte Angebote geladen.")
        st.rerun()

# ---------------------------------------------------------------------------
# Layout: Tabelle links (jede Zelle klickbar) · Detail rechts (sofort sichtbar)
# ---------------------------------------------------------------------------
top = ranked[:15]
if st.session_state.get("model_id") not in {m.model_id for m in top}:
    st.session_state.model_id = top[0].model_id
if "cat" not in st.session_state:
    st.session_state.cat = "price"

# Klickbare Tabellen-Spalten mit ECHTEN Zahlen: (cat-key, Header)
CATCOLS = [
    ("tco", "💶 TCO"),
    ("value", "📉 Wertst"),
    ("equipment", "⭐ Ausst"),
    ("weak_points", "🔧 Mängel"),
    ("reliability", "📊 Pannen"),
    ("parts", "🧩 Teile"),
]
# Alle Kategorien (auch die ohne Tabellen-Spalte) fuer die Detail-Leiste
ALL_CATS = [
    ("price", "💰 Angebote"), ("tco", "💶 TCO"), ("wear", "🔩 Verschleiß"),
    ("check", "🕵️ Kauf-Check"), ("value", "📉 Wertstab."), ("equipment", "⭐ Ausstattung"),
    ("weak_points", "🔧 Mängel"), ("reliability", "📊 Zuverl."), ("parts", "🧩 Teile"),
    ("workshop", "🛠️ Werkst."),
]
WANT4 = ["einparkhilfe", "rueckfahrkamera", "notbremsassistent", "spurhalteassistent"]


def cell_value(cat_key, m, mt) -> str:
    if cat_key == "price":
        return f"{m.n_listings}"
    if cat_key == "weak_points":
        return f"{mt['maengel_pct']:.1f}%" if mt["maengel_pct"] is not None else "–"
    if cat_key == "reliability":
        return f"{mt['pannen']:.1f}" if mt["pannen"] is not None else "–"
    if cat_key == "tco":
        return f"{m.annual_tco:.0f}" if m.annual_tco else "–"
    if cat_key == "parts":
        return f"{mt['parts']:.0f}%" if mt["parts"] is not None else "–"
    if cat_key == "workshop":
        return f"{mt['workshops']}"
    if cat_key == "value":
        return f"{mt['depr']*100:.0f}%" if mt["depr"] is not None else "–"
    if cat_key == "equipment":
        n = sum(1 for f in WANT4 if f in mt["features"])
        return f"{n}/4" + ("⚠️" if mt["has_matrix"] else "")
    return "–"


left, right = st.columns([2.9, 1.1])

with left:
    st.subheader(f"Ranking · {len(ranked)} Modelle")
    st.caption("👉 **Modellname** oder eine **Zahl** anklicken – Detail rechts. "
               "Antrieb ⚡Elektro/🔋Hybrid/⛽Verbrenner · **📉 = hoher Wertverlust (≥15 %/J)** · "
               "Wertst = Wertverlust %/J · Ausst = Wunsch-Assistenz von 4 (⚠️ oft teure Matrix-LED) · "
               "Mängel = TÜV % · Pannen /1000 · Teile = Verfügbarkeit %.")
    park_mm = park_cm * 10
    HEADERS = ["Modell", "Baujahr", "Preis günst./ø", "L×B (m)", "GESAMT 5J", "GESAMT 10J", "Wertv/J", "🚨"]
    WIDTHS = [2.4, 0.8, 1.25, 1.05, 1.1, 1.15, 0.95, 0.5] + [0.85] * len(CATCOLS)
    head = st.columns(WIDTHS)
    for c, t in zip(head, HEADERS + [lbl for _, lbl in CATCOLS]):
        c.markdown(f"<small><b>{t}</b></small>", unsafe_allow_html=True)

    def _s(txt):
        return f"<small>{txt}</small>"

    for i, m in enumerate(top, 1):
        mt = real_metrics(m.model_id)
        c = st.columns(WIDTHS)
        sel_model = st.session_state.model_id == m.model_id
        mark = {"elektro": "⚡", "hybrid": "🔋"}.get(m.drivetrain, "⛽")
        depr = mt.get("depr") or 0
        warn = " 📉" if depr >= 0.15 else ""      # hoher Wertverlust
        # passt in den Parkplatz? (Breite mit Spiegeln ~ +38cm)
        w = mt.get("width_mm")
        too_wide = bool(park_mm and w and (w + 380) > park_mm)
        pk = " 📏" if too_wide else ""
        helps = []
        if warn: helps.append(f"📉 hoher Wertverlust ~{depr*100:.0f} %/J")
        if too_wide: helps.append(f"📏 zu breit: {w/10:.0f} cm + Spiegel > Parkplatz {park_cm} cm")
        if c[0].button(f"{'▶ ' if sel_model else ''}{mark} {i}. {m.label}{warn}{pk}", key=f"mdl_{m.model_id}",
                       width="stretch", type="primary" if sel_model else "secondary",
                       help=(" · ".join(helps) or None)):
            st.session_state.model_id = m.model_id
            st.session_state.cat = "price"      # Klick aufs Modell -> echte Angebote
            st.rerun()
        yf, yt = m.details.get("year_from"), m.details.get("year_to")
        c[1].markdown(_s(f"{yf}–{yt}" if yf else "–"), unsafe_allow_html=True)
        if m.purchase_price:
            sub = []
            if m.purchase_km:
                sub.append(f"@ {round(m.purchase_km / 1000)}tkm")
            if m.median_price:
                sub.append("ø " + f"{m.median_price:,.0f}€".replace(",", "."))
            price_html = (f"<b>{m.purchase_price:,.0f} €</b>".replace(",", ".")
                          + (f"<br><span style='color:#888'>{' · '.join(sub)}</span>" if sub else ""))
            c[2].markdown(f"<small>{price_html}</small>", unsafe_allow_html=True)
        else:
            c[2].markdown("–", unsafe_allow_html=True)
        lm, wm = mt.get("length_mm"), mt.get("width_mm")
        lxb = f"{lm/1000:.2f}×{wm/1000:.2f}".replace(".", ",") if lm and wm else "–"
        # Breiten-Delta zum Referenzauto
        d = ""
        if ref_w and wm:
            dw = round((wm - ref_w) / 10)
            d = f" <b>▲{dw}</b>" if dw >= 2 else (f" ▼{abs(dw)}" if dw <= -2 else " ≈")
        c[3].markdown(_s(f"{lxb}{d}{pk}"), unsafe_allow_html=True)
        t5, t10 = T5.get(m.model_id), T10.get(m.model_id)
        c[4].markdown(f"<small><b>{t5:,.0f} €</b></small>".replace(",", ".") if t5 else "–",
                      unsafe_allow_html=True)
        c[5].markdown(f"<small><b>{t10:,.0f} €</b></small>".replace(",", ".") if t10 else "–",
                      unsafe_allow_html=True)
        wv = m.tco_breakdown.get("wertverlust")
        c[6].markdown(_s(f"{wv:,.0f} €".replace(",", ".")) if wv else "–", unsafe_allow_html=True)
        c[7].markdown(_s(f"🚨{mt['recalls']}" if mt["recalls"] else "–"), unsafe_allow_html=True)
        for j, (cat_key, _) in enumerate(CATCOLS):
            active = sel_model and st.session_state.cat == cat_key
            if c[8 + j].button(cell_value(cat_key, m, mt), key=f"cell_{m.model_id}_{cat_key}",
                               width="stretch", type="primary" if active else "secondary"):
                st.session_state.model_id = m.model_id
                st.session_state.cat = cat_key
                st.rerun()

    if result.excluded:
        with st.expander(f"❌ Ausgeschlossen ({len(result.excluded)}) – harte Kriterien"):
            st.dataframe(pd.DataFrame([{"Modell": e.label, "Grund": e.reason}
                                       for e in result.excluded]),
                         hide_index=True, width="stretch")

with right:
    model = next(m for m in ranked if m.model_id == st.session_state.model_id)
    st.markdown(f"### {model.label}")
    a, b = st.columns(2)
    a.metric("Score", f"{model.total:.1f}")
    b.metric("Günstigstes", f"{model.purchase_price:,.0f} €".replace(",", ".") if model.purchase_price else "–",
             help="günstigstes gesundes Angebot (Ausreißer < 45 % Median rausgefiltert)")
    a.metric("TCO/Jahr", f"{model.annual_tco:,.0f} €".replace(",", "."))
    _pk = f"@ {round(model.purchase_km / 1000)} tkm" if model.purchase_km else ""
    _ty = ("typisch ø " + f"{model.median_price:,.0f} €".replace(",", ".")
           + (f" bei ~{round(model.median_km / 1000)} tkm" if model.median_km else "")) if model.median_price else ""
    _fair = f"{model.fair_gap_pct:+.0f} % vs. fair" if model.fair_gap_pct is not None else ""
    _info = " · ".join(x for x in (_pk, _ty, _fair, f"{model.n_listings} Angebote") if x)
    if _info:
        st.caption(_info)
    if model.drivetrain == "elektro":
        b.metric("Laden in 30 min", f"{model.km_per_30min:.0f} km" if model.km_per_30min else "-",
                 help="Nachgeladene km in 30 min Schnellladen – NICHT die Reichweite!")
    else:
        b.metric("Antrieb", model.drivetrain or "-")
    if model.drivetrain == "elektro" and model.range_km:
        st.caption(f"🔋 Reichweite ~{model.range_km:.0f} km (voll) · davon lädt der "
                   f"Schnelllader ~{model.km_per_30min:.0f} km in 30 min nach.")

    # km-Fenster fuer die Verschleiss-Ansicht (Laufleistung des guenstigsten Angebots)
    _sk = conn.execute("SELECT mileage_km FROM listing WHERE model_id=? AND active=1 "
                       "AND mileage_km IS NOT NULL ORDER BY price LIMIT 1",
                       (model.model_id,)).fetchone()
    st.session_state._start_km = _sk[0] if _sk else 80000
    st.session_state._span_km = annual_km * years

    # Kategorie-Leiste (auch Angebote/Werkstätten, die keine Tabellenspalte haben)
    st.caption("Kategorie:")
    r1 = st.columns(4)
    r2 = st.columns(4)
    for idx, (key, lbl) in enumerate(ALL_CATS):
        col = (r1 if idx < 4 else r2)[idx % 4]
        act = st.session_state.cat == key
        if col.button(lbl, key=f"catbar_{key}", width="stretch",
                      type="primary" if act else "secondary"):
            st.session_state.cat = key
            st.rerun()
    st.divider()
    render_category(model, st.session_state.cat)


# ---------------------------------------------------------------------------
# ⚖️ Direkter Modell-Vergleich (2–4 Modelle nebeneinander)
# ---------------------------------------------------------------------------
st.divider()
with st.expander("⚖️ Modelle direkt vergleichen"):
    _labels = [m.label for m in ranked]
    _default = [m.label for m in ranked[:3]]
    _picked = st.multiselect("Modelle wählen (2–4)", _labels, default=_default, max_selections=4)
    _sel = [m for m in ranked if m.label in _picked]
    if len(_sel) < 2:
        st.caption("Mindestens 2 Modelle wählen.")
    else:
        def _rel(mid, metric):
            r = conn.execute("SELECT value FROM reliability_stat WHERE model_id=? AND metric=?",
                             (mid, metric)).fetchone()
            return r[0] if r else None

        def _tire(mid):
            r = conn.execute("SELECT cost_eur, interval_km FROM wear_item WHERE model_id=? "
                             "AND component LIKE 'Reifen%'", (mid,)).fetchone()
            return f"{r[0]:.0f} €/{r[1]//1000}tkm" if r else "–"

        cols = {}
        for m in _sel:
            mt = real_metrics(m.model_id)
            b = m.tco_breakdown
            lm, wm = mt.get("length_mm"), mt.get("width_mm")
            dwid = (f"{(wm-ref_w)/10:+.0f} cm" if (ref_w and wm) else "–")
            maeng = _rel(m.model_id, "maengelquote_pct")
            pann = _rel(m.model_id, "pannen_pro_1000")
            cols[m.label.split(" (")[0]] = {
                "Antrieb": m.drivetrain,
                "Score": f"{m.total:.0f}",
                "Kaufpreis": f"{m.purchase_price:,.0f} €".replace(",", ".") if m.purchase_price else "–",
                "Gesamt 5 J": f"{T5.get(m.model_id, 0):,.0f} €".replace(",", ".") if T5.get(m.model_id) else "–",
                "Gesamt 10 J": f"{T10.get(m.model_id, 0):,.0f} €".replace(",", ".") if T10.get(m.model_id) else "–",
                "Wertverlust/J": f"{b.get('wertverlust', 0):.0f} €",
                "Energie/J": f"{b.get('energie', 0):.0f} €",
                "Versicherung/J": f"{b.get('versicherung', 0):.0f} €",
                "Verschleiß/J": f"{b.get('verschleiss_reparatur', 0):.0f} €",
                "Reifensatz": _tire(m.model_id),
                "TÜV Mängel %": f"{maeng:.1f}" if maeng is not None else "–",
                "ADAC Pannen/1000": f"{pann:.1f}" if pann is not None else "–",
                "Reichweite": f"{m.range_km:.0f} km" if m.range_km else "–",
                "Laden 30 min": f"{m.km_per_30min:.0f} km" if m.km_per_30min else "–",
                "L×B": f"{lm/1000:.2f}×{wm/1000:.2f} m".replace(".", ",") if lm and wm else "–",
                f"Breite vs {ref_choice or 'Ref'}": dwid,
                "Wendekreis": f"{mt['turning_m']:.1f} m".replace(".", ",") if mt.get("turning_m") else "–",
                "Rückrufe": str(conn.execute("SELECT COUNT(*) FROM recall WHERE model_id=?", (m.model_id,)).fetchone()[0]),
                "Ersatzteile": f"{mt['parts']:.0f}/100" if mt.get("parts") is not None else "–",
            }
        st.dataframe(pd.DataFrame(cols), width="stretch")
