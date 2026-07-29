"""Streamlit-Dashboard: sortierbare Tabelle + Filter + TCO-Detailansicht.

Starten:
    streamlit run autobewertung/dashboard.py
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

DIM_LABELS = {
    "tco": "TCO/Jahr",
    "price_value": "Preis/Deal",
    "reliability": "Zuverlaessigkeit",
    "weak_points": "Schwachstellen",
    "parts_availability": "Ersatzteile",
    "workshop_access": "Werkstaetten",
}
TCO_LABELS = {
    "wertverlust": "Wertverlust", "energie": "Energie", "versicherung": "Versicherung",
    "steuer": "Kfz-Steuer", "wartung_reparatur": "Wartung/Reparatur", "sonstiges": "Sonstiges",
}

st.set_page_config(page_title="Auto-Bewertung", layout="wide")
st.title("🚗 Auto-Bewertung – Gebrauchtwagen mit Total Cost of Ownership")


@st.cache_resource
def get_conn():
    return init_db(DEFAULT_DB)


conn = get_conn()

# --- Seitenleiste -----------------------------------------------------------
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
    weights=weights,
    max_price=max_price or None,
    max_mileage_km=max_km or None,
    min_vehicle_class=min_class,
    home_plz=home_plz or None,
    ev_price_exception=ev_exc,
    ev_min_charge_km_30min=ev_km30 or None,
    tco=TcoAssumptions(annual_km=annual_km, holding_years=years,
                       price_benzin=p_benzin, price_diesel=p_diesel,
                       price_strom_home=p_strom_home, price_strom_public=p_strom_pub),
)

result = score_models(conn, crit)
ranked = result.ranked

if not ranked and not result.excluded:
    st.warning("Keine Daten. Erst `python -m autobewertung.collect run` ausfuehren.")
    st.stop()

# --- Haupttabelle -----------------------------------------------------------
rows = []
for i, m in enumerate(ranked, 1):
    row = {"#": i, "Modell": m.label, "Antrieb": m.drivetrain, "Klasse": m.vehicle_class,
           "Score": m.total, "Kaufpreis": m.purchase_price, "TCO/Jahr": m.annual_tco,
           "Angebote": m.n_listings, "Rabatt %": m.best_deal_discount_pct}
    for d in DIMENSIONS:
        row[DIM_LABELS[d]] = m.dims[d]
    rows.append(row)
df = pd.DataFrame(rows)

st.subheader(f"Ranking  ·  {len(ranked)} qualifizierte Modelle")
st.dataframe(
    df, use_container_width=True, hide_index=True,
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
                     hide_index=True, use_container_width=True)

if not ranked:
    st.stop()

# --- Detailansicht ----------------------------------------------------------
st.subheader("Detail")
sel = st.selectbox("Modell", [m.label for m in ranked])
model = next(m for m in ranked if m.label == sel)
mid = model.model_id

c1, c2, c3, c4 = st.columns(4)
c1.metric("Gesamtscore", f"{model.total:.1f}")
c2.metric("Kaufpreis", f"{model.purchase_price:,.0f} €".replace(",", "."))
c3.metric("TCO / Jahr", f"{model.annual_tco:,.0f} €".replace(",", "."))
c4.metric("Restwert n. Haltedauer", f"{model.resale_value:,.0f} €".replace(",", "."))

if model.drivetrain == "elektro":
    e1, e2, e3 = st.columns(3)
    e1.metric("Reichweite", f"{model.range_km:.0f} km" if model.range_km else "-")
    e2.metric("Laden 30 min", f"{model.km_per_30min:.0f} km" if model.km_per_30min else "-")
    if model.ev_savings_year is not None:
        e3.metric("Ersparnis/Jahr vs. Verbrenner", f"{model.ev_savings_year:,.0f} €".replace(",", "."),
                  help="Laufende Kosten ggue. Verbrenner-Median. Deckt den Budget-Aufpreis.")

col_l, col_r = st.columns(2)

with col_l:
    st.markdown("**TCO-Aufschluesselung (pro Jahr)**")
    if model.tco_breakdown:
        tco_df = pd.DataFrame(
            [{"Posten": TCO_LABELS.get(k, k), "€/Jahr": v}
             for k, v in model.tco_breakdown.items()]).sort_values("€/Jahr", ascending=False)
        st.dataframe(tco_df, hide_index=True, use_container_width=True)
        st.bar_chart(tco_df.set_index("Posten"))

    st.markdown("**Bekannte Schwachstellen**")
    wp = pd.read_sql_query(
        "SELECT component AS Bauteil, description AS Beschreibung, severity AS Schwere "
        "FROM weak_point WHERE model_id=? ORDER BY severity DESC", conn, params=(mid,))
    st.dataframe(wp, hide_index=True, use_container_width=True) if not wp.empty \
        else st.caption("keine erfasst")

with col_r:
    st.markdown("**Rueckrufe (KBA)**")
    rc = pd.read_sql_query(
        "SELECT date AS Datum, description AS Beschreibung FROM recall WHERE model_id=?",
        conn, params=(mid,))
    st.dataframe(rc, hide_index=True, use_container_width=True) if not rc.empty \
        else st.caption("keine erfasst")

    st.markdown("**Reparatur/Wartung**")
    rp = pd.read_sql_query(
        "SELECT category AS Posten, typical_eur AS EUR, period AS Zeitraum "
        "FROM repair_cost WHERE model_id=?", conn, params=(mid,))
    st.dataframe(rp, hide_index=True, use_container_width=True) if not rp.empty \
        else st.caption("keine erfasst")

    st.markdown("**Ersatzteile**")
    pa = pd.read_sql_query(
        "SELECT score AS Verfuegbarkeit, avg_price_idx AS Preisindex "
        "FROM parts_availability WHERE model_id=?", conn, params=(mid,))
    st.dataframe(pa, hide_index=True, use_container_width=True) if not pa.empty \
        else st.caption("keine erfasst")

st.markdown("**Angebote & Preisverlauf**")
listings = pd.read_sql_query(
    "SELECT title AS Titel, price AS Preis, mileage_km AS km, first_reg AS EZ, "
    "location AS Ort FROM listing WHERE model_id=? AND active=1 ORDER BY price",
    conn, params=(mid,))
if listings.empty:
    st.caption("keine Angebote (Marktpreis geschaetzt)")
else:
    st.dataframe(listings, hide_index=True, use_container_width=True)
    hist = pd.read_sql_query(
        "SELECT p.ts AS Zeit, p.price AS Preis, l.title AS Angebot "
        "FROM price_point p JOIN listing l ON l.id=p.listing_id "
        "WHERE l.model_id=? ORDER BY p.ts", conn, params=(mid,))
    if not hist.empty:
        st.line_chart(hist.pivot_table(index="Zeit", columns="Angebot", values="Preis"))
