"""FakeDoor Habicapital — dashboard live."""
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

st.markdown(
    f"<h1 style='color:{DEEP};font-size:1.5rem;font-weight:700;margin-bottom:0'>"
    f"Fake Door · Crédito de Libre Inversión con Garantía Hipotecaria</h1>"
    f"<div style='color:{MED};font-size:0.8rem;margin-bottom:20px'>"
    f"Habi Capital · lanzado 20 abr 2026 · A/B: AH=84m vs BH=120m · 20% EA</div>",
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────────────────────────────────────
# Loaders cacheados
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=120, show_spinner="Cargando Leads (Google Sheets)…")
def load_leads() -> pd.DataFrame:
    df = gs_src.fetch_tab("Leads")
    if df.empty:
        return df
    for col in ("cedula", "telefono", "contesto?"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    return df


@st.cache_data(ttl=120, show_spinner="Cargando Entrevistas (Google Sheets)…")
def load_entrevistas() -> pd.DataFrame:
    df = gs_src.fetch_tab("Entrevista")
    if not df.empty and "telefono" in df.columns:
        df["telefono"] = df["telefono"].astype(str).str.strip()
    return df


@st.cache_data(ttl=300, show_spinner="Consultando HubSpot…")
def load_hs_deals() -> pd.DataFrame:
    return hs_src.fetch_fakedoor_deals(since_iso="2026-04-20")


@st.cache_data(ttl=600, show_spinner="Consultando scores…")
def load_scores(cedulas: tuple[str, ...]) -> pd.DataFrame:
    if not cedulas:
        return pd.DataFrame(columns=["cedula", "score", "nivel_riesgo", "aplica"])
    return score_src.consultar_batch(list(cedulas))


def _norm_phone(s: object) -> str:
    s = str(s).strip().replace(" ", "").lstrip("+")
    if s.startswith("57") and len(s) > 10:
        s = s[2:]
    return s[-10:] if len(s) >= 10 else s


# ─────────────────────────────────────────────────────────────────────────────
# Carga base
# ─────────────────────────────────────────────────────────────────────────────
try:
    df_leads = load_leads()
except Exception as exc:
    st.error(f"Error cargando Leads: {exc}")
    st.stop()

if df_leads.empty:
    st.warning("La pestaña Leads está vacía.")
    st.stop()

try:
    df_int = load_entrevistas()
except Exception as exc:
    df_int = pd.DataFrame()
    st.warning(f"No pude cargar Entrevistas: {exc}")

df_leads["phone_norm"] = df_leads["telefono"].apply(_norm_phone)
if not df_int.empty:
    df_int["phone_norm"] = df_int["telefono"].apply(_norm_phone)


# Score por cédula
cedulas = tuple(df_leads["cedula"].dropna().astype(str).unique()) if "cedula" in df_leads.columns else ()
df_scores = load_scores(cedulas)

# Cruzar leads + entrevistas + scores
df = df_leads.merge(
    df_scores[["cedula", "score", "nivel_riesgo", "aplica"]],
    on="cedula", how="left",
)
if not df_int.empty:
    df = df.merge(
        df_int[["phone_norm", "tiene hipoteca?"]],
        on="phone_norm", how="left",
    )
else:
    df["tiene hipoteca?"] = None

df["aplica"] = df["aplica"].fillna("pending")
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
    "aplica_contactado":         GREEN_DARK,
    "aplica_pendiente_llamar":   GREEN_LIGHT,
    "pendiente_score":           YELLOW,
    "no_aplica":                 GREY,
    "error":                     RED,
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
n_leads = len(df)
n_tc = int(df["contesto?"].notna().sum())
n_interes = int((df["contesto?"].astype(str).str.lower() == "si").sum())
n_aplica = int(df["status"].isin(["aplica_contactado", "aplica_pendiente_llamar"]).sum())
n_call_list = int((df["status"] == "aplica_pendiente_llamar").sum())
n_pendiente_score = int((df["status"] == "pendiente_score").sum())

c1, c2, c3, c4, c5 = st.columns(5)
c1.markdown(kpi_card("Leads T&C", n_leads, "filas en Sheet Leads"), unsafe_allow_html=True)
c2.markdown(kpi_card("Contactados", int(df["contactado"].sum()), f"{int(df['contactado'].sum())/max(1,n_leads):.0%}"), unsafe_allow_html=True)
c3.markdown(kpi_card("Interés activo", n_interes, f"{n_interes/max(1,n_leads):.0%}"), unsafe_allow_html=True)
c4.markdown(kpi_card("Elegibles", n_aplica, "score ≥720 + sin hipoteca"), unsafe_allow_html=True)
c5.markdown(kpi_card("Por llamar", n_call_list, "aplican y no contactados"), unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline de leads (vista clave)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Pipeline de leads</h2>", unsafe_allow_html=True)

# Leyenda
legend_html = "<div style='font-size:0.78rem;color:#444;margin-bottom:8px'>"
for k, c in STATUS_COLORS.items():
    legend_html += (
        f"<span class='legend-box' style='background:{c}'></span>"
        f"<span style='margin-right:14px'>{STATUS_LABELS[k]}</span>"
    )
legend_html += "</div>"
st.markdown(legend_html, unsafe_allow_html=True)

if not score_src.is_configured():
    st.info(
        "ℹ️ `SCORE_API_URL` y `SCORE_API_TOKEN` no están configurados → todos los "
        "leads aparecen como **Pendiente de score**. Defínelos en `.env` (local) o "
        "Streamlit Secrets para activar la consulta en vivo."
    )

DISPLAY_COLS = ["nombre_completo", "telefono", "cedula", "grupo",
                "contesto?", "tiene hipoteca?", "score", "aplica", "status"]
disp = df[[c for c in DISPLAY_COLS if c in df.columns]].copy()
disp = disp.rename(columns={
    "nombre_completo": "Nombre", "telefono": "Teléfono", "cedula": "Cédula",
    "grupo": "Grupo", "contesto?": "Contesto?", "tiene hipoteca?": "Hipoteca?",
    "score": "Score", "aplica": "Aplica",
    "status": "_status",
})


def _row_color(row):
    color = STATUS_COLORS.get(row["_status"], "")
    text = "white" if row["_status"] in ("aplica_contactado", "error") else "#222"
    return [f"background-color:{color};color:{text}" for _ in row.index]


styled = disp.drop(columns=["_status"]).style.apply(
    lambda _r: _row_color(disp.loc[_r.name]), axis=1
)

# Orden: call list primero
disp_sorted = disp.sort_values(
    by="_status",
    key=lambda s: s.map({
        "aplica_pendiente_llamar": 0,
        "pendiente_score": 1,
        "aplica_contactado": 2,
        "no_aplica": 3,
        "error": 4,
    }).fillna(99),
).reset_index(drop=True)
styled_sorted = disp_sorted.drop(columns=["_status"]).style.apply(
    lambda _r: _row_color(disp_sorted.loc[_r.name]), axis=1
)

st.dataframe(styled_sorted, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Funnel
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Embudo del experimento</h2>", unsafe_allow_html=True)

n_contactados = int(df["contactado"].sum())
f_labels = ["Firmaron T&C", "Contactados", "Interés activo", "Aplican", "Por llamar"]
f_vals   = [n_leads, n_contactados, n_interes, n_aplica, n_call_list]
f_colors = [DEEP, PRIMARY, MED, ACCENT, LIGHT]
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
    height=320, margin=dict(l=10, r=140, t=10, b=10),
    xaxis=dict(gridcolor="#ede8f5", tickformat=",d"),
    yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig_funnel, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# A/B test
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
# Entrevistas cualitativas
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Entrevistas cualitativas</h2>", unsafe_allow_html=True)

if df_int.empty:
    st.info("La pestaña Entrevista está vacía.")
else:
    cols_int = [c for c in ["telefono", "tiene hipoteca?", "P1", "P4", "P5", "P9"] if c in df_int.columns]
    st.dataframe(df_int[cols_int], hide_index=True, use_container_width=True)
    st.caption(f"{len(df_int)} entrevistas")


# ─────────────────────────────────────────────────────────────────────────────
# HubSpot deals
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>HubSpot · deals con flag fakedoor (desde 20 abr)</h2>", unsafe_allow_html=True)

try:
    df_hs = load_hs_deals()
    if df_hs.empty:
        st.info("HubSpot no devolvió deals con `flag_fakedoor` ≥ 20 abr 2026.")
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
                st.caption(
                    "Si ves 100% NaN en alguna propiedad custom, dime el label exacto en HubSpot "
                    "y la cambio. Internal names se ven en HubSpot → Settings → Properties."
                )
except Exception as exc:
    st.error(f"Error consultando HubSpot: {type(exc).__name__}: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Footer
# ─────────────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    "TTL cache 120s (Sheets) · 300s (HubSpot) · 600s (Scores). "
    "Refresca la página para forzar pull."
)
