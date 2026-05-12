"""FakeDoor Habicapital — dashboard live con filtros + funnel desde HubSpot+BQ."""
from __future__ import annotations

import os
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def _bootstrap_from_st_secrets() -> None:
    keys = [
        "BQ_PROJECT_ID", "BQ_DATASET_PROJECT", "BQ_DATASET", "BQ_TABLE",
        "GOOGLE_APPLICATION_CREDENTIALS_JSON",
        "HUBSPOT_ACCESS_TOKEN",
        "GOOGLE_SHEETS_ID", "GOOGLE_SHEETS_TAB", "GOOGLE_SHEETS_CREDENTIALS",
        "SCORE_API_URL", "SCORE_API_TOKEN",
    ]
    try:
        for k in keys:
            if k in st.secrets and not os.environ.get(k):
                os.environ[k] = str(st.secrets[k])
    except Exception:
        return


_bootstrap_from_st_secrets()

from src.experiments import REGISTRY
from src.sources import bigquery as bq_src
from src.sources import gsheets as gs_src
from src.sources import hubspot as hs_src
from src.sources import risk_score as score_src
from src.styling import (
    inject_base_css, kpi_card,
    DEEP, PRIMARY, MED, ACCENT, LIGHT, PALE, WHITE,
    GREEN_DARK, GREEN_LIGHT, YELLOW, GREY, RED,
)

st.set_page_config(page_title="FakeDoor Habicapital", layout="wide")
inject_base_css()

EXPERIMENT = next(e for e in REGISTRY if e.slug == "fakedoor-habicapital")

st.markdown(
    f"<h1 style='color:{DEEP};font-size:1.5rem;font-weight:700;margin-bottom:0'>"
    f"Fake Door · Crédito de Libre Inversión con Garantía Hipotecaria</h1>"
    f"<div style='color:{MED};font-size:0.8rem;margin-bottom:20px'>"
    f"Habi Capital · lanzado {EXPERIMENT.start_date} · A/B: AH=84m vs BH=120m · 20% EA</div>",
    unsafe_allow_html=True,
)


def _norm_phone(s: object) -> str:
    s = str(s).strip().replace(" ", "").lstrip("+")
    if s.startswith("57") and len(s) > 10:
        s = s[2:]
    return s[-10:] if len(s) >= 10 else s


# ─────────────────────────────────────────────────────────────────────────────
# Loaders
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=120, show_spinner="HubSpot · deals fakedoor…")
def load_hs_deals() -> pd.DataFrame:
    return hs_src.fetch_fakedoor_deals(since_iso=EXPERIMENT.start_date)


@st.cache_data(ttl=120, show_spinner="Leads + scores (Sheet cache)…")
def load_leads_with_scores() -> tuple[pd.DataFrame, dict]:
    df_raw = gs_src.fetch_tab("Leads")
    if df_raw.empty:
        return df_raw, {}
    return score_src.enrich_leads_with_scores(df_raw, tab="Leads", cedula_col="cedula")


@st.cache_data(ttl=120, show_spinner="Entrevistas…")
def load_entrevistas() -> pd.DataFrame:
    df = gs_src.fetch_tab("Entrevista")
    if not df.empty and "telefono" in df.columns:
        df["telefono"] = df["telefono"].astype(str).str.strip()
        df["phone_norm"] = df["telefono"].apply(_norm_phone)
    return df


@st.cache_data(ttl=180, show_spinner="Infobip (envios WA)…")
def load_infobip_phones() -> set[str]:
    df = gs_src.fetch_tab("Infobip")
    if df.empty:
        return set()
    phones: set[str] = set()
    for col in df.columns:
        for v in df[col].astype(str):
            p = _norm_phone(v)
            if p and p.isdigit() and len(p) == 10:
                phones.add(p)
    return phones


@st.cache_data(ttl=300, show_spinner="BigQuery · eventos de landing…")
def load_landing_events() -> pd.DataFrame:
    try:
        return bq_src.fetch_fakedoor_landing_events()
    except Exception as exc:
        st.warning(f"BigQuery falló: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["uuid", "total_events", "reached_consent"])


# ─────────────────────────────────────────────────────────────────────────────
# Carga base
# ─────────────────────────────────────────────────────────────────────────────
try:
    df_leads, score_stats = load_leads_with_scores()
except Exception as exc:
    st.error(f"Error cargando Leads: {type(exc).__name__}: {exc}")
    st.stop()

if df_leads.empty:
    st.warning("La pestaña Leads está vacía.")
    st.stop()

df_int = load_entrevistas()

try:
    df_hs = load_hs_deals()
except Exception as exc:
    df_hs = pd.DataFrame()
    st.warning(f"HubSpot no disponible: {type(exc).__name__}: {exc}")

infobip_phones = load_infobip_phones()
df_bq = load_landing_events()


# ─────────────────────────────────────────────────────────────────────────────
# Filtros (sidebar)
# ─────────────────────────────────────────────────────────────────────────────
if not df_hs.empty:
    estados_all = sorted([s for s in df_hs.get("dealstage", pd.Series()).dropna().unique() if s])
    oport_all = sorted([s for s in df_hs.get("oportunidad_del_negocio_co", pd.Series()).dropna().unique() if s])
else:
    estados_all, oport_all = [], []

with st.sidebar:
    st.markdown(f"<div style='color:{LIGHT};font-weight:700;font-size:0.9rem;margin-bottom:14px'>Filtros</div>", unsafe_allow_html=True)
    st.markdown("### Estado del Negocio")
    sel_estados = st.multiselect("estados", estados_all, default=estados_all, label_visibility="collapsed",
                                  help="dealstage en HubSpot (puede salir como ID interno)")
    st.markdown("### Oportunidad del Negocio")
    sel_oport = st.multiselect("oportunidades", oport_all, default=oport_all, label_visibility="collapsed")
    if not df_hs.empty and ("flag_fakedoor" in df_hs.columns):
        flags_all = sorted([s for s in df_hs["flag_fakedoor"].dropna().unique() if s])
        st.markdown("### Flag fakedoor")
        sel_flags = st.multiselect("flags", flags_all, default=flags_all, label_visibility="collapsed")
    else:
        sel_flags = []
    st.markdown("---")
    st.caption("Los filtros aplican sobre la base HubSpot y propagan al funnel y al pipeline.")


# ─────────────────────────────────────────────────────────────────────────────
# Aplicar filtros HubSpot
# ─────────────────────────────────────────────────────────────────────────────
df_hs_f = df_hs.copy()
if not df_hs_f.empty:
    if sel_estados and len(sel_estados) < len(estados_all):
        df_hs_f = df_hs_f[df_hs_f["dealstage"].isin(sel_estados) | df_hs_f["dealstage"].isna()]
    if sel_oport and len(sel_oport) < len(oport_all):
        df_hs_f = df_hs_f[df_hs_f["oportunidad_del_negocio_co"].isin(sel_oport) | df_hs_f["oportunidad_del_negocio_co"].isna()]
    if "flag_fakedoor" in df_hs_f.columns and sel_flags and len(sel_flags) < len(flags_all):
        df_hs_f = df_hs_f[df_hs_f["flag_fakedoor"].isin(sel_flags)]

allowed_uuids: set[str] = set(df_hs_f["deal_uuid"].dropna().astype(str)) if not df_hs_f.empty else set()
allowed_phones: set[str] = set(df_hs_f["phone"].dropna().apply(_norm_phone)) if not df_hs_f.empty and "phone" in df_hs_f.columns else set()


# Cruces sobre Leads (filtrados a los uuids de HubSpot, si HubSpot disponible)
df = df_leads.copy()
df["phone_norm"] = df["telefono"].apply(_norm_phone)
if not df_int.empty:
    df = df.merge(df_int[["phone_norm", "tiene hipoteca?"]], on="phone_norm", how="left")
else:
    df["tiene hipoteca?"] = None

if allowed_uuids:
    df_in_filter = df[df["uuid"].astype(str).isin(allowed_uuids)].copy()
else:
    df_in_filter = df.copy()  # sin filtro HubSpot → mostramos todo

df_in_filter["contactado"] = df_in_filter["contesto?"].fillna("").astype(str).str.strip().ne("")
df_in_filter["con_hipoteca"] = (
    df_in_filter["tiene hipoteca?"].fillna("").astype(str).str.strip().str.lower() == "si"
)


def _status(row) -> str:
    aplica = row.get("aplica", "pending")
    if aplica == "error":
        return "error"
    if aplica == "pending":
        return "pendiente_score"
    if aplica == "no" or row["con_hipoteca"]:
        return "no_aplica"
    return "aplica_contactado" if row["contactado"] else "aplica_pendiente_llamar"


df_in_filter["status"] = df_in_filter.apply(_status, axis=1)

STATUS_COLORS = {
    "aplica_contactado":       GREEN_DARK,
    "aplica_pendiente_llamar": GREEN_LIGHT,
    "pendiente_score":         YELLOW,
    "no_aplica":               GREY,
    "error":                   RED,
}
STATUS_LABELS = {
    "aplica_contactado":       "Aplica + contactado",
    "aplica_pendiente_llamar": "Aplica + LLAMAR",
    "pendiente_score":         "Pendiente de score",
    "no_aplica":               "No aplica",
    "error":                   "Error",
}


# ─────────────────────────────────────────────────────────────────────────────
# KPIs
# ─────────────────────────────────────────────────────────────────────────────
n_universe = len(df_hs_f) if not df_hs_f.empty else 0
n_leads = len(df_in_filter)
n_contactados = int(df_in_filter["contactado"].sum())
n_interes = int((df_in_filter["contesto?"].astype(str).str.lower() == "si").sum())
n_aplica = int(df_in_filter["status"].isin(["aplica_contactado", "aplica_pendiente_llamar"]).sum())
n_call_list = int((df_in_filter["status"] == "aplica_pendiente_llamar").sum())

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.markdown(kpi_card("Universo HS", f"{n_universe:,}", "fakedoor + ≥20 abr"), unsafe_allow_html=True)
c2.markdown(kpi_card("Leads T&C", n_leads, "firmaron formulario"), unsafe_allow_html=True)
c3.markdown(kpi_card("Contactados", n_contactados, f"{n_contactados/max(1,n_leads):.0%}"), unsafe_allow_html=True)
c4.markdown(kpi_card("Interés activo", n_interes, f"{n_interes/max(1,n_leads):.0%}"), unsafe_allow_html=True)
c5.markdown(kpi_card("Elegibles", n_aplica, "≥720 + sin hipoteca"), unsafe_allow_html=True)
c6.markdown(kpi_card("Por llamar", n_call_list, "call list activa"), unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Persistencia de scores
# ─────────────────────────────────────────────────────────────────────────────
with st.expander("Estado de consulta de scores", expanded=False):
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("API configurado", "Sí" if score_stats.get("api_configured") else "No")
    c2.metric("Cache (Sheet)", score_stats.get("cached", 0))
    c3.metric("Consultados ahora", score_stats.get("consulted", 0))
    c4.metric("Pendientes", score_stats.get("pending", 0))
    c5.metric("Escritos al Sheet", score_stats.get("written", 0))
    if score_stats.get("write_error"):
        st.error(f"Write fail: {score_stats['write_error']}")
    if not score_stats.get("api_configured"):
        st.info(
            "Define `SCORE_API_URL` y `SCORE_API_TOKEN` en Secrets para consultar "
            "scores nuevos. Los 26 históricos ya están cargados desde quienesaplican.xlsx."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline de leads
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Pipeline de leads</h2>", unsafe_allow_html=True)

legend_html = "<div style='font-size:0.78rem;color:#444;margin-bottom:8px'>"
for k, c in STATUS_COLORS.items():
    legend_html += (
        f"<span class='legend-box' style='background:{c}'></span>"
        f"<span style='margin-right:14px'>{STATUS_LABELS[k]}</span>"
    )
legend_html += "</div>"
st.markdown(legend_html, unsafe_allow_html=True)

DISPLAY_COLS = ["nombre_completo", "telefono", "cedula", "grupo",
                "contesto?", "tiene hipoteca?", "score", "nivel_riesgo", "aplica"]
disp = df_in_filter[[c for c in DISPLAY_COLS if c in df_in_filter.columns] + ["status"]].copy()
disp_sorted = disp.sort_values(
    by="status",
    key=lambda s: s.map({
        "aplica_pendiente_llamar": 0,
        "pendiente_score":         1,
        "aplica_contactado":       2,
        "no_aplica":               3,
        "error":                   4,
    }).fillna(99),
).reset_index(drop=True)


def _row_color(row_idx):
    s = disp_sorted.loc[row_idx, "status"]
    color = STATUS_COLORS.get(s, "")
    text = "white" if s in ("aplica_contactado", "error") else "#222"
    return [f"background-color:{color};color:{text}" for _ in range(len(disp_sorted.columns) - 1)]


styled = (
    disp_sorted.drop(columns=["status"])
    .rename(columns={
        "nombre_completo": "Nombre", "telefono": "Teléfono", "cedula": "Cédula",
        "grupo": "Grupo", "contesto?": "Contesto?", "tiene hipoteca?": "Hipoteca?",
        "score": "Score", "nivel_riesgo": "Nivel", "aplica": "Aplica",
    })
    .style.apply(lambda r: _row_color(r.name), axis=1)
)
st.dataframe(styled, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Funnel LIVE
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Embudo del experimento (en vivo)</h2>", unsafe_allow_html=True)

# Cálculos por etapa
landing_uuids = set(df_bq["uuid"].dropna().astype(str)) if not df_bq.empty else set()
consent_uuids = set(df_bq[df_bq["reached_consent"] == 1]["uuid"].dropna().astype(str)) if not df_bq.empty else set()

# N0: universo HubSpot fakedoor (filtrado)
n_n0 = n_universe
# N1: con nombre del conjunto
if not df_hs_f.empty and "nombre_del_conjunto" in df_hs_f.columns:
    n_n1 = int(df_hs_f["nombre_del_conjunto"].fillna("").astype(str).str.strip().ne("").sum())
else:
    n_n1 = 0
# N2: enviados WA = intersección con Infobip (matched por phone normalizado)
if not df_hs_f.empty and "phone" in df_hs_f.columns and infobip_phones:
    deal_phones = df_hs_f["phone"].dropna().apply(_norm_phone)
    n_n2 = int(deal_phones.isin(infobip_phones).sum())
else:
    n_n2 = 0
# N3: abrieron link (BQ) intersect con HubSpot
if allowed_uuids and landing_uuids:
    n_n3 = len(allowed_uuids & landing_uuids)
elif landing_uuids:
    n_n3 = len(landing_uuids)
else:
    n_n3 = 0
# N4: T&C firmados (Leads ∩ HubSpot)
n_n4 = n_leads
# N5..N8: del pipeline ya filtrado
n_n5 = n_contactados
n_n6 = n_interes
n_n7 = n_aplica
n_n8 = n_aplica - int(df_in_filter["con_hipoteca"].sum())

stages = [
    ("Leads elegibles (HS fakedoor)",     n_n0, "HubSpot"),
    ("Con nombre del conjunto",           n_n1, "HubSpot"),
    ("Enviados WA (Infobip)",             n_n2, "Sheets/Infobip"),
    ("Abrieron link (landing)",           n_n3, "BigQuery"),
    ("T&C firmados",                       n_n4, "Sheets/Leads"),
    ("Contactados",                        n_n5, "Sheets/Leads"),
    ("Interés activo",                     n_n6, "Sheets/Leads"),
    ("Aprobados riesgo (Aplica=sí)",       n_n7, "Sheet+API"),
    ("Elegibles (sin hipoteca)",           n_n8, "Sheet+Entrevista"),
]

f_labels = [s[0] for s in stages]
f_vals = [s[1] for s in stages]
f_sources = [s[2] for s in stages]
f_colors = [DEEP, PRIMARY, MED, ACCENT, LIGHT, "#9ecae1", GREEN_DARK, "#1a7a50", "#0f5535"][:len(stages)]
f_text = [
    f"{v:,}  ({v/f_vals[i-1]*100:.0f}%)" if i > 0 and f_vals[i-1] > 0 else f"{v:,}"
    for i, v in enumerate(f_vals)
]

fig = go.Figure(go.Bar(
    x=f_vals, y=f_labels, orientation="h",
    marker_color=f_colors, text=f_text,
    textposition="outside", textfont=dict(size=11, color=DEEP),
    customdata=f_sources,
    hovertemplate="<b>%{y}</b><br>%{x:,} · fuente: %{customdata}<extra></extra>",
))
fig.update_layout(
    paper_bgcolor=WHITE, plot_bgcolor=WHITE,
    font=dict(family="Inter, sans-serif", color=DEEP, size=11),
    height=440, margin=dict(l=10, r=230, t=10, b=10),
    xaxis=dict(type="log" if max(f_vals) > 100 and min(f_vals) > 0 else "linear",
               title="Clientes" + (" (log)" if max(f_vals) > 100 else ""),
               gridcolor="#ede8f5", tickformat=",d"),
    yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# A/B
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>A/B · AH (84m) vs BH (120m)</h2>", unsafe_allow_html=True)
if "grupo" in df_in_filter.columns:
    ab = (
        df_in_filter.groupby("grupo")
        .agg(
            firmaron=("cedula", "count"),
            contactados=("contactado", "sum"),
            interes=("contesto?", lambda s: (s.astype(str).str.lower() == "si").sum()),
            aplican=("status", lambda s: s.isin(["aplica_contactado","aplica_pendiente_llamar"]).sum()),
        )
        .reset_index()
    )
    st.dataframe(ab, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Entrevistas
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Entrevistas cualitativas</h2>", unsafe_allow_html=True)
if df_int.empty:
    st.info("La pestaña Entrevista está vacía.")
else:
    cols_int = [c for c in ["telefono", "tiene hipoteca?", "P1", "P4", "P5", "P9"] if c in df_int.columns]
    st.dataframe(df_int[cols_int], hide_index=True, use_container_width=True)
    st.caption(f"{len(df_int)} entrevistas")


# ─────────────────────────────────────────────────────────────────────────────
# HubSpot raw
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(f"<h2>HubSpot · deals con flag fakedoor ({len(df_hs_f):,} con filtros)</h2>", unsafe_allow_html=True)
if df_hs.empty:
    st.info("HubSpot no disponible o sin resultados.")
else:
    df_hs_display = df_hs_f.rename(columns=hs_src.FAKEDOOR_PROPS)
    st.dataframe(df_hs_display, hide_index=True, use_container_width=True)

    with st.expander("Debug · propiedades vacías", expanded=False):
        null_pct = (df_hs.isna().mean() * 100).round(1).sort_values(ascending=False)
        empty = null_pct[null_pct > 0]
        if empty.empty:
            st.caption("Todas las propiedades trajeron datos.")
        else:
            st.write("% NaN por propiedad (100% = el internal name probablemente está mal):")
            st.dataframe(empty.to_frame("% NaN"), use_container_width=True)


st.divider()
st.caption(
    f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    "TTL cache: 120s (Sheets, HS) · 300s (BQ). "
    "Scores ya consultados quedan persistidos en columnas `Aplica` y `Metadata` del Sheet."
)
