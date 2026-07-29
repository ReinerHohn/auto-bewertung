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
from urllib.parse import quote_plus

# Streamlit legt beim Start nur das Skriptverzeichnis auf sys.path, nicht das
# Projekt-Root -> Paket-Import sicherstellen, bevor autobewertung importiert wird.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

from autobewertung.config import DEFAULT_WEIGHTS, DIMENSIONS, Criteria
from autobewertung.db import DEFAULT_DB, init_db
from autobewertung.scoring import score_models
from autobewertung.tco import CLASS_RANK, TcoAssumptions

# Spaltenlabels der sechs Score-Dimensionen (0..100)
DIM_LABELS = {
    "tco": "Kosten-Score",
    "price_value": "Preis/Deal",
    "reliability": "Zuverlaessigkeit",
    "weak_points": "Schwachstellen",
    "parts_availability": "Ersatzteile",
    "workshop_access": "Werkstaetten",
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
TCO_LABELS = {
    "wertverlust": "Wertverlust", "energie": "Energie", "versicherung": "Versicherung",
    "steuer": "Kfz-Steuer", "wartung_reparatur": "Wartung/Reparatur", "sonstiges": "Sonstiges",
}
TCO_EXPLAIN = {
    "wertverlust": "Kaufpreis minus geschaetzter Restwert nach der Haltedauer, auf ein Jahr umgelegt.",
    "energie": "Verbrauch x Jahreskilometer x Energiepreis (Sprit bzw. Strom-Mischpreis Heim/Schnelllader).",
    "versicherung": "Angesetzte Versicherungspraemie pro Jahr (Teilkasko-Groessenordnung).",
    "steuer": "Kfz-Steuer pro Jahr (E-Autos meist befreit).",
    "wartung_reparatur": "Inspektionen und typische Reparaturen, auf ein Jahr umgelegt.",
    "sonstiges": "Pauschale fuer Reifen, HU/AU und Kleinkram.",
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


st.set_page_config(page_title="Auto-Bewertung", layout="wide")
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
    return {
        "maengel_pct": one("SELECT value FROM reliability_stat WHERE model_id=? AND metric='maengelquote_pct'", (model_id,)),
        "pannen": one("SELECT value FROM reliability_stat WHERE model_id=? AND metric='pannen_pro_1000'", (model_id,)),
        "parts": one("SELECT score FROM parts_availability WHERE model_id=?", (model_id,)),
        "workshops": one("SELECT COUNT(*) FROM workshop WHERE make=? OR make IS NULL", (make,)) or 0,
        "n_weak": one("SELECT COUNT(*) FROM weak_point WHERE model_id=?", (model_id,)) or 0,
    }


def render_category(model, cat: str) -> None:
    mid = model.model_id

    if cat == "weak_points":
        st.markdown("#### 🔧 Schwachstellen & Rückrufe")
        mk = _make_of(mid)
        mdl = conn.execute("SELECT model FROM car_model WHERE id=?", (mid,)).fetchone()["model"]
        wp = conn.execute(
            "SELECT component,description,severity,source,url FROM weak_point "
            "WHERE model_id=? ORDER BY severity DESC", (mid,)).fetchall()
        rc = conn.execute(
            "SELECT kba_code,date,description,url FROM recall WHERE model_id=?", (mid,)).fetchall()
        if not wp and not rc:
            st.info("Keine erfasst."); return
        for w in wp:
            sev = SEV.get(w["severity"], "?")
            bar = {1: "🟡", 2: "🟠", 3: "🔴"}.get(w["severity"], "⚪")
            with st.expander(f"{bar} {w['component']} · Schwere: {sev}"):
                st.markdown(f"**{w['component']}** — {w['description']}")
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
            "SELECT source,metric,value,vehicle_age,year FROM reliability_stat "
            "WHERE model_id=? ORDER BY source", (mid,)).fetchall()
        if not rows:
            st.info("Keine erfasst."); return
        for r in rows:
            unit = "Pannen/1000 Fzg" if r["metric"].startswith("pannen") else "% Mängel (HU)"
            with st.expander(f"{r['source']}: {r['value']:.1f} {unit}"):
                st.write(f"**Metrik:** {r['metric']}")
                st.write(f"**Wert:** {r['value']:.2f} ({unit})")
                st.caption(f"Fahrzeugalter: {r['vehicle_age'] or 'n/a'} · Berichtsjahr: {r['year'] or 'n/a'} "
                           f"· Quelle: {r['source']}")

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

    elif cat == "price":
        st.markdown("#### 💰 Angebote in Portalen")
        make = _make_of(mid)
        model_name = conn.execute("SELECT model FROM car_model WHERE id=?", (mid,)).fetchone()["model"]
        st.markdown("**Direkt zu den Portalen (Suche nach diesem Modell):**")
        for label, url in portal_links(make, model_name):
            st.markdown(f"- [{label}]({url})")

        # pkw.de-Preistrend fuer dieses Modell direkt eingebettet
        pkw_url = pkw_trend_url(make, model_name)
        st.markdown(f"**📈 Preistrend & Baujahre (pkw.de)** – [Seite öffnen]({pkw_url})")
        try:
            if hasattr(st, "iframe"):
                st.iframe(pkw_url, height=520, scrolling=True)
            else:
                import streamlit.components.v1 as components
                components.iframe(pkw_url, height=520, scrolling=True)
        except Exception:
            st.caption("Einbettung durch die Seite blockiert – nutze den Link oben.")

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
        st.markdown("**Erfasste Angebote & Preisverlauf (je Angebot)**")
        rows = conn.execute(
            "SELECT id,title,price,mileage_km,first_reg,location,plz,url,source "
            "FROM listing WHERE model_id=? AND active=1 ORDER BY price", (mid,)).fetchall()
        if not rows:
            st.info("Noch keine Angebote in der DB – Marktpreis geschätzt. "
                    "Nutze die Portal-Links oben oder importiere Angebote per CSV.")
            if model.purchase_price:
                st.metric("Geschätzter Marktpreis", f"{model.purchase_price:,.0f} €".replace(",", "."))
            return
        for r in rows:
            title = f"{r['price']:,.0f} € · {r['mileage_km'] or '?'} km · EZ {r['first_reg'] or '?'}".replace(",", ".")
            with st.expander(f"{title} · {r['location'] or ''}"):
                st.write(f"**Preis:** {r['price']:,.0f} €".replace(",", "."))
                st.write(f"**Laufleistung:** {r['mileage_km'] or '?'} km")
                st.write(f"**Erstzulassung:** {r['first_reg'] or '?'}")
                st.write(f"**Ort:** {r['location'] or '-'} (PLZ {r['plz'] or '-'})")
                st.caption(f"Quelle: {r['source']}")
                if r["url"]:
                    st.markdown(f"[Zum Inserat]({r['url']})")
                hist = pd.read_sql_query(
                    "SELECT ts AS Zeit, price AS Preis FROM price_point "
                    "WHERE listing_id=? ORDER BY ts", conn, params=(r["id"],))
                if len(hist) > 1:
                    st.markdown("**Preisverlauf**")
                    st.line_chart(hist.set_index("Zeit"))

    else:
        st.info("Diese Spalte hat keine Detail-Liste. Klicke z. B. Schwachstellen, "
                "Zuverlässigkeit, TCO/Jahr, Preis/Deal, Ersatzteile oder Werkstätten.")


# ---------------------------------------------------------------------------
# Seitenleiste (Kriterien/Filter/TCO)
# ---------------------------------------------------------------------------
st.sidebar.header("Gewichtung der Kriterien")
weights = {d: st.sidebar.slider(DIM_LABELS[d], 0.0, 1.0, float(DEFAULT_WEIGHTS[d]), 0.01)
           for d in DIMENSIONS}

st.sidebar.header("Harte Kriterien")
max_price = st.sidebar.number_input("Budget Verbrenner (€)", 0, 200000, 15000, step=1000)
classes = list(CLASS_RANK.keys())
min_class = st.sidebar.selectbox("Mindest-Klasse", classes, index=classes.index("kompakt"))
ev_exc = st.sidebar.checkbox("EV-Ausnahme (darf teurer sein, wenn es spart)", True)
ev_km30 = st.sidebar.number_input("EV: min. km nachladbar in 30 min", 0, 800, 300, step=25)
max_km = st.sidebar.number_input("Max. km (0 = egal)", 0, 400000, 0, step=10000)
home_plz = st.sidebar.text_input("Deine PLZ (Werkstattnaehe)", "79100")

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
    ev_price_exception=ev_exc, ev_min_charge_km_30min=ev_km30 or None, tco=_tco,
)

result = score_models(conn, crit)
ranked = result.ranked
if not ranked and not result.excluded:
    st.warning("Keine Daten. Erst `python -m autobewertung.collect run` ausführen.")
    st.stop()

# ---------------------------------------------------------------------------
# Layout: Tabelle links (jede Zelle klickbar) · Detail rechts (sofort sichtbar)
# ---------------------------------------------------------------------------
top = ranked[:15]
if st.session_state.get("model_id") not in {m.model_id for m in top}:
    st.session_state.model_id = top[0].model_id
if "cat" not in st.session_state:
    st.session_state.cat = "price"

# Kategorie-Spalten mit ECHTEN Zahlen (nicht 0..100-Score): (cat-key, Header)
CATCOLS = [
    ("price", "💰 Angeb."),
    ("weak_points", "🔧 Mängel %"),
    ("reliability", "📊 Pannen"),
    ("tco", "💶 TCO €/J"),
    ("parts", "🧩 Teile %"),
    ("workshop", "🛠️ Werkst."),
]


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
    return "–"


left, right = st.columns([2.15, 1.35])

with left:
    st.subheader(f"Ranking · {len(ranked)} Modelle")
    st.caption("👉 **Modellname** oder eine **Zahl** anklicken – Detail erscheint rechts. "
               "Mängel = TÜV-Quote %, Pannen = pro 1000, Teile = Verfügbarkeit %.")
    WIDTHS = [3.0, 1.3] + [1.0] * len(CATCOLS)
    head = st.columns(WIDTHS)
    for c, t in zip(head, ["Modell", "Preis"] + [lbl for _, lbl in CATCOLS]):
        c.markdown(f"<small><b>{t}</b></small>", unsafe_allow_html=True)

    for i, m in enumerate(top, 1):
        mt = real_metrics(m.model_id)
        c = st.columns(WIDTHS)
        sel_model = st.session_state.model_id == m.model_id
        if c[0].button(f"{'▶ ' if sel_model else ''}{i}. {m.label}", key=f"mdl_{m.model_id}",
                       width="stretch", type="primary" if sel_model else "secondary"):
            st.session_state.model_id = m.model_id
            st.rerun()
        c[1].markdown(f"<small>{m.purchase_price:,.0f} €</small>".replace(",", ".")
                      if m.purchase_price else "–", unsafe_allow_html=True)
        for j, (cat_key, _) in enumerate(CATCOLS):
            active = sel_model and st.session_state.cat == cat_key
            if c[2 + j].button(cell_value(cat_key, m, mt), key=f"cell_{m.model_id}_{cat_key}",
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
    b.metric("Kaufpreis", f"{model.purchase_price:,.0f} €".replace(",", "."))
    a.metric("TCO/Jahr", f"{model.annual_tco:,.0f} €".replace(",", "."))
    if model.drivetrain == "elektro":
        b.metric("Laden 30 min", f"{model.km_per_30min:.0f} km" if model.km_per_30min else "-")
    else:
        b.metric("Antrieb", model.drivetrain or "-")
    st.divider()
    render_category(model, st.session_state.cat)
