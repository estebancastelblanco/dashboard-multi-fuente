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

VARIANTS = ["A", "B", "C", "(sin variante)"]
NULL_VARIANT_LABEL = "(sin variante)"
COLOR_APROBADOS = PRIMARY
COLOR_APROBADOS_RTA = ACCENT
COLOR_CIERRE = LIGHT
COLOR_CVR = "#84cc16"           # verde-amarillo
COLOR_CVR_RTA = "#06b6d4"       # cyan


@st.cache_data(ttl=DAY, show_spinner="BigQuery · Oferta formal MX…", persist="disk")
def load_oferta_formal_data_v3() -> pd.DataFrame:
    """v3: agrega columna pipeline desde hubspot.deals."""
    df = bq_src.fetch_abc_test_landing_co()
    for col in ("fecha_aprobado", "fecha_aprobado_semana", "fecha_cierre",
                "v_fecha_promesa", "fecha_cierre_efectiva", "fecha_ofertado"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


# Alias para retro-compat con resto del código
load_abc_data = load_oferta_formal_data_v3


@st.cache_data(ttl=DAY, show_spinner="BigQuery · eventos landing…", persist="disk")
def load_landing_events() -> pd.DataFrame:
    """Eventos en https://ofertas.tuhabi.mx/<uuid>: una fila por UUID."""
    try:
        return bq_src.fetch_oferta_formal_landing_events()
    except Exception as exc:
        st.warning(f"BigQuery eventos landing: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["uuid", "events", "first_seen", "last_seen"])


@st.cache_data(ttl=DAY, show_spinner="BigQuery · envíos WhatsApp…", persist="disk")
def load_envios_wa() -> pd.DataFrame:
    """Envíos de WhatsApp del template de Oferta formal MX."""
    try:
        return bq_src.fetch_oferta_formal_envios_wa()
    except Exception as exc:
        st.warning(f"BigQuery envíos WA: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["nid", "message_status", "created_at"])


LANDING_SHEET_ID = "1_EMQesd_n67wSqReYaTdJtSd3uvZsb7GXPRD6LyrJN4"
import re
_UUID_RX = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)


@st.cache_data(ttl=120, show_spinner="Sheets · logs de envíos…")
def load_landing_logs() -> pd.DataFrame:
    """Filas del Sheet LOGS donde la URL apunta a ofertas.tuhabi.mx."""
    try:
        from src.sources import gsheets as gs_src
        df = gs_src.fetch_tab("LOGS", sheet_id=LANDING_SHEET_ID)
    except Exception as exc:
        st.warning(f"Sheets logs: {type(exc).__name__}: {exc}")
        return pd.DataFrame()
    if df.empty:
        return df
    mask = df.get("base_url", pd.Series(dtype=str)).astype(str).str.contains(
        "ofertas.tuhabi.mx", case=False, na=False
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

# Mapeo de pipeline_id → label legible. Solo nos interesan los 2 pipelines
# operativos del experimento; cualquier otro pipeline queda como "(otro)"
# y por default no se muestra.
PIPELINE_LABELS = {
    "731899270": "Sellers - Market Maker MX (NUEVO)",
    "638550350": "Nuevo - Inmobiliaria MX",
}
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
        for loader in (load_abc_data, load_landing_events, load_landing_logs, load_envios_wa):
            try:
                loader.clear()
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
        "variantes", VARIANTS, default=["B", "C"],
        label_visibility="collapsed",
        help="Por defecto B vs C (las que el experimento compara). A queda residual.",
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
        help="Filtra por equipo_sellers de detalle_ofertas_mx.",
    )

    st.markdown("### Pipeline")
    pipeline_opts = list(PIPELINE_LABELS.values())
    sel_pipelines = st.multiselect(
        "pipelines", pipeline_opts, default=pipeline_opts,
        label_visibility="collapsed",
        help="Solo los 2 pipelines operativos: Sellers MM MX (NUEVO) e Inmobiliaria MX (NUEVO).",
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
# Pipeline siempre se aplica: default = los 2 operativos, excluye "(otro)"
df = df[df["pipeline_label"].isin(sel_pipelines)].copy()


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

# Funnel: solo 2 etapas
# - Enviados = nids del template WhatsApp ∩ universo donde abc IN ('A','B','C')
#   (excluye "(sin variante)" — solo cuenta deals con variante asignada)
# - Interacciones = registros únicos en el Sheet LOGS (UUIDs únicos en el log)
df_envios = load_envios_wa()
# universo restringido a variantes reales A/B/C dentro del rango/nid/equipo
df_section3_abc = df_section3[df_section3["abc_test_landing_co"].isin(["A","B","C"])]
universe_nids_abc = set(df_section3_abc["nid"].dropna().astype(int).tolist())
universe_uuids_abc = set(df_section3_abc["deal_uuid"].dropna().astype(str).str.lower())

if not df_envios.empty and universe_nids_abc:
    envios_in_universe = df_envios[df_envios["nid"].isin(universe_nids_abc)]
    n_enviados = int(envios_in_universe["nid"].dropna().nunique())
else:
    n_enviados = 0

if not df_logs.empty:
    logs_in_universe = df_logs[df_logs["uuid"].isin(universe_uuids_abc)] if universe_uuids_abc else df_logs
    n_interacciones = int(logs_in_universe["uuid"].nunique())
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


# KPIs y distribución de aperturas por UUID
st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
st.markdown("<h2>Interacciones por cliente</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

# Interacciones desde el Sheet LOGS (cada fila = 1 interacción registrada).
# Filtramos a UUIDs del universo con variante A/B/C.
if df_logs.empty or not universe_uuids_abc:
    st.info("Sin interacciones en el Sheet para el universo seleccionado.")
else:
    logs_universe = df_logs[df_logs["uuid"].isin(universe_uuids_abc)]
    counts_per_uuid = logs_universe["uuid"].value_counts()
    n_uuids = int(len(counts_per_uuid))
    n_total = int(counts_per_uuid.sum())
    k1, k2, k3, k4 = st.columns(4)
    k1.markdown(kpi_card("UUIDs con interacciones", n_uuids,
                         "aparecen al menos 1 vez en el Sheet"),
                unsafe_allow_html=True)
    k2.markdown(kpi_card("Interacciones totales", n_total, "filas del Sheet LOGS"),
                unsafe_allow_html=True)
    k3.markdown(kpi_card("Promedio", f"{counts_per_uuid.mean():.1f}" if n_uuids else "0",
                         "interacciones por UUID"),
                unsafe_allow_html=True)
    k4.markdown(kpi_card("Mediana", f"{counts_per_uuid.median():.0f}" if n_uuids else "0",
                         "interacciones por UUID"),
                unsafe_allow_html=True)

    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
    # Distribución sin buckets: para cada N exacto (1, 2, 3, ...), cuántos UUIDs
    dist_series = counts_per_uuid.value_counts().sort_index()
    dist_series.index = dist_series.index.astype(int)
    dist = pd.DataFrame({
        "Veces que abrió": dist_series.index.astype(int),
        "UUIDs": dist_series.values.astype(int),
    })

    fig_dist = go.Figure(go.Bar(
        x=dist["Veces que abrió"].astype(str), y=dist["UUIDs"],
        marker_color=PRIMARY, text=dist["UUIDs"], textposition="outside",
    ))
    fig_dist.update_layout(
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=11),
        title=dict(text="Distribución de interacciones por cliente",
                   font=dict(size=13, color=DEEP)),
        height=360, margin=dict(l=10, r=10, t=44, b=10),
        xaxis=dict(title="N° exacto de interacciones", gridcolor="#ede8f5",
                   type="category"),
        yaxis=dict(title="UUIDs", gridcolor="#ede8f5"),
    )
    st.plotly_chart(fig_dist, use_container_width=True, key="dist_aperturas")


# ─────────────────────────────────────────────────────────────────────────────
# Sección 4 · Tabla desglose
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
st.markdown(f"<h2>Desglose ({len(df_apro):,})</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

table = df_apro.copy()
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

st.divider()
st.caption(
    f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    "TTL cache: 24h. Rango activo: "
    f"{date_from.isoformat()} → {date_to.isoformat()} · Variantes: {', '.join(sel_variants)}."
)
