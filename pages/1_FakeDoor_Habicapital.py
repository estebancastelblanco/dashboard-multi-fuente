"""FakeDoor Habicapital — dashboard live con persistencia de scores."""
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


# ─────────────────────────────────────────────────────────────────────────────
# Loaders
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=120, show_spinner="Cargando Leads…")
def load_leads_raw() -> pd.DataFrame:
    return gs_src.fetch_tab("Leads")


@st.cache_data(ttl=120, show_spinner="Cargando Entrevistas…")
def load_entrevistas() -> pd.DataFrame:
    df = gs_src.fetch_tab("Entrevista")
    if not df.empty and "telefono" in df.columns:
        df["telefono"] = df["telefono"].astype(str).str.strip()
    return df


@st.cache_data(ttl=120, show_spinner="Contando envios WA (Infobip)…")
def load_infobip_count() -> int:
    """Cuenta teléfonos no vacíos en la pestaña Infobip."""
    try:
        # Lee la columna 5 directamente (donde estaban los teléfonos según la inspección)
        df = gs_src.fetch_tab("Infobip")
        # Si get_all_records dejó columnas '', cuenta valores no-vacíos en todas las cols
        if df.empty:
            return 0
        all_vals = pd.concat([df[c].astype(str) for c in df.columns], ignore_index=True)
        return int(all_vals.str.strip().ne("").sum())
    except Exception:
        return 0


@st.cache_data(ttl=300, show_spinner="Consultando HubSpot…")
def load_hs_deals() -> pd.DataFrame:
    return hs_src.fetch_fakedoor_deals(since_iso=EXPERIMENT.start_date)


@st.cache_data(ttl=120, show_spinner="Consultando scores (con persistencia)…")
def load_leads_with_scores() -> tuple[pd.DataFrame, dict]:
    df_raw = load_leads_raw()
    if df_raw.empty:
        return df_raw, {}
    return score_src.enrich_leads_with_scores(df_raw, tab="Leads", cedula_col="cedula")


def _norm_phone(s: object) -> str:
    s = str(s).strip().replace(" ", "").lstrip("+")
    if s.startswith("57") and len(s) > 10:
        s = s[2:]
    return s[-10:] if len(s) >= 10 else s


# ─────────────────────────────────────────────────────────────────────────────
# Carga
# ─────────────────────────────────────────────────────────────────────────────
try:
    df, score_stats = load_leads_with_scores()
except Exception as exc:
    st.error(f"Error cargando Leads + scores: {type(exc).__name__}: {exc}")
    st.stop()

if df.empty:
    st.warning("La pestaña Leads está vacía.")
    st.stop()

try:
    df_int = load_entrevistas()
except Exception:
    df_int = pd.DataFrame()

# Cruces
df["phone_norm"] = df["telefono"].apply(_norm_phone)
if not df_int.empty:
    df_int["phone_norm"] = df_int["telefono"].apply(_norm_phone)
    df = df.merge(df_int[["phone_norm", "tiene hipoteca?"]], on="phone_norm", how="left")
else:
    df["tiene hipoteca?"] = None

df["contactado"] = df["contesto?"].fillna("").astype(str).str.strip().ne("")
df["con_hipoteca"] = (
    df["tiene hipoteca?"].fillna("").astype(str).str.strip().str.lower() == "si"
)


def _status(row) -> str:
    aplica = row["aplica"]
    if aplica == "error":
        return "error"
    if aplica == "pending":
        return "pendiente_score"
    if aplica == "no" or row["con_hipoteca"]:
        return "no_aplica"
    return "aplica_contactado" if row["contactado"] else "aplica_pendiente_llamar"


df["status"] = df.apply(_status, axis=1)

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
# Estado de persistencia
# ─────────────────────────────────────────────────────────────────────────────
with st.expander("Estado de consulta de scores", expanded=False):
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("API configurado", "Sí" if score_stats.get("api_configured") else "No")
    c2.metric("Desde cache (Sheet)", score_stats.get("cached", 0))
    c3.metric("Consultados ahora", score_stats.get("consulted", 0))
    c4.metric("Pendientes (sin API)", score_stats.get("pending", 0))
    c5.metric("Escritos al Sheet", score_stats.get("written", 0))
    if score_stats.get("write_error"):
        st.error(
            f"No pude escribir al Sheet: {score_stats['write_error']}\n\n"
            "→ Da permisos de **Editor** al service account "
            "`ctl-reader-service@try12-455405.iam.gserviceaccount.com` en el share del Sheet."
        )
    if not score_stats.get("api_configured"):
        st.info(
            "Configura `SCORE_API_URL` y `SCORE_API_TOKEN` en Streamlit Secrets para "
            "consultar el score en vivo. Mientras tanto, los leads sin valor en la "
            "columna **Aplica** del Sheet aparecen como pendientes."
        )


# ─────────────────────────────────────────────────────────────────────────────
# KPIs
# ─────────────────────────────────────────────────────────────────────────────
n_leads = len(df)
n_contactados = int(df["contactado"].sum())
n_interes = int((df["contesto?"].astype(str).str.lower() == "si").sum())
n_aplica = int(df["status"].isin(["aplica_contactado", "aplica_pendiente_llamar"]).sum())
n_call_list = int((df["status"] == "aplica_pendiente_llamar").sum())

c1, c2, c3, c4, c5 = st.columns(5)
c1.markdown(kpi_card("Leads T&C", n_leads, "Sheet Leads"), unsafe_allow_html=True)
c2.markdown(kpi_card("Contactados", n_contactados, f"{n_contactados/max(1,n_leads):.0%}"), unsafe_allow_html=True)
c3.markdown(kpi_card("Interés activo", n_interes, f"{n_interes/max(1,n_leads):.0%}"), unsafe_allow_html=True)
c4.markdown(kpi_card("Elegibles", n_aplica, "score≥720 + sin hipoteca"), unsafe_allow_html=True)
c5.markdown(kpi_card("Por llamar", n_call_list, "aplican y no contactados"), unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline de leads (vista clave)
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
disp = df[[c for c in DISPLAY_COLS if c in df.columns] + ["status"]].copy()
disp_sorted = disp.sort_values(
    by="status",
    key=lambda s: s.map({
        "aplica_pendiente_llamar": 0,
        "pendiente_score": 1,
        "aplica_contactado": 2,
        "no_aplica": 3,
        "error": 4,
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
# Funnel completo (replica del dashboard original)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Embudo del experimento</h2>", unsafe_allow_html=True)

baseline = EXPERIMENT.funnel_baseline
# stages: (label, value, is_live)
stages: list[tuple[str, int, bool]] = []
stages.append(("Leads elegibles", baseline.get("Leads elegibles", 0), False))

# Live count for enviados via Infobip (if available)
infobip_n = load_infobip_count()
stages.append(("Enviados WA", infobip_n if infobip_n else baseline.get("Enviados WA", 0), bool(infobip_n)))
stages.append(("Entregados WA", baseline.get("Entregados WA", 0), False))
stages.append(("Abrieron link", baseline.get("Abrieron link", 0), False))
stages.append(("T&C firmados", n_leads, True))
stages.append(("Contactados", n_contactados, True))
stages.append(("Interés activo", n_interes, True))
stages.append(("Aprobados riesgo (aplica)", n_aplica, True))
stages.append(("Elegibles (sin hipoteca)", n_aplica - int(df["con_hipoteca"].sum()), True))

f_labels = [f"{lbl}{' *' if not live else ''}" for lbl, _, live in stages]
f_vals = [v for _, v, _ in stages]
f_colors = [DEEP, PRIMARY, MED, ACCENT, LIGHT, "#9ecae1", GREEN_DARK, "#1a7a50", "#0f5535"][:len(stages)]
f_text = [
    f"{v:,}  ({v/f_vals[i-1]*100:.0f}%)" if i > 0 and f_vals[i-1] > 0 else f"{v:,}"
    for i, v in enumerate(f_vals)
]

fig_funnel = go.Figure(go.Bar(
    x=f_vals, y=f_labels, orientation="h",
    marker_color=f_colors, text=f_text,
    textposition="outside", textfont=dict(size=11, color=DEEP),
))
fig_funnel.update_layout(
    paper_bgcolor=WHITE, plot_bgcolor=WHITE,
    font=dict(family="Inter, sans-serif", color=DEEP, size=11),
    height=420, margin=dict(l=10, r=220, t=10, b=10),
    xaxis=dict(type="log", title="Clientes (escala log)", gridcolor="#ede8f5", tickformat=",d"),
    yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig_funnel, use_container_width=True)
st.caption("\\* etapas con baseline histórico — todavía no hay fuente live conectada (Infobip delivery/open, BQ landing tracking).")


# ─────────────────────────────────────────────────────────────────────────────
# A/B
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>A/B · AH (84m) vs BH (120m)</h2>", unsafe_allow_html=True)
if "grupo" in df.columns:
    ab = (
        df.groupby("grupo")
        .agg(
            firmaron=("cedula", "count"),
            contactados=("contactado", "sum"),
            interes=("contesto?", lambda s: (s.astype(str).str.lower() == "si").sum()),
            aplican=("status", lambda s: s.isin(["aplica_contactado","aplica_pendiente_llamar"]).sum()),
        )
        .reset_index()
    )
    st.dataframe(ab, hide_index=True, use_container_width=True)
else:
    st.info("No hay columna `grupo` en Leads.")


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
# HubSpot
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(f"<h2>HubSpot · deals con flag fakedoor (desde {EXPERIMENT.start_date})</h2>", unsafe_allow_html=True)
try:
    df_hs = load_hs_deals()
    if df_hs.empty:
        st.info("HubSpot no devolvió deals.")
    else:
        df_hs_display = df_hs.rename(columns=hs_src.FAKEDOOR_PROPS)
        st.dataframe(df_hs_display, hide_index=True, use_container_width=True)
        st.caption(f"{len(df_hs_display)} deals")

        with st.expander("Debug · propiedades vacías", expanded=False):
            null_pct = (df_hs.isna().mean() * 100).round(1).sort_values(ascending=False)
            empty = null_pct[null_pct > 0]
            if empty.empty:
                st.caption("Todas las propiedades trajeron datos.")
            else:
                st.write("Propiedades con % NaN (las que están al 100% probablemente tienen otro internal name):")
                st.dataframe(empty.to_frame("% NaN"), use_container_width=True)
except Exception as exc:
    st.error(f"Error consultando HubSpot: {type(exc).__name__}: {exc}")


st.divider()
st.caption(
    f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    "TTL cache: 120s. Scores ya consultados quedan persistidos en las "
    "columnas `Aplica` y `Metadata` del Sheet."
)
