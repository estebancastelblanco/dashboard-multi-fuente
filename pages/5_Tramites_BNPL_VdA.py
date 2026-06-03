"""Trámites BNPL · Valle de Aburrá — A/B test sobre conversión aprobado → cierre.

Reusa la query maestra de COL (fetch_oferta_formal_col_master) pero filtra el
universo a:
  - area_metropolitana = 'Valle de Aburrá'
  - ab_test_landing IN ('A', 'B')   (A = control, B = tratamiento)
  - fecha de aprobación de hoy en adelante (ajustable en el sidebar)

Muestra la comparativa de 2 funnels (A vs B), cada uno con solo 2 etapas
(Aprobados → Cierre), por fecha de aprobación y por fecha de ofertado.
"""
from __future__ import annotations

import os
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def _bootstrap_from_st_secrets() -> None:
    keys = ["BQ_PROJECT_ID", "GOOGLE_APPLICATION_CREDENTIALS_JSON"]
    try:
        for k in keys:
            if k in st.secrets and not os.environ.get(k):
                os.environ[k] = str(st.secrets[k])
    except Exception:
        return


_bootstrap_from_st_secrets()

from src.experiments import REGISTRY
from src.sources import bigquery as bq_src
from src.styling import inject_base_css, DEEP, PRIMARY, MED, ACCENT, LIGHT, WHITE

st.set_page_config(page_title="Trámites BNPL · Valle de Aburrá", layout="wide")
inject_base_css()

EXPERIMENT = next(e for e in REGISTRY if e.slug == "tramites-bnpl-vda")

st.markdown(
    f"<h1 style='color:{DEEP};font-size:1.5rem;font-weight:700;margin-bottom:0'>"
    f"{EXPERIMENT.title}</h1>"
    f"<div style='color:{MED};font-size:0.8rem;margin-bottom:20px'>"
    f"Habi · COL · Valle de Aburrá · BNPL · lanzado {EXPERIMENT.start_date} · "
    f"A/B sobre <code>ab_test_landing</code> · ofertas.habi.co</div>",
    unsafe_allow_html=True,
)

DAY = 86400
AREA_VALLE_ABURRA = "Valle de Aburrá"

# A = control (NO muestra la sección de trámites) · B = tratamiento (SÍ la muestra).
# Ojo: la doc del experimento tiene A/B invertidos; esta es la realidad.
VARIANTS = ["A", "B"]
VARIANT_LABELS = {
    "A": "A · Control (no muestra)",
    "B": "B · Tratamiento (sí muestra)",
}
VARIANT_COLOR = {"A": MED, "B": PRIMARY}


@st.cache_data(ttl=DAY, show_spinner="BigQuery · Trámites BNPL VdA…", persist="disk")
def load_vda_data() -> pd.DataFrame:
    """Universo COL filtrado a Valle de Aburrá + ab_test_landing A/B."""
    df = bq_src.fetch_oferta_formal_col_master()
    for col in ("fecha_aprobado", "fecha_aprobado_semana", "fecha_cierre",
                "v_fecha_promesa", "fecha_cierre_efectiva", "fecha_ofertado"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    # Filtro de universo del experimento: VdA + variante A/B.
    if "area_metropolitana" not in df.columns:
        df["area_metropolitana"] = pd.Series(dtype=str)
    mask = (
        df["area_metropolitana"].astype(str).str.strip().str.lower().isin(
            ["valle de aburrá", "valle de aburra", "1"]
        )
        & df["ab_test_landing"].astype(str).str.upper().str.strip().isin(["A", "B"])
    )
    df = df[mask].copy()
    df["ab_test_landing"] = df["ab_test_landing"].astype(str).str.upper().str.strip()
    return df


try:
    df = load_vda_data()
except Exception as exc:
    st.error(f"Error cargando BigQuery: {type(exc).__name__}: {exc}")
    st.stop()


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar — filtros
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    if st.button("Actualizar datos", use_container_width=True,
                 help="Refresca el cache de BigQuery."):
        load_vda_data.clear()
        st.rerun()
    st.markdown("---")
    st.markdown(
        f"<div style='color:{LIGHT};font-weight:700;font-size:0.9rem;margin-bottom:14px'>"
        f"Filtros</div>",
        unsafe_allow_html=True,
    )

    st.markdown("### Rango de fechas")
    default_start = date.today()
    default_end = date.today()
    sel_range = st.date_input(
        "rango",
        value=(default_start, default_end),
        label_visibility="collapsed",
        help="Por defecto: de hoy en adelante. Aplica sobre fecha_aprobado y "
             "fecha_ofertado según el funnel.",
    )
    if isinstance(sel_range, tuple) and len(sel_range) == 2:
        date_from, date_to = sel_range
    else:
        date_from, date_to = default_start, default_end

    st.markdown("### Variante")
    sel_variants = st.multiselect(
        "variantes", VARIANTS, default=VARIANTS,
        format_func=lambda v: VARIANT_LABELS.get(v, v),
        label_visibility="collapsed",
        help="A = control (no muestra sección) · B = tratamiento (sí la muestra).",
    )

    st.markdown("### NID")
    sel_nid = st.text_input(
        "nid", value="", placeholder="ej. 51479475715",
        label_visibility="collapsed",
        help="Filtra a un nid específico. Vacío = sin filtro.",
    ).strip()
    st.markdown("---")
    st.caption(
        f"Universo cargado: {df['nid'].nunique()} negocios VdA con variante A/B "
        f"(todas las fechas)."
    )


ts_from = pd.Timestamp(date_from)
ts_to = pd.Timestamp(date_to)
sel_variants = [v for v in VARIANTS if v in sel_variants]

if sel_nid:
    df = df[df["nid"].astype(str) == sel_nid].copy()
df = df[df["ab_test_landing"].isin(sel_variants)].copy()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _metrics(sub: pd.DataFrame) -> tuple[int, int, float]:
    """(aprobados, cierre, cvr%) — funnel de 2 etapas aprobado → cierre."""
    n_aprob = sub["nid"].dropna().nunique()
    n_cierre = sub.loc[sub["fecha_cierre_efectiva"].notna(), "nid"].dropna().nunique()
    cvr = (n_cierre / n_aprob * 100) if n_aprob else 0.0
    return n_aprob, n_cierre, cvr


def _funnel(sub: pd.DataFrame, variant: str) -> go.Figure:
    n_aprob, n_cierre, cvr = _metrics(sub)
    color = VARIANT_COLOR.get(variant, PRIMARY)
    fig = go.Figure(go.Funnel(
        y=["Aprobados", "Cierre"],
        x=[n_aprob, n_cierre],
        textinfo="value+percent initial",
        textfont=dict(size=14, color=WHITE),
        marker=dict(color=[color, LIGHT]),
        connector=dict(line=dict(color=MED, width=1)),
    ))
    fig.update_layout(
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=12),
        title=dict(text=f"{VARIANT_LABELS.get(variant, variant)}<br>"
                        f"<span style='font-size:12px;color:{MED}'>CVR aprobado→cierre: "
                        f"<b>{cvr:.1f}%</b></span>",
                   font=dict(size=13, color=DEEP)),
        height=320, margin=dict(l=10, r=10, t=70, b=10),
    )
    return fig


def _render_section(df_base: pd.DataFrame, date_col: str, titulo: str) -> None:
    st.markdown(f"<h2>{titulo}</h2>", unsafe_allow_html=True)
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    sub = df_base[df_base[date_col].between(ts_from, ts_to, inclusive="both")]
    if sub.empty:
        st.info(
            "Sin negocios en el rango seleccionado. El experimento se lanzó el "
            f"{EXPERIMENT.start_date}; aún no hay aprobaciones/ofertas en el rango "
            "'de hoy en adelante'. Amplía el rango en el sidebar para ver histórico."
        )
        return
    cols = st.columns(len(sel_variants) or 1)
    for col, v in zip(cols, sel_variants):
        with col:
            st.plotly_chart(_funnel(sub[sub["ab_test_landing"] == v], v),
                            use_container_width=True, key=f"funnel_{date_col}_{v}")
    # Resumen comparativo
    resumen = []
    for v in sel_variants:
        n_a, n_c, cvr = _metrics(sub[sub["ab_test_landing"] == v])
        resumen.append({"Variante": VARIANT_LABELS.get(v, v),
                        "Aprobados": n_a, "Cierre": n_c, "CVR": f"{cvr:.1f}%"})
    if resumen:
        st.dataframe(pd.DataFrame(resumen), hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Secciones · 2 funnels (A vs B) por fecha de aprobación y por fecha de ofertado
# ─────────────────────────────────────────────────────────────────────────────
_render_section(df, "fecha_aprobado", "Funnel aprobado → cierre · por fecha de aprobación")
st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
_render_section(df, "fecha_ofertado", "Funnel aprobado → cierre · por fecha de ofertado")

st.divider()
st.caption(
    f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    f"TTL cache: 24h · Rango activo: {date_from.isoformat()} → {date_to.isoformat()} · "
    f"Variantes: {', '.join(sel_variants)} · Filtro: Valle de Aburrá + ab_test_landing A/B."
)
