"""Oferta formal COL — A/B/C sobre conversión aprobado → cierre."""
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

st.set_page_config(page_title="Oferta formal COL", layout="wide")
inject_base_css()

EXPERIMENT = next(e for e in REGISTRY if e.slug == "oferta-formal-co")

st.markdown(
    f"<h1 style='color:{DEEP};font-size:1.5rem;font-weight:700;margin-bottom:0'>"
    f"{EXPERIMENT.title}</h1>"
    f"<div style='color:{MED};font-size:0.8rem;margin-bottom:20px'>"
    f"Habi · COL · lanzado {EXPERIMENT.start_date} · A vs B vs C · "
    f"ofertas.habi.co</div>",
    unsafe_allow_html=True,
)

DAY = 86400

VARIANTS = ["A", "B", "C", "(sin variante)"]
NULL_VARIANT_LABEL = "(sin variante)"
COLOR_APROBADOS = PRIMARY
COLOR_APROBADOS_RTA = ACCENT
COLOR_CIERRE = LIGHT
COLOR_CVR = "#84cc16"           # verde-amarillo
COLOR_CVR_RTA = "#06b6d4"       # cyan


@st.cache_data(ttl=DAY, show_spinner="BigQuery · Oferta formal COL…", persist="disk")
def load_oferta_formal_col_data() -> pd.DataFrame:
    df = bq_src.fetch_oferta_formal_col_master()
    for col in ("fecha_aprobado", "fecha_aprobado_semana", "fecha_cierre",
                "v_fecha_promesa", "fecha_cierre_efectiva", "fecha_ofertado"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


load_abc_data = load_oferta_formal_col_data


@st.cache_data(ttl=DAY, show_spinner="BigQuery · eventos landing COL…", persist="disk")
def load_landing_events() -> pd.DataFrame:
    """Eventos en https://ofertas.habi.co/<uuid>: una fila por UUID."""
    try:
        return bq_src.fetch_oferta_formal_col_landing_events()
    except Exception as exc:
        st.warning(f"BigQuery eventos landing: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["uuid", "events", "first_seen", "last_seen"])


@st.cache_data(ttl=DAY, show_spinner="BigQuery · eventos Segment landing COL…", persist="disk")
def load_landing_tracks() -> pd.DataFrame:
    """Eventos individuales (Segment tracks) en ofertas.habi.co."""
    try:
        return bq_src.fetch_oferta_formal_col_landing_tracks()
    except Exception as exc:
        st.warning(f"BigQuery tracks: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["uuid", "event_name", "timestamp"])


@st.cache_data(ttl=DAY, show_spinner="BigQuery · envíos WhatsApp COL…", persist="disk")
def load_envios_wa_v2() -> pd.DataFrame:
    """Filtrado a message_status IN ('read','delivered') · deals Colombia."""
    try:
        return bq_src.fetch_oferta_formal_col_envios_wa()
    except Exception as exc:
        st.warning(f"BigQuery envíos WA: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["nid", "message_status", "created_at"])


load_envios_wa = load_envios_wa_v2


LANDING_SHEET_ID = "1_EMQesd_n67wSqReYaTdJtSd3uvZsb7GXPRD6LyrJN4"
import re
_UUID_RX = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)


@st.cache_data(ttl=120, show_spinner="Sheets · logs de envíos COL…")
def load_landing_logs() -> pd.DataFrame:
    """Filas del Sheet LOGS donde la URL es de ofertas.habi.co."""
    try:
        from src.sources import gsheets as gs_src
        df = gs_src.fetch_tab("LOGS", sheet_id=LANDING_SHEET_ID)
    except Exception as exc:
        st.warning(f"Sheets logs: {type(exc).__name__}: {exc}")
        return pd.DataFrame()
    if df.empty:
        return df
    mask = (
        df.get("base_url", pd.Series(dtype=str)).astype(str).str.contains(
            r"ofertas\.habi\.co", case=False, na=False, regex=True,
        )
        | df.get("full_url", pd.Series(dtype=str)).astype(str).str.contains(
            r"ofertas\.habi\.co", case=False, na=False, regex=True,
        )
        | df.get("url", pd.Series(dtype=str)).astype(str).str.contains(
            r"ofertas\.habi\.co", case=False, na=False, regex=True,
        )
    )
    df = df[mask].copy()

    def _uuid(row):
        for col in ("Deal_uuid", "base_url", "full_url", "url"):
            m = _UUID_RX.search(str(row.get(col, "") or ""))
            if m:
                return m.group(1).lower()
        return None

    df["uuid"] = df.apply(_uuid, axis=1)
    df = df[df["uuid"].notna()].copy()
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Carga (antes del sidebar para alimentar opciones de equipo_sellers)
# ─────────────────────────────────────────────────────────────────────────────
try:
    df = load_abc_data()
except Exception as exc:
    st.error(f"Error cargando BigQuery: {type(exc).__name__}: {exc}")
    st.stop()

if df.empty:
    st.warning("No hay datos para el experimento.")
    st.stop()

if "equipo_sellers" not in df.columns:
    df["equipo_sellers"] = pd.Series(dtype=str)

# Pipelines operativos COL — ampliar cuando se confirmen los IDs en HubSpot.
PIPELINE_LABELS: dict[str, str] = {}
if "pipeline" not in df.columns:
    df["pipeline"] = pd.Series(dtype=str)
df["pipeline_label"] = (
    df["pipeline"].astype(str).map(PIPELINE_LABELS).fillna("(otro)")
)

# Normalizar variante: NULL → "(sin variante)" para que isin() funcione
# directo en todas las secciones.
df["abc_test_landing_co"] = df["abc_test_landing_co"].fillna(NULL_VARIANT_LABEL)
df.loc[df["abc_test_landing_co"].astype(str).str.strip() == "", "abc_test_landing_co"] = NULL_VARIANT_LABEL


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar — filtros
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    if st.button("Actualizar datos", use_container_width=True,
                 help="Refresca el cache de BigQuery."):
        for loader in (load_abc_data, load_landing_events, load_landing_logs, load_envios_wa, load_landing_tracks):
            try:
                loader.clear()
            except Exception:
                pass
        st.rerun()
    st.markdown("---")
    st.markdown(f"<div style='color:{LIGHT};font-weight:700;font-size:0.9rem;margin-bottom:14px'>Filtros</div>", unsafe_allow_html=True)

    st.markdown("### Rango de fechas")
    default_start = date(2026, 2, 16)
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

    # Equipos disponibles = los que tienen al menos un row con fecha_aprobado
    # dentro del rango actual. Los equipos sin datos en el rango no aportan
    # información si los deseleccionas, así que no los mostramos.
    _df_dates = pd.to_datetime(df["fecha_aprobado"], errors="coerce")
    _in_range = (_df_dates >= pd.Timestamp(date_from)) & (_df_dates <= pd.Timestamp(date_to))
    equipos_all = sorted([
        e for e in df.loc[_in_range, "equipo_sellers"].dropna().astype(str).str.strip().unique()
        if e and e.lower() != "nan"
    ])

    st.markdown("### Variante")
    sel_variants = st.multiselect(
        "variantes", VARIANTS, default=VARIANTS,
        label_visibility="collapsed",
        help="Por defecto A, B, C y '(sin variante)'. Deselecciona para enfocar.",
    )

    st.markdown("### NID")
    sel_nid = st.text_input(
        "nid", value="", placeholder="ej. 59030823233",
        label_visibility="collapsed",
        help="Filtra a un nid específico. Vacío = sin filtro.",
    ).strip()

    st.markdown("### Equipo sellers")
    sel_equipos = st.multiselect(
        "equipos", equipos_all, default=equipos_all,
        label_visibility="collapsed",
        help="Filtra por equipo_sellers de detalle_ofertas_col.",
    )

    st.markdown("### Pipeline")
    pipeline_opts = list(PIPELINE_LABELS.values()) + ["(otro)"]
    pipeline_default = pipeline_opts
    sel_pipelines = st.multiselect(
        "pipelines", pipeline_opts, default=pipeline_default,
        label_visibility="collapsed",
        help="Los deals sin pipeline asignado SIEMPRE pasan. "
             "Agrega pipelines operativos COL a PIPELINE_LABELS cuando se confirmen.",
    )
    st.markdown("---")


ts_from = pd.Timestamp(date_from)
ts_to = pd.Timestamp(date_to)


# Aplicar filtros transversales (nid + equipo) al universo entero ANTES de
# derivar los subsets por variante/fecha. Así las secciones aguas abajo
# heredan los filtros sin repetir lógica.
if sel_nid:
    df = df[df["nid"].astype(str) == sel_nid].copy()
if sel_equipos and len(sel_equipos) < len(equipos_all):
    df = df[df["equipo_sellers"].astype(str).isin(sel_equipos)].copy()
# Pipeline: los rows con pipeline NULL (= "(sin variante)" del LEFT JOIN sin
# match en base_hubspot) SIEMPRE pasan, independiente de la selección. El
# filtro solo descarta rows con pipeline asignado que NO esté en sel_pipelines.
_pipeline_null = df["pipeline"].isna() | (df["pipeline"].astype(str).str.lower().isin(["", "nan", "none"]))
df = df[_pipeline_null | df["pipeline_label"].isin(sel_pipelines)].copy()


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
        mode="lines+markers",
        line=dict(color=COLOR_CVR, width=3), marker=dict(size=11),
        yaxis="y2",
        hovertemplate="<b>%{x}</b><br>CVR: %{y:.2f}%<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        name="CVR Rta", x=g["variante"], y=g["cvr_rta"] * 100,
        mode="lines+markers",
        line=dict(color=COLOR_CVR_RTA, width=3), marker=dict(size=11),
        yaxis="y2",
        hovertemplate="<b>%{x}</b><br>CVR Rta: %{y:.2f}%<extra></extra>",
    ))
    # Anotaciones de % CVR/CVR Rta separadas verticalmente con offsets fijos
    # en pixels para que no choquen entre sí ni con las labels de las barras.
    for _, row in g.iterrows():
        fig.add_annotation(
            x=row["variante"], y=row["cvr"] * 100, yref="y2",
            text=f"<b>{row['cvr']*100:.2f}%</b>", showarrow=False,
            yshift=14, font=dict(size=12, color=COLOR_CVR),
            bgcolor="rgba(255,255,255,0.85)", borderpad=2,
        )
        fig.add_annotation(
            x=row["variante"], y=row["cvr_rta"] * 100, yref="y2",
            text=f"<b>{row['cvr_rta']*100:.2f}%</b>", showarrow=False,
            yshift=-16, font=dict(size=12, color=COLOR_CVR_RTA),
            bgcolor="rgba(255,255,255,0.85)", borderpad=2,
        )
    fig.update_layout(
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=11),
        height=420, margin=dict(l=10, r=10, t=70, b=10),
        barmode="group", bargap=0.18, bargroupgap=0.05,
        xaxis=dict(title="Variante", gridcolor="#ede8f5",
                   type="category", categoryorder="array", categoryarray=sel_variants),
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
# Mantener orden canónico A, B, C, (sin variante) — multiselect respeta orden
# de selección pero queremos eje X consistente.
sel_variants = [v for v in VARIANTS if v in sel_variants]
df_var = df[df["abc_test_landing_co"].isin(sel_variants)].copy()
# Funnel semanal: universo completo MX (sin filtrar por variante) para que
# los conteos cuadren con Looker, que no aplica ese filtro.
df_apro_all = df[df["fecha_aprobado"].between(ts_from, ts_to, inclusive="both")]
df_apro = df_var[
    df_var["fecha_aprobado"].between(ts_from, ts_to, inclusive="both")
]
df_ofer = df_var[
    df_var["fecha_ofertado"].between(ts_from, ts_to, inclusive="both")
]


# ─────────────────────────────────────────────────────────────────────────────
# Sección 1 · CVR por variante · fecha_aprobado y fecha_ofertado
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>CVR por variante — fecha de ofertado</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
st.plotly_chart(
    _bars_lines_by_variant(df_ofer, "fecha_ofertado"),
    use_container_width=True, key="chart_cvr_ofertado",
)

st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
st.markdown("<h2>CVR por variante — fecha de aprobación</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
st.plotly_chart(
    _bars_lines_by_variant(df_apro, "fecha_aprobado"),
    use_container_width=True, key="chart_cvr_aprobado",
)


# ─────────────────────────────────────────────────────────────────────────────
# Sección 2 · Funnel semanal por fecha de aprobación
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
st.markdown("<h2>Funnel semanal — fecha de aprobación</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

if df_apro_all.empty:
    st.info("Sin aprobados en el rango seleccionado.")
else:
    rows = []
    # W-TUE = semana que termina martes → empieza miércoles (alineado con Looker WEEK(WEDNESDAY))
    for week, sub in df_apro_all.groupby(df_apro_all["fecha_aprobado"].dt.to_period("W-TUE")):
        m = _metric_block(sub)
        m["semana_date"] = week.start_time.date()
        rows.append(m)
    g = pd.DataFrame(rows).sort_values("semana_date")
    # Etiqueta categórica en español ("25 mar 2026") — usar string evita el
    # bug de Plotly que no agrupa barras cuando el eje X es type=date.
    _meses_es = {1:"ene",2:"feb",3:"mar",4:"abr",5:"may",6:"jun",
                 7:"jul",8:"ago",9:"sep",10:"oct",11:"nov",12:"dic"}
    g["semana"] = g["semana_date"].apply(
        lambda d: f"{d.day} {_meses_es[d.month]} {d.year}"
    )
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
        mode="lines+markers", line=dict(color=COLOR_CVR, width=3),
        marker=dict(size=11),
        hovertemplate="%{x}<br>CVR: %{y:.2f}%<extra></extra>",
        yaxis="y2",
    ))
    fig.add_trace(go.Scatter(
        name="CVR Rta", x=g["semana"], y=g["cvr_rta"] * 100,
        mode="lines+markers", line=dict(color=COLOR_CVR_RTA, width=3),
        marker=dict(size=11),
        hovertemplate="%{x}<br>CVR Rta: %{y:.2f}%<extra></extra>",
        yaxis="y2",
    ))
    for _, row in g.iterrows():
        fig.add_annotation(
            x=row["semana"], y=row["cvr"] * 100, yref="y2",
            text=f"<b>{row['cvr']*100:.1f}%</b>", showarrow=False,
            yshift=14, font=dict(size=11, color=COLOR_CVR),
            bgcolor="rgba(255,255,255,0.85)", borderpad=2,
        )
        fig.add_annotation(
            x=row["semana"], y=row["cvr_rta"] * 100, yref="y2",
            text=f"<b>{row['cvr_rta']*100:.1f}%</b>", showarrow=False,
            yshift=-16, font=dict(size=11, color=COLOR_CVR_RTA),
            bgcolor="rgba(255,255,255,0.85)", borderpad=2,
        )
    fig.update_layout(
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=11),
        height=440, margin=dict(l=10, r=10, t=70, b=10),
        barmode="group", bargap=0.25, bargroupgap=0.05,
        xaxis=dict(title="Semana de fecha aprobado", gridcolor="#ede8f5",
                   type="category", categoryorder="array",
                   categoryarray=list(g["semana"])),
        yaxis=dict(title="Aprobados | Aprobados rta | Cierre", gridcolor="#ede8f5"),
        yaxis2=dict(title="CVR | CVR Rta", overlaying="y", side="right",
                    ticksuffix="%", gridcolor="rgba(0,0,0,0)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.06, x=0,
                    font=dict(size=11)),
    )
    st.plotly_chart(fig, use_container_width=True, key="chart_funnel_semanal")


# ─────────────────────────────────────────────────────────────────────────────
# Sección 3 · Distribución por variante + funnel de usabilidad de la landing
# Estas secciones también respetan el rango de fechas (vía fecha_aprobado)
# y los filtros de nid / equipo_sellers ya aplicados arriba.
# ─────────────────────────────────────────────────────────────────────────────
df_events = load_landing_events()
df_logs = load_landing_logs()

# df_apro ya viene filtrado por variante + fecha_aprobado + nid + equipo
df_section3 = df_apro

st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
st.markdown("<h2>Distribución por variante</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

v_counts = (
    df_section3.drop_duplicates("nid")["abc_test_landing_co"]
    .value_counts().reindex(sel_variants).fillna(0).astype(int).reset_index()
)
v_counts.columns = ["Variante", "N"]

col_pie, col_pie_int = st.columns(2)
with col_pie:
    if v_counts["N"].sum() == 0:
        st.info("Sin deals para las variantes seleccionadas.")
    else:
        fig_pie = go.Figure(go.Pie(
            labels=v_counts["Variante"], values=v_counts["N"],
            hole=0.42, marker_colors=[PRIMARY, ACCENT, LIGHT][:len(v_counts)],
            textinfo="label+percent+value", textfont_size=12,
        ))
        fig_pie.update_layout(
            paper_bgcolor=WHITE, showlegend=False,
            title=dict(text="Variante · universo del experimento",
                       font=dict(size=13, color=DEEP)),
            height=320, margin=dict(l=5, r=5, t=44, b=5),
        )
        st.plotly_chart(fig_pie, use_container_width=True, key="pie_variante")

# Interacciones por UUID — cruce variante × eventos BQ
with col_pie_int:
    eventos_por_var = []
    for v in sel_variants:
        sub_deals = df_section3[df_section3["abc_test_landing_co"] == v]
        uuids_v = set(sub_deals["deal_uuid"].dropna().astype(str).str.lower())
        eventos_v = df_events[df_events["uuid"].isin(uuids_v)]["events"].sum() if not df_events.empty else 0
        eventos_por_var.append({"Variante": v, "Eventos": int(eventos_v)})
    g_ev = pd.DataFrame(eventos_por_var)
    if g_ev["Eventos"].sum() == 0:
        st.info("Sin eventos en BQ para las variantes seleccionadas.")
    else:
        fig_ev = go.Figure(go.Bar(
            x=g_ev["Variante"], y=g_ev["Eventos"],
            marker_color=[PRIMARY, ACCENT, LIGHT][:len(g_ev)],
            text=g_ev["Eventos"], textposition="outside",
        ))
        fig_ev.update_layout(
            paper_bgcolor=WHITE, plot_bgcolor=WHITE,
            title=dict(text="Eventos de landing por variante",
                       font=dict(size=13, color=DEEP)),
            font=dict(family="Inter, sans-serif", color=DEEP, size=11),
            height=320, margin=dict(l=10, r=10, t=44, b=10),
            xaxis=dict(title="Variante", gridcolor="#ede8f5",
                       type="category", categoryorder="array", categoryarray=sel_variants),
            yaxis=dict(title="N eventos", gridcolor="#ede8f5"),
        )
        st.plotly_chart(fig_ev, use_container_width=True, key="bar_eventos_var")


st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
st.markdown("<h2>Funnel de usabilidad de la landing</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

# Funnel: solo 2 etapas. NO cruza con el universo filtrado del dashboard —
# usa los totales del template y del Sheet. Solo respeta el rango de fechas
# vía created_at (envíos) y timestamp (sheet logs).
# - Enviados = nids del template WhatsApp con message_status IN ('read','delivered')
# - Interacciones = UUIDs únicos en el Sheet LOGS con ofertas.habi.co
df_envios = load_envios_wa()

# Para el cruce de la sección "Comportamiento en la landing" (más abajo)
# sí se mantiene el universo A/B/C porque ahí queremos comparar variantes.
df_section3_abc = df_section3[df_section3["abc_test_landing_co"].isin(["A","B","C"])]
universe_uuids_abc = set(df_section3_abc["deal_uuid"].dropna().astype(str).str.lower())

# Enviados: respeta solo el rango de fechas sobre created_at
if not df_envios.empty:
    enviados_fechas = pd.to_datetime(df_envios["created_at"], errors="coerce")
    in_range = (enviados_fechas >= ts_from) & (enviados_fechas <= ts_to + pd.Timedelta(days=1))
    envios_filtered = df_envios[in_range | enviados_fechas.isna()]
    n_enviados = int(envios_filtered["nid"].dropna().nunique())
else:
    n_enviados = 0

# Interacciones: UUIDs únicos del Sheet LOGS ofertas.habi.co en el rango
if not df_logs.empty:
    if "timestamp" in df_logs.columns:
        logs_fechas = pd.to_datetime(df_logs["timestamp"], errors="coerce", utc=True).dt.tz_localize(None)
        in_range_logs = (logs_fechas >= ts_from) & (logs_fechas <= ts_to + pd.Timedelta(days=1))
        logs_filtered = df_logs[in_range_logs | logs_fechas.isna()]
    else:
        logs_filtered = df_logs
    n_interacciones = int(logs_filtered["uuid"].dropna().nunique())
else:
    n_interacciones = 0

f_labels = ["Enviados", "Interacciones"]
f_vals = [n_enviados, n_interacciones]
f_colors = [DEEP, PRIMARY]
f_text = [f"{v:,}" for v in f_vals]
nonzero = [v for v in f_vals if v > 0]
use_log_f = (max(f_vals) if f_vals else 0) > 100 and (min(nonzero) if nonzero else 0) > 0
fig_funnel = go.Figure(go.Bar(
    x=f_vals, y=f_labels, orientation="h",
    marker_color=f_colors, text=f_text,
    textposition="outside", textfont=dict(size=12, color=DEEP),
))
fig_funnel.update_layout(
    paper_bgcolor=WHITE, plot_bgcolor=WHITE,
    font=dict(family="Inter, sans-serif", color=DEEP, size=11),
    height=240, margin=dict(l=10, r=80, t=10, b=10),
    xaxis=dict(type="log" if use_log_f else "linear",
               title="Clientes" + (" (log)" if use_log_f else ""),
               gridcolor="#ede8f5", tickformat=",d"),
    yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig_funnel, use_container_width=True, key="funnel_landing")


# Análisis de eventos Segment de la landing
st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
st.markdown("<h2>Comportamiento en la landing</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

df_tracks = load_landing_tracks()

if df_tracks.empty or not universe_uuids_abc:
    st.info("Sin eventos Segment para el universo seleccionado.")
else:
    tracks_universe = df_tracks[df_tracks["uuid"].isin(universe_uuids_abc)]
    if tracks_universe.empty:
        st.info("Sin eventos Segment para el universo seleccionado.")
    else:
        # UUIDs únicos por evento (cuántos clientes hicieron cada acción)
        uuids_por_evento = (
            tracks_universe.groupby("event_name")["uuid"].nunique().sort_values(ascending=False)
        )
        total_uuids = int(tracks_universe["uuid"].nunique())

        # KPIs de comportamiento (% del que entró que llegó a cada hito)
        def _pct_uuids_que(event_substr: list[str]) -> int:
            """N UUIDs que dispararon AL MENOS UNO de los eventos del set."""
            mask = pd.Series(False, index=tracks_universe.index)
            for s in event_substr:
                mask |= tracks_universe["event_name"].str.contains(s, case=False, na=False)
            return int(tracks_universe.loc[mask, "uuid"].nunique())

        n_entraron = _pct_uuids_que(["page_view_landing"]) or total_uuids
        n_scroll_50 = _pct_uuids_que(["scroll_50"])
        n_scroll_75 = _pct_uuids_que(["scroll_75"])
        n_scroll_100 = _pct_uuids_que(["scroll_100"])
        n_compar = _pct_uuids_que(["comparable_selected"])
        n_cta_venta = _pct_uuids_que(["cta_continuar_venta"])
        n_asesor = _pct_uuids_que(["cta_hablar_asesor", "whatsapp_click"])
        pct = lambda n: f"{n/n_entraron*100:.0f}%" if n_entraron else "0%"

        KPI_EVENTS = [
            ("entraron", "Entraron landing", n_entraron, "page_view_landing"),
            ("scroll50", "Scroll 50%", f"{n_scroll_50} ({pct(n_scroll_50)})", "del que entró"),
            ("scroll75", "Scroll 75%", f"{n_scroll_75} ({pct(n_scroll_75)})", "del que entró"),
            ("scroll100", "Scroll 100%", f"{n_scroll_100} ({pct(n_scroll_100)})", "del que entró"),
            ("vio_compar", "Vio comparables",
             _pct_uuids_que(["section_viewed_comparables"]), "section_viewed_comparables"),
            ("eligio_compar", "Eligió comparable", n_compar, "comparable_selected"),
            ("cta_venta", "Click CTA venta", n_cta_venta, "cta_continuar_venta"),
            ("asesor", "Habló con asesor", n_asesor, "whatsapp / cta_asesor"),
        ]
        SLUG_TO_LABEL = {slug: label for slug, label, _, _ in KPI_EVENTS}
        SLUG_TO_EVENTS = {
            "entraron": ["page_view_landing"],
            "scroll50": ["scroll_50"],
            "scroll75": ["scroll_75"],
            "scroll100": ["scroll_100"],
            "vio_compar": ["section_viewed_comparables"],
            "eligio_compar": ["comparable_selected"],
            "cta_venta": ["cta_continuar_venta"],
            "asesor": ["cta_hablar_asesor", "whatsapp_click"],
        }

        # Estado del filtro en session_state (no usa query_params, así el
        # rerun es soft — Streamlit nativo, no full navigate).
        if "kpi_filter_slug" not in st.session_state:
            st.session_state["kpi_filter_slug"] = None
        active_kpi_slug = st.session_state["kpi_filter_slug"]
        st.session_state["kpi_filter"] = SLUG_TO_LABEL.get(active_kpi_slug)

        # Calcular subset de UUIDs que dispararon el evento del filtro.
        kpi_uuids_filter = None
        if active_kpi_slug in SLUG_TO_EVENTS and not df_tracks.empty:
            _target_events = SLUG_TO_EVENTS[active_kpi_slug]
            _mask = pd.Series(False, index=df_tracks.index)
            for ev in _target_events:
                _mask |= df_tracks["event_name"].str.contains(ev, case=False, na=False)
            kpi_uuids_filter = set(df_tracks.loc[_mask, "uuid"].dropna().astype(str).str.lower())
        st.session_state["kpi_uuids_filter"] = kpi_uuids_filter

        # CSS para las cards: visual idéntica a kpi_card + el button pequeño
        # debajo de cada card. Cuando la card está activa, se resalta.
        st.markdown(f"""
        <style>
        .kcard-wrap {{ margin-bottom: 4px; }}
        .kcard-wrap .kcard {{ min-height: 88px; }}
        .kcard-wrap.active .kcard {{
            border-left: 4px solid {DEEP};
            outline: 2px solid {PRIMARY};
            outline-offset: -2px;
        }}
        .kcard-wrap.active .kval {{ color: {DEEP}; }}
        /* Botoncitos pequeños debajo de cada card */
        section[data-testid="stMain"] div[data-testid="stButton"] > button {{
            padding: 4px 12px !important;
            min-height: 32px !important;
            font-size: 0.78rem !important;
            border-radius: 6px !important;
            border: 1px solid {PALE} !important;
            color: {PRIMARY} !important;
            background: {WHITE} !important;
            font-weight: 600 !important;
        }}
        section[data-testid="stMain"] div[data-testid="stButton"] > button:hover {{
            border-color: {PRIMARY} !important;
            background: {PALE} !important;
        }}
        section[data-testid="stMain"] div[data-testid="stButton"] > button[kind="primary"] {{
            background: {PRIMARY} !important;
            color: #ffffff !important;
            border-color: {PRIMARY} !important;
        }}
        section[data-testid="stMain"] div[data-testid="stButton"] > button[kind="primary"] *,
        section[data-testid="stMain"] div[data-testid="stButton"] > button[kind="primary"] p,
        section[data-testid="stMain"] div[data-testid="stButton"] > button[kind="primary"] div {{
            color: #ffffff !important;
        }}
        section[data-testid="stMain"] div[data-testid="stButton"] > button[kind="primary"]:hover {{
            background: {DEEP} !important;
            border-color: {DEEP} !important;
            color: #ffffff !important;
        }}
        </style>
        """, unsafe_allow_html=True)

        def _render_kpi(col, slug, label, value, sub):
            is_active = active_kpi_slug == slug
            active_cls = " active" if is_active else ""
            with col:
                st.markdown(
                    f"<div class='kcard-wrap{active_cls}'>"
                    f"<div class='kcard'>"
                    f"<div class='kval'>{value}</div>"
                    f"<div class='klbl'>{label}</div>"
                    f"<div class='ksub'>{sub}</div>"
                    f"</div></div>",
                    unsafe_allow_html=True,
                )
                btn_label = "✕ Quitar filtro" if is_active else "Filtrar Desglose"
                if st.button(
                    btn_label, key=f"kpi_btn_{slug}",
                    type="primary" if is_active else "secondary",
                    use_container_width=True,
                ):
                    st.session_state["kpi_filter_slug"] = None if is_active else slug
                    st.rerun()

        # Fila 1
        cols1 = st.columns(4)
        for col, (slug, lbl, val, sub) in zip(cols1, KPI_EVENTS[:4]):
            _render_kpi(col, slug, lbl, val, sub)

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        # Fila 2
        cols2 = st.columns(4)
        for col, (slug, lbl, val, sub) in zip(cols2, KPI_EVENTS[4:]):
            _render_kpi(col, slug, lbl, val, sub)

        if st.session_state.get("kpi_filter"):
            st.caption(
                f"Filtro activo en TODAS las secciones de abajo: nids cuyos "
                f"UUIDs dispararon **{st.session_state['kpi_filter']}**."
            )

        # Aplicar el filtro de KPI a tracks_universe para las gráficas y la
        # tabla de eventos. Si no hay filtro, tracks_universe queda como está.
        if kpi_uuids_filter is not None:
            tracks_universe = tracks_universe[
                tracks_universe["uuid"].isin(kpi_uuids_filter)
            ]
            uuids_por_evento = (
                tracks_universe.groupby("event_name")["uuid"].nunique()
                .sort_values(ascending=False)
            )

        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

        # Distribución de eventos: cuántos UUIDs únicos dispararon cada evento
        col_dist1, col_dist2 = st.columns(2)
        with col_dist1:
            top_events = uuids_por_evento.head(15).sort_values(ascending=True)
            fig_top = go.Figure(go.Bar(
                x=top_events.values, y=top_events.index, orientation="h",
                marker_color=PRIMARY, text=top_events.values, textposition="outside",
            ))
            fig_top.update_layout(
                paper_bgcolor=WHITE, plot_bgcolor=WHITE,
                title=dict(text="UUIDs únicos por evento (top 15)",
                           font=dict(size=13, color=DEEP)),
                font=dict(family="Inter, sans-serif", color=DEEP, size=11),
                height=max(380, len(top_events) * 24 + 80),
                margin=dict(l=10, r=60, t=44, b=10),
                xaxis=dict(title="UUIDs", gridcolor="#ede8f5"),
                yaxis=dict(gridcolor="#ede8f5"),
            )
            st.plotly_chart(fig_top, use_container_width=True, key="dist_eventos_top")

        with col_dist2:
            # Profundidad de scroll: % UUIDs por nivel
            scroll_levels = ["scroll_25", "scroll_50", "scroll_75", "scroll_90", "scroll_100"]
            scroll_data = [
                {"Nivel": lvl.replace("scroll_", "") + "%",
                 "UUIDs": _pct_uuids_que([lvl])}
                for lvl in scroll_levels
            ]
            sd = pd.DataFrame(scroll_data)
            sd["%"] = sd["UUIDs"] / max(n_entraron, 1) * 100
            fig_scroll = go.Figure(go.Bar(
                x=sd["Nivel"], y=sd["UUIDs"],
                marker_color=[PRIMARY, MED, ACCENT, LIGHT, "#16a34a"],
                text=[f"{n} ({p:.0f}%)" for n, p in zip(sd["UUIDs"], sd["%"])],
                textposition="outside",
            ))
            fig_scroll.update_layout(
                paper_bgcolor=WHITE, plot_bgcolor=WHITE,
                title=dict(text="Profundidad de scroll (UUIDs)",
                           font=dict(size=13, color=DEEP)),
                font=dict(family="Inter, sans-serif", color=DEEP, size=11),
                height=380, margin=dict(l=10, r=10, t=44, b=10),
                xaxis=dict(title="Profundidad", gridcolor="#ede8f5"),
                yaxis=dict(title="UUIDs únicos", gridcolor="#ede8f5"),
            )
            st.plotly_chart(fig_scroll, use_container_width=True, key="dist_scroll")

        # Tabla detallada de eventos
        with st.expander("Tabla · todos los eventos (UUIDs únicos)", expanded=False):
            tabla_eventos = uuids_por_evento.reset_index()
            tabla_eventos.columns = ["Evento", "UUIDs únicos"]
            tabla_eventos["% del universo"] = (
                tabla_eventos["UUIDs únicos"] / max(n_entraron, 1) * 100
            ).round(1).astype(str) + "%"
            st.dataframe(tabla_eventos, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Sección 4 · Tabla desglose
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
# Si hay un KPI seleccionado, filtrar df_apro a los nids cuyos deal_uuid
# dispararon ese evento en BQ tracks. Eso permite hacer drill-down desde
# los KPIs de "Comportamiento en la landing" directo al Desglose.
_kpi_active = st.session_state.get("kpi_filter")
_kpi_uuids = st.session_state.get("kpi_uuids_filter")
_df_apro_view = df_apro.copy()
if _kpi_active and _kpi_uuids is not None:
    _df_apro_view = _df_apro_view[
        _df_apro_view["deal_uuid"].astype(str).str.lower().isin(_kpi_uuids)
    ]

_desglose_title = f"Desglose ({len(_df_apro_view):,})"
if _kpi_active:
    _desglose_title += f" · filtrado por «{_kpi_active}»"
st.markdown(f"<h2>{_desglose_title}</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

table = _df_apro_view.copy()
table["Cierre"] = table["fecha_cierre_efectiva"].notna().map({True: "Sí", False: "No"})

# "Veces abrió landing" = nº de filas del Sheet LOGS para ese deal_uuid.
# Cada fila del Sheet corresponde a un envío/apertura del link.
if not df_logs.empty and "uuid" in df_logs.columns:
    aperturas_por_uuid = df_logs["uuid"].value_counts().to_dict()
else:
    aperturas_por_uuid = {}
table["Veces abrió landing"] = (
    table["deal_uuid"].astype(str).str.lower().map(aperturas_por_uuid).fillna(0).astype(int)
)

show_cols = [
    ("abc_test_landing_co", "abc_test_landing_co"),
    ("nid", "nid"),
    ("equipo_sellers", "equipo_sellers"),
    ("hubspot_owner_id", "hubspot_owner_id"),
    ("owner_name", "Propietario"),
    ("estado_aprobado", "estado_aprobado"),
    ("fecha_aprobado", "fecha_aprobado"),
    ("Cierre", "Cierre"),
    ("fecha_cierre", "fecha_cierre"),
    ("fue_ofertado", "fue_ofertado"),
    ("Veces abrió landing", "Veces abrió landing"),
    ("categoria_ancla", "Categoria ancla"),
]
table_view = table[[c for c, _ in show_cols]].rename(columns=dict(show_cols))

# Color escala rojo (pocas aperturas) → verde (muchas) en la columna
# "Veces abrió landing". Sin matplotlib (no instalado en cloud).
_max_val = table_view["Veces abrió landing"].max() if not table_view.empty else 0
_max_aperturas = max(int(_max_val) if pd.notna(_max_val) else 0, 1)

def _color_aperturas(v):
    try:
        n = int(v)
    except Exception:
        return ""
    if n <= 0:
        return "background-color:#fef2f2;color:#7f1d1d"  # rojo muy claro
    ratio = min(n / _max_aperturas, 1.0)
    # Interpolar rojo (#dc2626) → amarillo (#f59e0b) → verde (#16a34a)
    if ratio < 0.5:
        # rojo → amarillo
        t = ratio / 0.5
        r, g, b = int(220 - (220 - 245) * t), int(38 + (158 - 38) * t), int(38 + (11 - 38) * t)
    else:
        # amarillo → verde
        t = (ratio - 0.5) / 0.5
        r, g, b = int(245 - (245 - 22) * t), int(158 + (163 - 158) * t), int(11 + (74 - 11) * t)
    text_color = "#fff" if ratio > 0.4 else "#222"
    return f"background-color:rgb({r},{g},{b});color:{text_color};font-weight:600"

styled = table_view.style.map(_color_aperturas, subset=["Veces abrió landing"])
st.dataframe(styled, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Sección 5 · Matriz por propietario del negocio
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
st.markdown("<h2>Matriz por propietario</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

# Universo: deals del Desglose (df_apro, ya filtrado por sidebar + KPI si aplica)
if _df_apro_view.empty:
    st.info("Sin deals para el filtro actual.")
else:
    owners_base = _df_apro_view.drop_duplicates("nid").copy()
    owners_base["owner_id"] = owners_base["hubspot_owner_id"].astype(str)
    owners_base["owner_label"] = (
        owners_base["owner_name"].fillna("").astype(str).str.strip()
        .replace("", None)
        .fillna(owners_base["owner_id"])
    )
    owners_base.loc[owners_base["owner_id"].isin(["", "nan", "None", "NaN"]),
                    "owner_label"] = "(sin propietario)"

    # Cruces con eventos / sheet
    deal_uuid_lower = df_apro["deal_uuid"].astype(str).str.lower()
    uuid_to_owner = dict(zip(deal_uuid_lower, owners_base.set_index("nid").loc[
        df_apro["nid"].map(lambda x: x if x in owners_base["nid"].values else None).dropna()
    ]["owner_label"].values if not owners_base.empty else []))

    # Forma más simple: agrupar manualmente
    owner_rows = []
    for owner_label, g_own in owners_base.groupby("owner_label"):
        nids = g_own["nid"].tolist()
        uuids = set(g_own["deal_uuid"].dropna().astype(str).str.lower())
        n_negocios = len(g_own)
        # cuántos UUIDs abrieron (al menos 1 evento page en BQ)
        if not df_events.empty:
            n_abrieron = int(df_events[df_events["uuid"].isin(uuids)]["uuid"].nunique())
            n_interacciones = int(df_events[df_events["uuid"].isin(uuids)]["events"].sum())
        else:
            n_abrieron = 0
            n_interacciones = 0
        owner_rows.append({
            "Propietario": owner_label,
            "Negocios con landing": n_negocios,
            "Abrieron landing": n_abrieron,
            "Interacciones": n_interacciones,
            "% abrió": round(n_abrieron / n_negocios * 100, 1) if n_negocios else 0.0,
        })

    matriz = pd.DataFrame(owner_rows).sort_values("Negocios con landing", ascending=False)
    matriz.reset_index(drop=True, inplace=True)

    if matriz.empty:
        st.info("Sin propietarios para el filtro actual.")
    else:
        # Coloreo verde (bueno) → rojo (malo) por % abrió
        def _color_pct(val):
            try:
                p = float(val)
            except Exception:
                return ""
            # Verde si >50%, amarillo 25-50%, rojo <25%
            if p >= 50:
                return "background-color:#16a34a;color:#fff;font-weight:600"
            if p >= 25:
                return "background-color:#f59e0b;color:#fff;font-weight:600"
            return "background-color:#dc2626;color:#fff;font-weight:600"

        # Color por cantidad de interacciones: gradiente PALE→PRIMARY
        max_int = max(int(matriz["Interacciones"].max()), 1)

        def _color_interacciones(val):
            try:
                n = int(val)
            except Exception:
                return ""
            if n == 0:
                return "background-color:#fef2f2;color:#7f1d1d"
            ratio = min(n / max_int, 1.0)
            # Interpolar PALE (#E0AAFF) → PRIMARY (#4A148C)
            r = int(224 + (74 - 224) * ratio)
            g = int(170 + (20 - 170) * ratio)
            b = int(255 + (140 - 255) * ratio)
            text = "#fff" if ratio > 0.5 else "#222"
            return f"background-color:rgb({r},{g},{b});color:{text};font-weight:600"

        # Color por # abrieron (verde si > 0, escala con max_abrieron)
        max_abr = max(int(matriz["Abrieron landing"].max()), 1)

        def _color_abrieron(val):
            try:
                n = int(val)
            except Exception:
                return ""
            if n == 0:
                return "background-color:#fef2f2;color:#7f1d1d"
            ratio = min(n / max_abr, 1.0)
            # rojo → verde
            if ratio < 0.5:
                t = ratio / 0.5
                r = int(220 - (220 - 245) * t)
                g = int(38 + (158 - 38) * t)
                b = int(38 + (11 - 38) * t)
            else:
                t = (ratio - 0.5) / 0.5
                r = int(245 - (245 - 22) * t)
                g = int(158 + (163 - 158) * t)
                b = int(11 + (74 - 11) * t)
            text = "#fff" if ratio > 0.4 else "#222"
            return f"background-color:rgb({r},{g},{b});color:{text};font-weight:600"

        styled_matriz = (
            matriz.style
            .map(_color_abrieron, subset=["Abrieron landing"])
            .map(_color_interacciones, subset=["Interacciones"])
            .map(_color_pct, subset=["% abrió"])
            .format({"% abrió": "{:.1f}%"})
        )
        st.dataframe(styled_matriz, hide_index=True, use_container_width=True)
        st.caption(
            f"{len(matriz)} propietarios · "
            f"{int(matriz['Negocios con landing'].sum())} negocios totales · "
            f"{int(matriz['Abrieron landing'].sum())} abrieron · "
            f"{int(matriz['Interacciones'].sum())} interacciones (page views)."
        )


st.divider()
st.caption(
    f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    "TTL cache: 24h. Rango activo: "
    f"{date_from.isoformat()} → {date_to.isoformat()} · Variantes: {', '.join(sel_variants)}."
)
