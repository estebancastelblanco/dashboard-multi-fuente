"""FakeDoor Habicapital — dashboard live (HubSpot + BQ + Sheets)."""
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
# Enriquecer HubSpot con fuente
# ─────────────────────────────────────────────────────────────────────────────
if not df_hs.empty:
    df_hs["fuente"] = df_hs.apply(hs_src.compute_fuente, axis=1)
    df_hs["phone_norm"] = df_hs.get("phone", pd.Series(dtype=str)).apply(_norm_phone)
else:
    df_hs["fuente"] = pd.Series(dtype=str)


# ─────────────────────────────────────────────────────────────────────────────
# Filtros (sidebar)
# ─────────────────────────────────────────────────────────────────────────────
def _unique(series: pd.Series) -> list[str]:
    return sorted([s for s in series.dropna().astype(str).unique() if s])


variantes_all = _unique(df_hs.get("ab_test_landing", pd.Series(dtype=str)))
fuentes_all = [f for f in hs_src.FUENTES if f in set(df_hs.get("fuente", pd.Series()).dropna().unique())]
estados_all = _unique(df_hs.get("estado", pd.Series(dtype=str)))
oport_all = _unique(df_hs.get("oportunidad_del_negocio", pd.Series(dtype=str)))

with st.sidebar:
    st.markdown(f"<div style='color:{LIGHT};font-weight:700;font-size:0.9rem;margin-bottom:14px'>Filtros</div>", unsafe_allow_html=True)

    st.markdown("### Variante")
    sel_variantes = st.multiselect(
        "variantes", variantes_all, default=variantes_all,
        label_visibility="collapsed", help="ab_test_landing en HubSpot"
    )

    st.markdown("### Fuente")
    sel_fuentes = st.multiselect(
        "fuentes", hs_src.FUENTES, default=hs_src.FUENTES,
        label_visibility="collapsed",
        help="Top y MM+Inmo desde flag_fakedoor; Rechazos Remo/Comite desde comite_remodelaciones y oportunidad",
    )

    st.markdown("### Oportunidad del Negocio")
    sel_oport = st.multiselect("oportunidades", oport_all, default=oport_all, label_visibility="collapsed")

    st.markdown("### Estado del Negocio")
    sel_estados = st.multiselect("estados", estados_all, default=estados_all, label_visibility="collapsed")

    st.markdown("---")
    st.caption("Los filtros aplican sobre la base HubSpot y propagan al funnel, distribuciones, pipeline y tabla de deals.")


# Aplicar filtros sobre df_hs
df_hs_f = df_hs.copy()
if not df_hs_f.empty:
    if len(sel_variantes) < len(variantes_all):
        df_hs_f = df_hs_f[df_hs_f["ab_test_landing"].isin(sel_variantes) | df_hs_f["ab_test_landing"].isna()]
    if len(sel_fuentes) < len(hs_src.FUENTES):
        df_hs_f = df_hs_f[df_hs_f["fuente"].isin(sel_fuentes)]
    if len(sel_oport) < len(oport_all):
        df_hs_f = df_hs_f[df_hs_f["oportunidad_del_negocio"].isin(sel_oport) | df_hs_f["oportunidad_del_negocio"].isna()]
    if len(sel_estados) < len(estados_all):
        df_hs_f = df_hs_f[df_hs_f["estado"].isin(sel_estados) | df_hs_f["estado"].isna()]

allowed_uuids: set[str] = set(df_hs_f["deal_uuid"].dropna().astype(str)) if not df_hs_f.empty else set()


# Cruces Leads ↔ Entrevista ↔ HubSpot
df = df_leads.copy()
df["phone_norm"] = df["telefono"].apply(_norm_phone)
df["uuid_str"] = df["uuid"].astype(str)
if not df_int.empty:
    df = df.merge(df_int[["phone_norm", "tiene hipoteca?"]], on="phone_norm", how="left")
else:
    df["tiene hipoteca?"] = None

# Trae fuente + variante del HubSpot
if not df_hs_f.empty and "deal_uuid" in df_hs_f.columns:
    df = df.merge(
        df_hs_f[["deal_uuid", "fuente", "ab_test_landing"]].rename(
            columns={"deal_uuid": "uuid_str", "ab_test_landing": "variante_hs"}
        ),
        on="uuid_str", how="left",
    )
else:
    df["fuente"] = None
    df["variante_hs"] = None

# Filtra leads a los uuids permitidos por el filtro HubSpot
if allowed_uuids:
    df_in = df[df["uuid_str"].isin(allowed_uuids)].copy()
else:
    df_in = df.copy()

df_in["contactado"] = df_in["contesto?"].fillna("").astype(str).str.strip().ne("")
df_in["con_hipoteca"] = (
    df_in["tiene hipoteca?"].fillna("").astype(str).str.strip().str.lower() == "si"
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


df_in["status"] = df_in.apply(_status, axis=1)

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
n_leads = len(df_in)
n_contactados = int(df_in["contactado"].sum())
n_interes = int((df_in["contesto?"].astype(str).str.lower() == "si").sum())
n_aplica = int(df_in["status"].isin(["aplica_contactado", "aplica_pendiente_llamar"]).sum())
n_call_list = int((df_in["status"] == "aplica_pendiente_llamar").sum())

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.markdown(kpi_card("Universo HS", f"{n_universe:,}", "fakedoor + ≥20 abr"), unsafe_allow_html=True)
c2.markdown(kpi_card("Leads T&C", n_leads, "firmaron formulario"), unsafe_allow_html=True)
c3.markdown(kpi_card("Contactados", n_contactados, f"{n_contactados/max(1,n_leads):.0%}"), unsafe_allow_html=True)
c4.markdown(kpi_card("Interés activo", n_interes, f"{n_interes/max(1,n_leads):.0%}"), unsafe_allow_html=True)
c5.markdown(kpi_card("Elegibles", n_aplica, "≥720 + sin hipoteca"), unsafe_allow_html=True)
c6.markdown(kpi_card("Por llamar", n_call_list, "call list activa"), unsafe_allow_html=True)


# Persistence panel
with st.expander("Estado de consulta de scores", expanded=False):
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("API configurado", "Sí" if score_stats.get("api_configured") else "No")
    c2.metric("Cache (Sheet)", score_stats.get("cached", 0))
    c3.metric("Consultados ahora", score_stats.get("consulted", 0))
    c4.metric("Pendientes", score_stats.get("pending", 0))
    c5.metric("Escritos al Sheet", score_stats.get("written", 0))
    if score_stats.get("write_error"):
        st.error(f"Write fail: {score_stats['write_error']}")


# ─────────────────────────────────────────────────────────────────────────────
# Funnel LIVE
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Embudo del experimento</h2>", unsafe_allow_html=True)

# BQ desglosado
pages_uuids = set(df_bq[df_bq["had_pages"] == 1]["uuid"].dropna().astype(str)) if not df_bq.empty and "had_pages" in df_bq.columns else set()
tracks_uuids = set(df_bq[df_bq["had_tracks"] == 1]["uuid"].dropna().astype(str)) if not df_bq.empty and "had_tracks" in df_bq.columns else set()
# Fallback si la query antigua aun cachea sin las columnas nuevas
if not pages_uuids and not df_bq.empty and "uuid" in df_bq.columns:
    pages_uuids = set(df_bq["uuid"].dropna().astype(str))

# Etapa 1: Leads elegibles = universo HS filtrado
n_e1 = n_universe
# Etapa 2: Enviados WA = los que tienen nombre del conjunto en HubSpot
if not df_hs_f.empty and "nombre_del_conjunto" in df_hs_f.columns:
    n_e2 = int(df_hs_f["nombre_del_conjunto"].fillna("").astype(str).str.strip().ne("").sum())
else:
    n_e2 = 0
# Etapa 3: Entregados WA = 77% historico de Enviados (no hay API Infobip live)
delivery_ratio = EXPERIMENT.funnel_baseline.get("wa_delivery_ratio", 0.77)
n_e3 = int(round(n_e2 * delivery_ratio))
# Etapa 4: Abrieron WA = uuids con page-view en BQ ∩ HS allowed
if allowed_uuids and pages_uuids:
    n_e4 = len(allowed_uuids & pages_uuids)
else:
    n_e4 = len(pages_uuids) if not allowed_uuids else 0
# Etapa 5: Clicks landing = uuids con track-event en BQ ∩ HS allowed
if allowed_uuids and tracks_uuids:
    n_e5 = len(allowed_uuids & tracks_uuids)
else:
    n_e5 = len(tracks_uuids) if not allowed_uuids else 0
# Etapa 6: T&C firmados
n_e6 = n_leads
# Etapa 7: Interes activo
n_e7 = n_interes
# Etapa 8: Aprobados riesgo
n_e8 = n_aplica
# Etapa 9: Elegibles (sin hipoteca)
n_e9 = n_aplica - int(df_in["con_hipoteca"].sum())

stages = [
    ("Leads elegibles",      n_e1, "HubSpot · flag_fakedoor + ≥20 abr"),
    ("Enviados WA",          n_e2, "HubSpot · con nombre del conjunto"),
    (f"Entregados WA",       n_e3, f"Estimado · {int(delivery_ratio*100)}% × Enviados"),
    ("Abrieron WA",          n_e4, "BigQuery · pages ∩ HS"),
    ("Clicks landing",       n_e5, "BigQuery · tracks ∩ HS"),
    ("T&C firmados",         n_e6, "Sheets/Leads ∩ HS"),
    ("Interés activo",       n_e7, "Sheets/Leads · contesto?=si"),
    ("Aprobados riesgo",     n_e8, "Sheet+API · Aplica=si"),
    ("Elegibles",            n_e9, "Aprobados sin hipoteca"),
]
f_labels = [s[0] for s in stages]
f_vals = [s[1] for s in stages]
f_sources = [s[2] for s in stages]
f_colors = [DEEP, PRIMARY, MED, ACCENT, LIGHT, "#9ecae1", GREEN_DARK, "#1a7a50", "#0f5535"][:len(stages)]
f_text = [
    f"{v:,}  ({v/f_vals[i-1]*100:.0f}%)" if i > 0 and f_vals[i-1] > 0 else f"{v:,}"
    for i, v in enumerate(f_vals)
]
use_log = (max(f_vals) if f_vals else 0) > 100 and (min([v for v in f_vals if v > 0], default=0) > 0)
fig_funnel = go.Figure(go.Bar(
    x=f_vals, y=f_labels, orientation="h",
    marker_color=f_colors, text=f_text,
    textposition="outside", textfont=dict(size=11, color=DEEP),
    customdata=f_sources,
    hovertemplate="<b>%{y}</b><br>%{x:,} · fuente: %{customdata}<extra></extra>",
))
fig_funnel.update_layout(
    paper_bgcolor=WHITE, plot_bgcolor=WHITE,
    font=dict(family="Inter, sans-serif", color=DEEP, size=11),
    height=440, margin=dict(l=10, r=230, t=10, b=10),
    xaxis=dict(type="log" if use_log else "linear",
               title="Clientes" + (" (log)" if use_log else ""),
               gridcolor="#ede8f5", tickformat=",d"),
    yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig_funnel, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Distribuciones
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Distribución del universo (con filtros aplicados)</h2>", unsafe_allow_html=True)


def _pie(values: pd.Series, title: str, palette: list[str]):
    counts = values.fillna("(sin valor)").astype(str).replace("", "(sin valor)").value_counts()
    fig = go.Figure(go.Pie(
        labels=counts.index, values=counts.values,
        hole=0.42, marker_colors=palette,
        textinfo="label+percent+value", textfont_size=10,
    ))
    fig.update_layout(
        paper_bgcolor=WHITE, plot_bgcolor=WHITE, showlegend=False,
        font=dict(family="Inter, sans-serif", color=DEEP, size=11),
        title=dict(text=title, font=dict(size=12, color=DEEP)),
        height=260, margin=dict(l=5, r=5, t=36, b=5),
    )
    return fig


if df_hs_f.empty:
    st.info("No hay deals con los filtros actuales.")
else:
    col_a, col_b = st.columns(2)
    with col_a:
        st.plotly_chart(_pie(df_hs_f["fuente"], "Fuente", [DEEP, PRIMARY, MED, ACCENT]), use_container_width=True)
        st.plotly_chart(_pie(df_hs_f["ab_test_landing"], "Variante A/B", [PRIMARY, ACCENT, PALE, LIGHT]), use_container_width=True)
    with col_b:
        if "oportunidad_del_negocio" in df_hs_f.columns:
            # Barras horizontales en lugar de pie (suele haber muchas categorías)
            op_c = df_hs_f["oportunidad_del_negocio"].fillna("(sin valor)").value_counts().reset_index()
            op_c.columns = ["Oportunidad", "N"]
            op_c = op_c.sort_values("N", ascending=True).tail(10)
            fig_op = go.Figure(go.Bar(
                x=op_c["N"], y=op_c["Oportunidad"], orientation="h",
                marker=dict(color=op_c["N"], colorscale=[[0, PALE], [1, PRIMARY]], showscale=False),
                text=op_c["N"], textposition="outside", textfont_size=10,
            ))
            fig_op.update_layout(
                paper_bgcolor=WHITE, plot_bgcolor=WHITE,
                font=dict(family="Inter, sans-serif", color=DEEP, size=11),
                title=dict(text="Oportunidad del negocio (top 10)", font=dict(size=12, color=DEEP)),
                height=260, margin=dict(l=5, r=40, t=36, b=5),
                yaxis=dict(gridcolor="#ede8f5"), xaxis=dict(gridcolor="#ede8f5"),
            )
            st.plotly_chart(fig_op, use_container_width=True)
        if "estado" in df_hs_f.columns:
            es_c = df_hs_f["estado"].fillna("(sin valor)").value_counts().reset_index()
            es_c.columns = ["Estado", "N"]
            es_c = es_c.sort_values("N", ascending=True).tail(10)
            fig_es = go.Figure(go.Bar(
                x=es_c["N"], y=es_c["Estado"], orientation="h",
                marker=dict(color=es_c["N"], colorscale=[[0, PALE], [1, MED]], showscale=False),
                text=es_c["N"], textposition="outside", textfont_size=10,
            ))
            fig_es.update_layout(
                paper_bgcolor=WHITE, plot_bgcolor=WHITE,
                font=dict(family="Inter, sans-serif", color=DEEP, size=11),
                title=dict(text="Estado del negocio (top 10)", font=dict(size=12, color=DEEP)),
                height=260, margin=dict(l=5, r=40, t=36, b=5),
                yaxis=dict(gridcolor="#ede8f5"), xaxis=dict(gridcolor="#ede8f5"),
            )
            st.plotly_chart(fig_es, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline de leads con metadata
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Pipeline de leads (con metadata del API)</h2>", unsafe_allow_html=True)

legend_html = "<div style='font-size:0.78rem;color:#444;margin-bottom:8px'>"
for k, c in STATUS_COLORS.items():
    legend_html += (
        f"<span class='legend-box' style='background:{c}'></span>"
        f"<span style='margin-right:14px'>{STATUS_LABELS[k]}</span>"
    )
legend_html += "</div>"
st.markdown(legend_html, unsafe_allow_html=True)

DISPLAY_COLS = [
    "nombre_completo", "telefono", "cedula", "grupo", "fuente",
    "contesto?", "tiene hipoteca?",
    "score", "nivel_riesgo", "aplica",
    "cuota_maxima", "ingresos_mensuales", "razon",
]
disp = df_in[[c for c in DISPLAY_COLS if c in df_in.columns] + ["status"]].copy()
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
        "grupo": "Grupo", "fuente": "Fuente", "contesto?": "Contesto?",
        "tiene hipoteca?": "Hipoteca?", "score": "Score", "nivel_riesgo": "Nivel",
        "aplica": "Aplica", "cuota_maxima": "Cuota Máxima",
        "ingresos_mensuales": "Ingresos", "razon": "Razón",
    })
    .style.apply(lambda r: _row_color(r.name), axis=1)
)
st.dataframe(styled, hide_index=True, use_container_width=True)
st.caption(f"{len(disp_sorted)} leads · ordenados por prioridad de llamada")


# ─────────────────────────────────────────────────────────────────────────────
# Tabla raw de HubSpot deals (filtrados)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(f"<h2>HubSpot · deals filtrados ({len(df_hs_f):,})</h2>", unsafe_allow_html=True)
if df_hs_f.empty:
    st.info("Sin deals con los filtros aplicados.")
else:
    show_cols = [
        "dealname", "phone", "createdate", "estado",
        "fuente", "flag_fakedoor", "ab_test_landing",
        "oportunidad_del_negocio", "nombre_del_conjunto",
        "comite_remodelaciones", "deal_uuid",
    ]
    show_cols = [c for c in show_cols if c in df_hs_f.columns]
    df_hs_display = df_hs_f[show_cols].rename(columns={**hs_src.FAKEDOOR_PROPS, "fuente": "Fuente"})
    st.dataframe(df_hs_display, hide_index=True, use_container_width=True)

    with st.expander("Debug · propiedades vacías"):
        null_pct = (df_hs.isna().mean() * 100).round(1).sort_values(ascending=False)
        empty = null_pct[null_pct > 0]
        if empty.empty:
            st.caption("Todas las propiedades trajeron datos.")
        else:
            st.write("% NaN por propiedad (100% = internal name probablemente está mal):")
            st.dataframe(empty.to_frame("% NaN"), use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Insights de entrevistas (en lugar de la tabla)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Insights · entrevistas cualitativas</h2>", unsafe_allow_html=True)

if df_int.empty:
    st.info("La pestaña Entrevista está vacía.")
else:
    # Filtra entrevistas a las que pertenecen a los leads filtrados
    if allowed_uuids:
        leads_phones = set(df_in["phone_norm"].dropna().astype(str))
        df_int_f = df_int[df_int["phone_norm"].isin(leads_phones)].copy()
    else:
        df_int_f = df_int.copy()

    if df_int_f.empty:
        st.info("Ninguna entrevista corresponde a los leads filtrados.")
    else:
        col_pie, col_quote = st.columns([1, 2])
        with col_pie:
            hip_vals = df_int_f["tiene hipoteca?"].fillna("(sin dato)").astype(str).str.strip().str.lower()
            hip_vals = hip_vals.replace({"si": "Sí", "sí": "Sí", "no": "No", "": "(sin dato)"})
            st.plotly_chart(_pie(hip_vals, "¿Tiene hipoteca?", [GREEN_DARK, ACCENT, "#bbb"]),
                            use_container_width=True)
            st.caption(f"{len(df_int_f)} entrevistas")

        with col_quote:
            def _bullets(col_name: str, label: str):
                if col_name not in df_int_f.columns:
                    return
                vals = df_int_f[col_name].dropna().astype(str).str.strip()
                vals = [v for v in vals if v and v.lower() not in ("nan", "")]
                if not vals:
                    return
                with st.expander(f"{label} ({len(vals)} respuestas)", expanded=False):
                    for v in vals:
                        st.markdown(f"- {v}")

            _bullets("P1", "P1 · ¿Qué está pasando hoy? (trigger)")
            _bullets("P5", "P5 · Plazo preferido")
            _bullets("P8", "P8 · Objeciones / fricción")
            _bullets("P9", "P9 · Urgencia")


# ─────────────────────────────────────────────────────────────────────────────
# A/B summary
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>A/B · AH (84m) vs BH (120m)</h2>", unsafe_allow_html=True)
if "grupo" in df_in.columns:
    ab = (
        df_in.groupby("grupo")
        .agg(
            firmaron=("cedula", "count"),
            contactados=("contactado", "sum"),
            interes=("contesto?", lambda s: (s.astype(str).str.lower() == "si").sum()),
            aplican=("status", lambda s: s.isin(["aplica_contactado", "aplica_pendiente_llamar"]).sum()),
        )
        .reset_index()
    )
    st.dataframe(ab, hide_index=True, use_container_width=True)


st.divider()
st.caption(
    f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    "TTL cache: 120s (Sheets, HS) · 300s (BQ). Scores persisten en Aplica + Metadata del Sheet."
)
