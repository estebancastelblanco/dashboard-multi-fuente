"""Oferta formal MX — A/B/C sobre conversión aprobado → cierre."""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def _bootstrap_from_st_secrets() -> None:
    keys = [
        "BQ_PROJECT_ID", "GOOGLE_APPLICATION_CREDENTIALS_JSON",
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
from src.styling import (
    inject_base_css, kpi_card,
    DEEP, PRIMARY, MED, ACCENT, LIGHT, PALE, WHITE,
)

st.set_page_config(page_title="Oferta formal MX", layout="wide")
inject_base_css()

EXPERIMENT = next(e for e in REGISTRY if e.slug == "oferta-formal-mx")

st.markdown(
    f"<h1 style='color:{DEEP};font-size:1.5rem;font-weight:700;margin-bottom:0'>"
    f"{EXPERIMENT.title}</h1>"
    f"<div style='color:{MED};font-size:0.8rem;margin-bottom:20px'>"
    f"Habi · MX · lanzado {EXPERIMENT.start_date} · A vs B vs C</div>",
    unsafe_allow_html=True,
)

DAY = 86400

VARIANTS = ["A", "B", "C"]
COLOR_APROBADOS = PRIMARY
COLOR_APROBADOS_RTA = ACCENT
COLOR_CIERRE = LIGHT
COLOR_CVR = "#84cc16"           # verde-amarillo
COLOR_CVR_RTA = "#06b6d4"       # cyan


@st.cache_data(ttl=DAY, show_spinner="BigQuery · ABC Test Landing CO…", persist="disk")
def load_abc_data() -> pd.DataFrame:
    df = bq_src.fetch_abc_test_landing_co()
    for col in ("fecha_aprobado", "fecha_aprobado_semana", "fecha_cierre",
                "v_fecha_promesa", "fecha_cierre_efectiva", "fecha_ofertado"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar — filtros
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    if st.button("Actualizar datos", use_container_width=True,
                 help="Refresca el cache de BigQuery."):
        try:
            load_abc_data.clear()
        except Exception:
            pass
        st.rerun()
    st.markdown("---")
    st.markdown(f"<div style='color:{LIGHT};font-weight:700;font-size:0.9rem;margin-bottom:14px'>Filtros</div>", unsafe_allow_html=True)

    st.markdown("### Rango de fechas")
    default_start = date(2026, 5, 19)
    default_end = date.today()
    sel_range = st.date_input(
        "rango",
        value=(default_start, default_end),
        label_visibility="collapsed",
        help="Filtro aplica sobre fecha_aprobado y fecha_ofertado según el gráfico.",
    )
    if isinstance(sel_range, tuple) and len(sel_range) == 2:
        date_from, date_to = sel_range
    else:
        date_from, date_to = default_start, default_end

    st.markdown("### Variante")
    sel_variants = st.multiselect(
        "variantes", VARIANTS, default=["B", "C"],
        label_visibility="collapsed",
        help="Por defecto B vs C (las que el experimento compara). A queda residual.",
    )
    st.markdown("---")


# ─────────────────────────────────────────────────────────────────────────────
# Carga
# ─────────────────────────────────────────────────────────────────────────────
try:
    df = load_abc_data()
except Exception as exc:
    st.error(f"Error cargando BigQuery: {type(exc).__name__}: {exc}")
    st.stop()

if df.empty:
    st.warning("No hay datos para el experimento.")
    st.stop()

ts_from = pd.Timestamp(date_from)
ts_to = pd.Timestamp(date_to)


def _metric_block(_df: pd.DataFrame) -> dict:
    """Aprobados / Aprobados rta / Cierre / CVR / CVR rta según defs Looker."""
    nids_total = _df["nid"].dropna().nunique()
    rta_mask = _df["estado_aprobado"].isin(["aceptado", "rechazado"])
    nids_rta = _df.loc[rta_mask, "nid"].dropna().nunique()
    cierre_mask = _df["fecha_cierre_efectiva"].notna()
    nids_cierre = _df.loc[cierre_mask, "nid"].dropna().nunique()
    cvr = (nids_cierre / nids_total) if nids_total else 0.0
    cvr_rta = (nids_cierre / nids_rta) if nids_rta else 0.0
    return dict(aprobados=nids_total, aprobados_rta=nids_rta, cierre=nids_cierre,
                cvr=cvr, cvr_rta=cvr_rta)


def _bars_lines_by_variant(df_filt: pd.DataFrame, date_col: str, title: str | None = None) -> go.Figure:
    """Gráfico de barras (Aprobados/AprobadosRta/Cierre) + líneas (CVR/CVR Rta)
    agrupado por variante. df_filt ya debe venir filtrado por el rango de fechas."""
    rows = []
    for v in sel_variants:
        sub = df_filt[df_filt["abc_test_landing_co"] == v]
        m = _metric_block(sub)
        m["variante"] = v
        rows.append(m)
    if not rows:
        fig = go.Figure()
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=70, b=10))
        return fig
    g = pd.DataFrame(rows)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Aprobados", x=g["variante"], y=g["aprobados"],
        marker_color=COLOR_APROBADOS, text=g["aprobados"], textposition="outside",
        yaxis="y", offsetgroup=0,
    ))
    fig.add_trace(go.Bar(
        name="Aprobados rta", x=g["variante"], y=g["aprobados_rta"],
        marker_color=COLOR_APROBADOS_RTA, text=g["aprobados_rta"], textposition="outside",
        yaxis="y", offsetgroup=1,
    ))
    fig.add_trace(go.Bar(
        name="Cierre", x=g["variante"], y=g["cierre"],
        marker_color=COLOR_CIERRE, text=g["cierre"], textposition="outside",
        yaxis="y", offsetgroup=2,
    ))
    fig.add_trace(go.Scatter(
        name="CVR", x=g["variante"], y=g["cvr"] * 100,
        mode="lines+markers+text",
        line=dict(color=COLOR_CVR, width=3), marker=dict(size=9),
        text=[f"{v*100:.2f}%" for v in g["cvr"]],
        textposition="top center",
        yaxis="y2",
    ))
    fig.add_trace(go.Scatter(
        name="CVR Rta", x=g["variante"], y=g["cvr_rta"] * 100,
        mode="lines+markers+text",
        line=dict(color=COLOR_CVR_RTA, width=3), marker=dict(size=9),
        text=[f"{v*100:.2f}%" for v in g["cvr_rta"]],
        textposition="bottom center",
        yaxis="y2",
    ))
    fig.update_layout(
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=11),
        height=420, margin=dict(l=10, r=10, t=70, b=10),
        barmode="group", bargap=0.18, bargroupgap=0.05,
        xaxis=dict(title="Variante", gridcolor="#ede8f5"),
        yaxis=dict(title="Aprobados | Aprobados rta | Cierre", gridcolor="#ede8f5"),
        yaxis2=dict(title="CVR | CVR Rta", overlaying="y", side="right",
                    ticksuffix="%", gridcolor="rgba(0,0,0,0)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.06, x=0,
                    font=dict(size=11)),
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Filtros aplicados (fechas)
# ─────────────────────────────────────────────────────────────────────────────
df_var = df[df["abc_test_landing_co"].isin(sel_variants)].copy()
df_apro = df_var[
    df_var["fecha_aprobado"].between(ts_from, ts_to, inclusive="both")
]
df_ofer = df_var[
    df_var["fecha_ofertado"].between(ts_from, ts_to, inclusive="both")
]


# ─────────────────────────────────────────────────────────────────────────────
# Sección 1 · CVR por variante · fecha_aprobado y fecha_ofertado
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>CVR por variante — fecha de aprobación</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
st.plotly_chart(
    _bars_lines_by_variant(df_apro, "fecha_aprobado"),
    use_container_width=True, key="chart_cvr_aprobado",
)

st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
st.markdown("<h2>CVR por variante — fecha de ofertado</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
st.plotly_chart(
    _bars_lines_by_variant(df_ofer, "fecha_ofertado"),
    use_container_width=True, key="chart_cvr_ofertado",
)


# ─────────────────────────────────────────────────────────────────────────────
# Sección 2 · Funnel semanal por fecha de aprobación
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
st.markdown("<h2>Funnel semanal — fecha de aprobación</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

if df_apro.empty:
    st.info("Sin aprobados en el rango seleccionado.")
else:
    rows = []
    for week, sub in df_apro.groupby(df_apro["fecha_aprobado"].dt.to_period("W-MON")):
        m = _metric_block(sub)
        m["semana"] = week.start_time.date()
        rows.append(m)
    g = pd.DataFrame(rows).sort_values("semana")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Aprobados", x=g["semana"], y=g["aprobados"],
        marker_color=COLOR_APROBADOS, text=g["aprobados"], textposition="outside",
        yaxis="y", offsetgroup=0,
    ))
    fig.add_trace(go.Bar(
        name="Aprobados rta", x=g["semana"], y=g["aprobados_rta"],
        marker_color=COLOR_APROBADOS_RTA, text=g["aprobados_rta"], textposition="outside",
        yaxis="y", offsetgroup=1,
    ))
    fig.add_trace(go.Bar(
        name="Cierre", x=g["semana"], y=g["cierre"],
        marker_color=COLOR_CIERRE, text=g["cierre"], textposition="outside",
        yaxis="y", offsetgroup=2,
    ))
    fig.add_trace(go.Scatter(
        name="CVR", x=g["semana"], y=g["cvr"] * 100,
        mode="lines+markers+text", line=dict(color=COLOR_CVR, width=3),
        marker=dict(size=9),
        text=[f"{v*100:.2f}%" for v in g["cvr"]],
        textposition="top center", yaxis="y2",
    ))
    fig.add_trace(go.Scatter(
        name="CVR Rta", x=g["semana"], y=g["cvr_rta"] * 100,
        mode="lines+markers+text", line=dict(color=COLOR_CVR_RTA, width=3),
        marker=dict(size=9),
        text=[f"{v*100:.2f}%" for v in g["cvr_rta"]],
        textposition="bottom center", yaxis="y2",
    ))
    fig.update_layout(
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=11),
        height=440, margin=dict(l=10, r=10, t=70, b=10),
        barmode="group", bargap=0.18, bargroupgap=0.05,
        xaxis=dict(title="Semana de fecha aprobado", gridcolor="#ede8f5"),
        yaxis=dict(title="Aprobados | Aprobados rta | Cierre", gridcolor="#ede8f5"),
        yaxis2=dict(title="CVR | CVR Rta", overlaying="y", side="right",
                    ticksuffix="%", gridcolor="rgba(0,0,0,0)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.06, x=0,
                    font=dict(size=11)),
    )
    st.plotly_chart(fig, use_container_width=True, key="chart_funnel_semanal")


# ─────────────────────────────────────────────────────────────────────────────
# Sección 3 · Tabla desglose
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
st.markdown(f"<h2>Desglose ({len(df_apro):,})</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

table = df_apro.copy()
table["Cierre"] = table["fecha_cierre_efectiva"].notna().map({True: "Sí", False: "No"})
show_cols = [
    ("abc_test_landing_co", "abc_test_landing_co"),
    ("nid", "nid"),
    ("estado_aprobado", "estado_aprobado"),
    ("fecha_aprobado", "fecha_aprobado"),
    ("Cierre", "Cierre"),
    ("fecha_cierre", "fecha_cierre"),
    ("fue_ofertado", "fue_ofertado"),
    ("categoria_ancla", "Categoria ancla"),
]
table_view = table[[c for c, _ in show_cols]].rename(columns=dict(show_cols))
st.dataframe(table_view, hide_index=True, use_container_width=True)

st.divider()
st.caption(
    f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    "TTL cache: 24h. Rango activo: "
    f"{date_from.isoformat()} → {date_to.isoformat()} · Variantes: {', '.join(sel_variants)}."
)
