"""Streamlit-Dashboard: sortierbare Tabelle + Filter + Detailansicht.

Starten:
    streamlit run autobewertung/dashboard.py

Zeigt das Modell-Ranking als sortierbare Tabelle, erlaubt Live-Anpassung der
Kriterien-Gewichte in der Seitenleiste und blendet je Modell Schwachstellen,
Rueckrufe, Reparaturkosten und Angebote/Preisverlauf ein.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from autobewertung.config import DEFAULT_WEIGHTS, DIMENSIONS, Criteria
from autobewertung.db import DEFAULT_DB, init_db
from autobewertung.scoring import score_models

DIM_LABELS = {
    "price_value": "Preis/Deal",
    "reliability": "Zuverlaessigkeit",
    "weak_points": "Schwachstellen",
    "repair_cost": "Unterhalt",
    "parts_availability": "Ersatzteile",
    "workshop_access": "Werkstaetten",
}

st.set_page_config(page_title="Auto-Bewertung", layout="wide")
st.title("🚗 Auto-Bewertung – Gebrauchtwagen-Ranking")


@st.cache_resource
def get_conn():
    return init_db(DEFAULT_DB)


conn = get_conn()

# --- Seitenleiste: Kriterien ------------------------------------------------
st.sidebar.header("Deine Kriterien")
st.sidebar.caption("Gewichte anpassen – das Ranking rechnet sofort neu.")
weights = {}
for d in DIMENSIONS:
    weights[d] = st.sidebar.slider(DIM_LABELS[d], 0.0, 1.0,
                                   float(DEFAULT_WEIGHTS[d]), 0.01)

st.sidebar.header("Harte Filter")
max_price = st.sidebar.number_input("Max. Preis (€)", 0, 200000, 0, step=1000)
max_km = st.sidebar.number_input("Max. km", 0, 400000, 0, step=10000)
home_plz = st.sidebar.text_input("Deine PLZ (Werkstattnaehe)", "79100")

crit = Criteria(
    weights=weights,
    max_price=max_price or None,
    max_mileage_km=max_km or None,
    home_plz=home_plz or None,
)

ranked = score_models(conn, crit)

if not ranked:
    st.warning("Keine Daten. Erst `python -m autobewertung.collect run` ausfuehren.")
    st.stop()

# --- Haupttabelle -----------------------------------------------------------
rows = []
for i, m in enumerate(ranked, 1):
    row = {"#": i, "Modell": m.label, "Score": m.total,
           "Angebote": m.n_listings,
           "Bester Preis": m.best_deal_eur,
           "Rabatt %": m.best_deal_discount_pct}
    for d in DIMENSIONS:
        row[DIM_LABELS[d]] = m.dims[d]
    rows.append(row)
df = pd.DataFrame(rows)

st.subheader("Ranking")
st.dataframe(
    df,
    use_container_width=True, hide_index=True,
    column_config={
        "Score": st.column_config.ProgressColumn(
            "Score", min_value=0, max_value=100, format="%.1f"),
        "Bester Preis": st.column_config.NumberColumn(format="%.0f €"),
        "Rabatt %": st.column_config.NumberColumn(format="%.0f %%"),
        **{DIM_LABELS[d]: st.column_config.ProgressColumn(
            DIM_LABELS[d], min_value=0, max_value=100, format="%.0f")
           for d in DIMENSIONS},
    },
)

# --- Detailansicht ----------------------------------------------------------
st.subheader("Detail")
labels = [m.label for m in ranked]
sel = st.selectbox("Modell", labels)
model = next(m for m in ranked if m.label == sel)
mid = model.model_id

c1, c2, c3 = st.columns(3)
c1.metric("Gesamtscore", f"{model.total:.1f}")
c2.metric("Angebote", model.n_listings)
if model.best_deal_discount_pct is not None:
    c3.metric("Bester Deal", f"{model.best_deal_eur:,.0f} €".replace(",", "."),
              f"{model.best_deal_discount_pct:.0f}% unter Median")

col_l, col_r = st.columns(2)

with col_l:
    st.markdown("**Bekannte Schwachstellen**")
    wp = pd.read_sql_query(
        "SELECT component AS Bauteil, description AS Beschreibung, severity AS Schwere "
        "FROM weak_point WHERE model_id=? ORDER BY severity DESC", conn, params=(mid,))
    st.dataframe(wp, hide_index=True, use_container_width=True) if not wp.empty \
        else st.caption("keine erfasst")

    st.markdown("**Rueckrufe (KBA)**")
    rc = pd.read_sql_query(
        "SELECT date AS Datum, description AS Beschreibung FROM recall WHERE model_id=?",
        conn, params=(mid,))
    st.dataframe(rc, hide_index=True, use_container_width=True) if not rc.empty \
        else st.caption("keine erfasst")

with col_r:
    st.markdown("**Reparatur/Unterhalt**")
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
    "SELECT id, title AS Titel, price AS Preis, mileage_km AS km, first_reg AS EZ, "
    "location AS Ort, url FROM listing WHERE model_id=? AND active=1 ORDER BY price",
    conn, params=(mid,))
if listings.empty:
    st.caption("keine Angebote")
else:
    st.dataframe(listings.drop(columns=["id"]), hide_index=True, use_container_width=True)
    hist = pd.read_sql_query(
        "SELECT p.ts AS Zeit, p.price AS Preis, l.title AS Angebot "
        "FROM price_point p JOIN listing l ON l.id=p.listing_id "
        "WHERE l.model_id=? ORDER BY p.ts", conn, params=(mid,))
    if not hist.empty:
        pivot = hist.pivot_table(index="Zeit", columns="Angebot", values="Preis")
        st.line_chart(pivot)
