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


def render_category(model, cat: str) -> None:
    mid = model.model_id

    if cat == "weak_points":
        st.markdown("#### 🔧 Schwachstellen & Rückrufe")
        wp = conn.execute(
            "SELECT component,description,severity,source,url FROM weak_point "
            "WHERE model_id=? ORDER BY severity DESC", (mid,)).fetchall()
        rc = conn.execute(
            "SELECT kba_code,date,description,url FROM recall WHERE model_id=?", (mid,)).fetchall()
        if not wp and not rc:
            st.info("Keine erfasst."); return
        for w in wp:
            sev = SEV.get(w["severity"], "?")
            with st.expander(f"⚠️ {w['component']} · Schwere: {sev}"):
                st.write(w["description"])
                st.caption(f"Schweregrad {w['severity']} ({sev}) · Quelle: {w['source'] or '-'}")
                if w["url"]:
                    st.markdown(f"[Mehr Details]({w['url']})")
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
        st.markdown("#### 💰 Angebote & Preisverlauf")
        rows = conn.execute(
            "SELECT id,title,price,mileage_km,first_reg,location,plz,url,source "
            "FROM listing WHERE model_id=? AND active=1 ORDER BY price", (mid,)).fetchall()
        if not rows:
            st.info("Keine Angebote – Marktpreis geschätzt.")
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
p_strom_home = st.sidebar.number_input("Strom Heim €/kWh", 0.0, 2.0, 0.30, step=0.01)
p_strom_pub = st.sidebar.number_input("Strom Schnelllader €/kWh", 0.0, 2.0, 0.55, step=0.01)

crit = Criteria(
    weights=weights, max_price=max_price or None, max_mileage_km=max_km or None,
    min_vehicle_class=min_class, home_plz=home_plz or None,
    ev_price_exception=ev_exc, ev_min_charge_km_30min=ev_km30 or None,
    tco=TcoAssumptions(annual_km=annual_km, holding_years=years,
                       price_benzin=p_benzin, price_diesel=p_diesel,
                       price_strom_home=p_strom_home, price_strom_public=p_strom_pub),
)

result = score_models(conn, crit)
ranked = result.ranked
if not ranked and not result.excluded:
    st.warning("Keine Daten. Erst `python -m autobewertung.collect run` ausführen.")
    st.stop()

# ---------------------------------------------------------------------------
# Haupttabelle (klickbar: Zeile = Auto, Spalte = Kategorie)
# ---------------------------------------------------------------------------
rows = []
for i, m in enumerate(ranked, 1):
    row = {"#": i, "Modell": m.label, "Antrieb": m.drivetrain, "Klasse": m.vehicle_class,
           "Score": m.total, "Kaufpreis": m.purchase_price, "TCO/Jahr": m.annual_tco,
           "Angebote": m.n_listings, "Rabatt %": m.best_deal_discount_pct}
    for d in DIMENSIONS:
        row[DIM_LABELS[d]] = m.dims[d]
    rows.append(row)
df = pd.DataFrame(rows)

st.subheader(f"Ranking · {len(ranked)} qualifizierte Modelle")
st.caption("👉 Zeile anklicken (Auto) und Spalte anklicken (Kategorie) für die Detail-Liste darunter.")
event = st.dataframe(
    df, width="stretch", hide_index=True,
    on_select="rerun", selection_mode=["single-row", "single-column"], key="rank",
    column_config={
        "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
        "Kaufpreis": st.column_config.NumberColumn(format="%.0f €"),
        "TCO/Jahr": st.column_config.NumberColumn("TCO/Jahr", format="%.0f €"),
        "Rabatt %": st.column_config.NumberColumn(format="%.0f %%"),
        **{DIM_LABELS[d]: st.column_config.ProgressColumn(
            DIM_LABELS[d], min_value=0, max_value=100, format="%.0f") for d in DIMENSIONS},
    },
)

if result.excluded:
    with st.expander(f"❌ Ausgeschlossen ({len(result.excluded)}) – harte Kriterien"):
        st.dataframe(pd.DataFrame([{"Modell": e.label, "Grund": e.reason}
                                   for e in result.excluded]),
                     hide_index=True, width="stretch")

if not ranked:
    st.stop()

# --- Auswahl auswerten ------------------------------------------------------
sel = event.selection if event else None
sel_rows = list(sel.get("rows", [])) if sel else []
sel_cols = list(sel.get("columns", [])) if sel else []

model = ranked[sel_rows[0]] if sel_rows else ranked[0]
picked_col = sel_cols[0] if sel_cols else None

st.divider()
head = f"### {model.label}"
if not sel_rows:
    head += "  \n*(oben eine Zeile anklicken, um ein anderes Auto zu wählen)*"
st.markdown(head)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Score", f"{model.total:.1f}")
k2.metric("Kaufpreis", f"{model.purchase_price:,.0f} €".replace(",", "."))
k3.metric("TCO/Jahr", f"{model.annual_tco:,.0f} €".replace(",", "."))
if model.drivetrain == "elektro":
    k4.metric("Laden 30 min", f"{model.km_per_30min:.0f} km" if model.km_per_30min else "-")
else:
    k4.metric("Antrieb", model.drivetrain or "-")

# --- Level 2/3: Detail-Liste ------------------------------------------------
if picked_col and picked_col in COLUMN_TO_CATEGORY:
    st.caption(f"Kategorie: **{picked_col}** — klicke einen Eintrag für Details.")
    render_category(model, COLUMN_TO_CATEGORY[picked_col])
elif picked_col:
    st.info(f"Spalte „{picked_col}“ hat keine Detail-Liste. Klicke z. B. Schwachstellen, "
            "Zuverlässigkeit, TCO/Jahr, Preis/Deal, Ersatzteile oder Werkstätten.")
else:
    st.caption("Keine Spalte gewählt – alle Kategorien als Reiter:")
    tab_keys = ["tco", "price", "reliability", "weak_points", "parts", "workshop"]
    tab_names = ["💶 TCO", "💰 Preis", "📊 Zuverlässigkeit", "🔧 Schwachstellen",
                 "🧩 Ersatzteile", "🛠️ Werkstätten"]
    for tab, key in zip(st.tabs(tab_names), tab_keys):
        with tab:
            render_category(model, key)
