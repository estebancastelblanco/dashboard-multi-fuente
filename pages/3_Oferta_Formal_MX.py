"""Oferta formal MX — A/B/C sobre conversión aprobado → cierre."""
from __future__ import annotations

import math
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
def load_oferta_formal_data_v7() -> pd.DataFrame:
    """v7: añade análisis por variante (ABC) y periodicidad + dedup de insights.
    Bump de versión para refrescar el cache en disco de Streamlit Cloud."""
    df = bq_src.fetch_abc_test_landing_co()
    for col in ("fecha_aprobado", "fecha_aprobado_semana", "fecha_cierre",
                "v_fecha_promesa", "fecha_cierre_efectiva", "fecha_ofertado"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


# Alias para retro-compat con resto del código
load_abc_data = load_oferta_formal_data_v7
load_oferta_formal_data_v6 = load_oferta_formal_data_v7  # alias legacy
load_oferta_formal_data_v5 = load_oferta_formal_data_v7  # alias legacy
load_oferta_formal_data_v4 = load_oferta_formal_data_v7  # alias legacy


@st.cache_data(ttl=DAY, show_spinner="BigQuery · eventos landing…", persist="disk")
def load_landing_events() -> pd.DataFrame:
    """Eventos en https://ofertas.tuhabi.mx/<uuid>: una fila por UUID."""
    try:
        return bq_src.fetch_oferta_formal_landing_events()
    except Exception as exc:
        st.warning(f"BigQuery eventos landing: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["uuid", "events", "first_seen", "last_seen"])


@st.cache_data(ttl=DAY, show_spinner="BigQuery · eventos Segment landing…", persist="disk")
def load_landing_tracks() -> pd.DataFrame:
    """Eventos individuales (Segment tracks) en ofertas.tuhabi.mx."""
    try:
        return bq_src.fetch_oferta_formal_landing_tracks()
    except Exception as exc:
        st.warning(f"BigQuery tracks: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["uuid", "event_name", "timestamp"])


@st.cache_data(ttl=DAY, show_spinner="BigQuery · envíos WhatsApp…", persist="disk")
def load_envios_wa_v2() -> pd.DataFrame:
    """v2: filtrado a message_status IN ('read','delivered')."""
    try:
        return bq_src.fetch_oferta_formal_envios_wa()
    except Exception as exc:
        st.warning(f"BigQuery envíos WA: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["nid", "message_status", "created_at"])


# Alias para retro-compat
load_envios_wa = load_envios_wa_v2


LANDING_SHEET_ID = "1_EMQesd_n67wSqReYaTdJtSd3uvZsb7GXPRD6LyrJN4"
import re
_UUID_RX = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)


@st.cache_data(ttl=120, show_spinner="Sheets · logs de envíos…")
def load_landing_logs() -> pd.DataFrame:
    """Filas del Sheet LOGS donde la URL es de dominio MX (.mx).

    Captura cualquier subdominio .mx (ofertas.tuhabi.mx, etc.).
    """
    try:
        from src.sources import gsheets as gs_src
        df = gs_src.fetch_tab("LOGS", sheet_id=LANDING_SHEET_ID)
    except Exception as exc:
        st.warning(f"Sheets logs: {type(exc).__name__}: {exc}")
        return pd.DataFrame()
    if df.empty:
        return df
    mask = df.get("base_url", pd.Series(dtype=str)).astype(str).str.contains(
        r"\.mx", case=False, na=False, regex=True,
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
# Normalización de las 3 columnas de segmentación (razón de venta, ciudad MX,
# monto del préstamo). Son la materia prima de los filtros nuevos y de la
# sección "Análisis e Insights".
# ─────────────────────────────────────────────────────────────────────────────
RAZON_NA = "(sin razón)"
CIUDAD_NA = "(sin ciudad)"

if "razon_venta" not in df.columns:
    df["razon_venta"] = pd.NA
if "ciudad_mx" not in df.columns:
    df["ciudad_mx"] = pd.NA
if "final_prestamo_mx" not in df.columns:
    df["final_prestamo_mx"] = pd.NA

df["razon_venta"] = (
    df["razon_venta"].astype(str).str.strip()
    .replace({"": RAZON_NA, "nan": RAZON_NA, "None": RAZON_NA, "<NA>": RAZON_NA})
)
df["ciudad_mx"] = (
    df["ciudad_mx"].astype(str).str.strip()
    .replace({"": CIUDAD_NA, "nan": CIUDAD_NA, "None": CIUDAD_NA, "<NA>": CIUDAD_NA})
)
df["final_prestamo_mx"] = pd.to_numeric(df["final_prestamo_mx"], errors="coerce")


# ─────────────────────────────────────────────────────────────────────────────
# Mapeo municipio → Zona Metropolitana (ZM). El análisis de insights se hace a
# nivel de ZM (no de municipio): los municipios sueltos tienen muestras chicas;
# agregarlos por zona conurbada da señal estadística mucho más robusta y
# accionable para zonificar la landing. Basado en las delimitaciones oficiales
# de zonas metropolitanas de México (SEDATU/CONAPO/INEGI).
# ─────────────────────────────────────────────────────────────────────────────
ZONA_NA = "(sin zona)"

MUNICIPIO_A_ZONA: dict[str, str] = {
    # ── ZM Valle de México (CDMX + conurbados Edomex/Hidalgo) ──
    "Ciudad de México": "ZM Valle de México",
    "Huehuetoca": "ZM Valle de México",
    "Zumpango": "ZM Valle de México",
    "Tizayuca": "ZM Valle de México",          # Hidalgo, integrado a ZMVM (2005)
    "Coacalco de Berriozábal": "ZM Valle de México",
    "Tecámac": "ZM Valle de México",
    "Ixtapaluca": "ZM Valle de México",
    "Tultitlán": "ZM Valle de México",
    "Chicoloapan": "ZM Valle de México",
    "Atizapán de Zaragoza": "ZM Valle de México",
    "Cuautitlán": "ZM Valle de México",
    "Cuautitlán Izcalli": "ZM Valle de México",
    "Tultepec": "ZM Valle de México",
    "Nicolás Romero": "ZM Valle de México",
    "Chalco": "ZM Valle de México",
    "Acolman": "ZM Valle de México",
    "Tlalnepantla de Baz": "ZM Valle de México",
    "Nextlalpan": "ZM Valle de México",
    "Naucalpan de Juárez": "ZM Valle de México",
    "Teoloyucan": "ZM Valle de México",
    "Valle de Chalco Solidaridad": "ZM Valle de México",
    "Huixquilucan": "ZM Valle de México",
    "Melchor Ocampo": "ZM Valle de México",
    "Nezahualcóyotl": "ZM Valle de México",
    # ── ZM Guadalajara (Jalisco) ──
    "Zapopan": "ZM Guadalajara",
    "Tlajomulco de Zúñiga": "ZM Guadalajara",
    "San Pedro Tlaquepaque": "ZM Guadalajara",
    "Guadalajara": "ZM Guadalajara",
    "Tonalá": "ZM Guadalajara",
    "El Salto": "ZM Guadalajara",
    "Juanacatlán": "ZM Guadalajara",
    # ── ZM Monterrey (Nuevo León) ──
    "Apodaca": "ZM Monterrey",
    "García": "ZM Monterrey",
    "General Escobedo": "ZM Monterrey",
    "Guadalupe": "ZM Monterrey",
    "Salinas Victoria": "ZM Monterrey",
    "San Nicolás de los Garza": "ZM Monterrey",
    "Monterrey": "ZM Monterrey",
    "Pesquería": "ZM Monterrey",
    "General Zuazua": "ZM Monterrey",
    "Santa Catarina": "ZM Monterrey",
    "El Carmen": "ZM Monterrey",
    "Cadereyta Jiménez": "ZM Monterrey",
    "Ciénega de Flores": "ZM Monterrey",
    "Juárez": "ZM Monterrey",                  # Juárez NL (corredor habitacional)
    # ── ZM Toluca (Edomex) ──
    "Toluca": "ZM Toluca",
    "Metepec": "ZM Toluca",
    "Zinacantepec": "ZM Toluca",
    "San Mateo Atenco": "ZM Toluca",
    "Almoloya de Juárez": "ZM Toluca",
    "Temoaya": "ZM Toluca",
    "Chapultepec": "ZM Toluca",
    "Calimaya": "ZM Toluca",
    "San Antonio la Isla": "ZM Toluca",
    "Lerma": "ZM Toluca",
    # ── ZM León / Bajío (Guanajuato) ──
    "León": "ZM León",
    # ── ZM Querétaro ──
    "Querétaro": "ZM Querétaro",
    "El Marqués": "ZM Querétaro",
    "Corregidora": "ZM Querétaro",
    "Apaseo el Grande": "ZM Querétaro",        # Guanajuato, conurbado a Qro
    # ── ZM Pachuca (Hidalgo) ──
    "Mineral de la Reforma": "ZM Pachuca",
    "Pachuca de Soto": "ZM Pachuca",
    "Zempoala": "ZM Pachuca",
}

df["zona_metropolitana"] = (
    df["ciudad_mx"].map(MUNICIPIO_A_ZONA).fillna(ZONA_NA)
)
# Los municipios sin mapear quedan como "(otra · <municipio>)" para no perderlos
_sin_zona = df["zona_metropolitana"] == ZONA_NA
df.loc[_sin_zona & (df["ciudad_mx"] != CIUDAD_NA), "zona_metropolitana"] = (
    "(otra · " + df.loc[_sin_zona & (df["ciudad_mx"] != CIUDAD_NA), "ciudad_mx"] + ")"
)


def _price_buckets(series: pd.Series, n: int = 5) -> tuple[list[float], list[str]]:
    """Devuelve (edges, labels) para discretizar el monto del préstamo.

    Usa cortes redondeados a 100k que cubren el rango observado de
    `final_prestamo_mx`. Si el rango es degenerado, cae a un único bucket.
    """
    vals = pd.to_numeric(series, errors="coerce").dropna()
    vals = vals[vals > 0]
    if vals.empty:
        return [0, 1], ["(sin monto)"]
    lo, hi = float(vals.min()), float(vals.max())
    if hi <= lo:
        return [0, hi + 1], [f"≤ {hi/1e6:.1f}M"]
    # cortes "bonitos": 0, 600k, 900k, 1.3M, 2M, +∞ por defecto, pero si el
    # rango es chico, usar quintiles redondeados a 100k.
    fixed = [0, 600_000, 900_000, 1_300_000, 2_000_000, float("inf")]
    if hi <= 2_000_000:
        qs = [vals.quantile(q) for q in (0.2, 0.4, 0.6, 0.8)]
        edges = [0] + [round(q / 100_000) * 100_000 for q in qs] + [float("inf")]
        edges = sorted(set(edges))
    else:
        edges = fixed

    def _fmt(x: float) -> str:
        if x == float("inf"):
            return "∞"
        if x >= 1e6:
            return f"{x/1e6:.1f}M".replace(".0M", "M")
        return f"{int(x/1000)}k"

    labels = [f"{_fmt(edges[i])}–{_fmt(edges[i+1])}" for i in range(len(edges) - 1)]
    return edges, labels


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
    default_start = date(2026, 3, 25)
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
        help="Filtra por equipo_sellers de detalle_ofertas_mx.",
    )

    st.markdown("### Pipeline")
    pipeline_opts = list(PIPELINE_LABELS.values()) + ["(otro)"]
    pipeline_default = list(PIPELINE_LABELS.values())  # los 2 operativos
    sel_pipelines = st.multiselect(
        "pipelines", pipeline_opts, default=pipeline_default,
        label_visibility="collapsed",
        help="Default: 2 pipelines operativos. Solo restringe deals con variante "
             "(A/B/C); los '(sin variante)' SIEMPRE pasan (igual que Looker). "
             "Agrega '(otro)' para incluir deals A/B/C con un pipeline distinto "
             "a los 2 operativos.",
    )

    # ── Filtros de segmentación (razón de venta · ciudad · monto préstamo) ──
    # Solo restringen cuando el usuario los mueve del default. El default deja
    # TODO el universo intacto para no romper el cuadre con Looker.
    st.markdown("### Razón de venta")
    razon_opts = sorted(
        df["razon_venta"].dropna().astype(str).unique().tolist()
    )
    sel_razones = st.multiselect(
        "razon_venta", razon_opts, default=razon_opts,
        label_visibility="collapsed",
        help="razon_de_venta_usuario_gabi_mx (Liquidez, Cambio de Casa, Otros). "
             "~44% de los deals la tienen; el resto cae en '(sin razón)'.",
    )

    st.markdown("### Ciudad (MX)")
    ciudad_counts = df["ciudad_mx"].value_counts()
    ciudad_opts = ciudad_counts.index.tolist()  # ordenadas por frecuencia
    sel_ciudades = st.multiselect(
        "ciudad_mx", ciudad_opts, default=ciudad_opts,
        label_visibility="collapsed",
        help="ciudad_mx (municipio). 100% de cobertura en MX. Ordenadas por "
             "número de deals.",
    )

    st.markdown("### Monto del préstamo (final_prestamo_mx)")
    _fp = pd.to_numeric(df["final_prestamo_mx"], errors="coerce")
    _fp_pos = _fp[_fp > 0]
    if not _fp_pos.empty:
        fp_min = int(_fp_pos.min())
        # Tope robusto a outliers: hay un puñado de deals con montos basura
        # (valores de prueba > 10M, p.ej. 933M) que comprimirían el slider.
        # Descartamos lo que esté por encima de p99×2 y redondeamos a 100k.
        _p99 = float(_fp_pos.quantile(0.99))
        _cutoff = _p99 * 2 if _p99 > 0 else float(_fp_pos.max())
        _robust_max = float(_fp_pos[_fp_pos <= _cutoff].max())
        fp_max = int(math.ceil(_robust_max / 100_000) * 100_000)
        # Nunca dejar un rango degenerado (mín 100k de ancho).
        fp_max = max(fp_max, 100_000)
    else:
        # Sin montos disponibles (caché frío o columna ausente): rango nominal
        # que mantiene el slider usable sin romper el layout.
        fp_min, fp_max = 0, 5_000_000
    sel_precio = st.slider(
        "final_prestamo_mx",
        min_value=0,
        max_value=fp_max,
        value=(0, fp_max),
        step=max(fp_max // 100, 1000),
        format="$%d",
        label_visibility="collapsed",
        help="Rango de monto final del préstamo (MXN). Mueve los extremos para "
             "acotar. Por defecto cubre todo el rango (0 → máximo observado, "
             "robusto a outliers). Los deals con monto > tope se incluyen solo "
             "cuando el slider está en su máximo.",
    )
    incluir_sin_monto = st.checkbox(
        "Incluir deals sin monto",
        value=True,
        help="94% de los deals tienen final_prestamo_mx. Desmárcalo para "
             "excluir los que no lo tienen cuando acotas el rango.",
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
# Pipeline: el filtro SOLO restringe deals con variante (A/B/C). Los deals
# "(sin variante)" SIEMPRE pasan para cuadrar con Looker, que no aplica filtro
# de pipeline. (Antes "(sin variante)" coincidía con pipeline NULL; desde que
# el LEFT JOIN a HubSpot trae pipeline para todos los deals, hay que filtrar
# por variante explícitamente, no por pipeline nulo.)
_pipeline_null = df["pipeline"].isna() | (df["pipeline"].astype(str).str.lower().isin(["", "nan", "none"]))
_sin_variante = df["abc_test_landing_co"] == NULL_VARIANT_LABEL
df = df[_sin_variante | _pipeline_null | df["pipeline_label"].isin(sel_pipelines)].copy()

# ── Filtros de segmentación: solo restringen si el usuario los movió del
# default (todo seleccionado / rango completo). Así el universo entero queda
# intacto por defecto y el cuadre con Looker no se rompe.
if sel_razones and len(sel_razones) < len(razon_opts):
    df = df[df["razon_venta"].astype(str).isin(sel_razones)].copy()
if sel_ciudades and len(sel_ciudades) < len(ciudad_opts):
    df = df[df["ciudad_mx"].astype(str).isin(sel_ciudades)].copy()
_lo_precio, _hi_precio = sel_precio
if (_lo_precio, _hi_precio) != (0, fp_max):
    _fp_col = pd.to_numeric(df["final_prestamo_mx"], errors="coerce")
    _in_precio = _fp_col.between(_lo_precio, _hi_precio)
    if incluir_sin_monto:
        df = df[_in_precio | _fp_col.isna()].copy()
    else:
        df = df[_in_precio].copy()


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
# - Interacciones = UUIDs únicos en el Sheet LOGS con dominio .mx
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

# Interacciones: UUIDs únicos del Sheet LOGS con dominio .mx en el rango
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


# ─────────────────────────────────────────────────────────────────────────────
# Sección 6 · Análisis e Insights — segmentación por razón × ciudad × monto
#
# Objetivo: encontrar qué combinaciones de (razón de venta, ciudad MX, rango de
# monto del préstamo) convierten mejor o peor a cierre, con respaldo estadístico
# (z-test de proporciones vs el resto del universo), y proponer una hipótesis +
# un experimento accionable por cada señal fuerte. La idea es zonificar e iterar
# la landing por segmento.
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<div style='height:32px'></div>", unsafe_allow_html=True)
st.markdown("<h2>Análisis e Insights · segmentación</h2>", unsafe_allow_html=True)
st.markdown(
    f"<div style='color:{MED};font-size:0.82rem;margin-bottom:6px'>"
    "Cruce de <b>razón de venta</b> × <b>zona metropolitana</b> × <b>rango de "
    "monto del préstamo</b> sobre la conversión aprobado→cierre (CVR). El "
    "mapeo geográfico es por zona metropolitana (no municipio) para agregar "
    "muestra y dar señal robusta. Solo se reportan los segmentos con señal "
    "estadística vs. el resto del universo. Respeta los filtros del sidebar.</div>",
    unsafe_allow_html=True,
)

# Universo de análisis: deals aprobados en el rango (ya filtrados por sidebar,
# incluyendo los 3 filtros nuevos). Deduplicado por nid; CVR = cierre/aprobado.
_ins_base = df_apro.drop_duplicates("nid").copy()

if _ins_base.empty or len(_ins_base) < 20:
    st.info(
        "Universo insuficiente para el análisis de segmentos "
        f"({len(_ins_base)} deals aprobados). Amplía el rango de fechas o "
        "relaja los filtros."
    )
else:
    _ins_base["cerro"] = _ins_base["fecha_cierre_efectiva"].notna()
    _ins_base["razon_venta"] = _ins_base["razon_venta"].fillna(RAZON_NA).astype(str)
    _ins_base["ciudad_mx"] = _ins_base["ciudad_mx"].fillna(CIUDAD_NA).astype(str)
    if "zona_metropolitana" not in _ins_base.columns:
        _ins_base["zona_metropolitana"] = _ins_base["ciudad_mx"].map(
            MUNICIPIO_A_ZONA).fillna(ZONA_NA)
    _ins_base["zona_metropolitana"] = _ins_base["zona_metropolitana"].fillna(ZONA_NA).astype(str)

    # Buckets de monto sobre el universo activo
    _edges, _labels = _price_buckets(_ins_base["final_prestamo_mx"])
    _fp_num = pd.to_numeric(_ins_base["final_prestamo_mx"], errors="coerce")
    _ins_base["price_bucket"] = pd.cut(
        _fp_num, bins=_edges, labels=_labels, include_lowest=True
    ).astype("object")
    _ins_base["price_bucket"] = _ins_base["price_bucket"].fillna("(sin monto)")

    n_total = len(_ins_base)
    cierre_total = int(_ins_base["cerro"].sum())
    cvr_base = cierre_total / n_total if n_total else 0.0

    # KPIs baseline
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(kpi_card("Aprobados (universo)", f"{n_total:,}"), unsafe_allow_html=True)
    with k2:
        st.markdown(kpi_card("Cierres", f"{cierre_total:,}"), unsafe_allow_html=True)
    with k3:
        st.markdown(kpi_card("CVR baseline", f"{cvr_base*100:.1f}%"), unsafe_allow_html=True)
    with k4:
        st.markdown(kpi_card("Zonas metro activas",
                             f"{_ins_base['zona_metropolitana'].nunique():,}"),
                    unsafe_allow_html=True)

    # ── z-test de dos proporciones (segmento vs. resto) sin scipy ──
    def _two_prop_z(c1: int, n1: int, c2: int, n2: int) -> tuple[float, float]:
        if n1 == 0 or n2 == 0:
            return 0.0, 1.0
        p1, p2 = c1 / n1, c2 / n2
        p_pool = (c1 + c2) / (n1 + n2)
        se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
        if se == 0:
            return 0.0, 1.0
        z = (p1 - p2) / se
        p_val = math.erfc(abs(z) / math.sqrt(2))  # two-sided
        return z, p_val

    MIN_N = 25  # mínimo de aprobados en el segmento para considerarlo

    # ── Heatmaps de CVR ──
    def _heatmap_cvr(rows_col: str, row_order: list[str], title: str, key: str):
        cols_order = [l for l in _labels if l in _ins_base["price_bucket"].unique()]
        if "(sin monto)" in _ins_base["price_bucket"].unique():
            cols_order = cols_order + ["(sin monto)"]
        z_vals, text_vals = [], []
        for r in row_order:
            z_row, t_row = [], []
            for c in cols_order:
                sub = _ins_base[(_ins_base[rows_col] == r) & (_ins_base["price_bucket"] == c)]
                n = len(sub)
                if n < max(MIN_N // 2, 8):
                    z_row.append(None)
                    t_row.append(f"n={n}" if n else "")
                else:
                    cvr = sub["cerro"].mean() * 100
                    z_row.append(cvr)
                    t_row.append(f"{cvr:.0f}%<br>n={n}")
            z_vals.append(z_row)
            text_vals.append(t_row)
        fig = go.Figure(go.Heatmap(
            z=z_vals, x=cols_order, y=row_order, text=text_vals,
            texttemplate="%{text}", textfont=dict(size=10),
            colorscale=[[0, "#fde2e2"], [0.5, "#fff3cd"], [1, "#16a34a"]],
            colorbar=dict(title="CVR %"), hoverongaps=False,
            zmin=0, zmax=max(cvr_base * 200, 10),
        ))
        fig.update_layout(
            paper_bgcolor=WHITE, plot_bgcolor=WHITE,
            title=dict(text=title, font=dict(size=13, color=DEEP)),
            font=dict(family="Inter, sans-serif", color=DEEP, size=11),
            height=max(300, len(row_order) * 46 + 120),
            margin=dict(l=10, r=10, t=46, b=10),
            xaxis=dict(title="Rango de monto del préstamo (MXN)"),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig, use_container_width=True, key=key)
        st.caption(
            "Celdas con muestra < 8 quedan en blanco. CVR = cierre / aprobado."
        )

    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
    razon_order = [r for r in ["Liquidez", "Cambio de Casa", "Otros", RAZON_NA]
                   if r in _ins_base["razon_venta"].unique()]
    _heatmap_cvr("razon_venta", razon_order,
                 "CVR por razón de venta × rango de monto", "heat_razon_precio")

    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    top_zonas = (
        _ins_base[~_ins_base["zona_metropolitana"].isin([ZONA_NA])]
        ["zona_metropolitana"].value_counts().head(8).index.tolist()
    )
    if top_zonas:
        _heatmap_cvr("zona_metropolitana", top_zonas,
                     "CVR por zona metropolitana × rango de monto",
                     "heat_zona_precio")

    # ── Análisis por variante de landing (histórico ABC test) ──
    # A = control sin landing (gestión 100% comercial); B = landing simple;
    # C = landing compleja (configurador tipo Tesla). En MX el experimento dejó
    # 95% B / 5% C tras ganar B; en CO ganó C (100%). Antes corrió el triple
    # A/B/C y hubo un periodo apagado.
    st.markdown("<div style='height:22px'></div>", unsafe_allow_html=True)
    st.markdown("<h3>CVR por variante de landing (ABC test)</h3>", unsafe_allow_html=True)
    LAND_LABEL = {
        "A": "A · control (sin landing)",
        "B": "B · landing simple",
        "C": "C · landing compleja (Tesla)",
        NULL_VARIANT_LABEL: "(sin variante)",
    }
    if "abc_test_landing_co" in _ins_base.columns:
        vr = (_ins_base.groupby("abc_test_landing_co")
              .agg(Aprobados=("nid", "size"), Cierres=("cerro", "sum")))
        vr["CVR %"] = (vr["Cierres"] / vr["Aprobados"] * 100).round(1)
        vr = vr.reset_index()
        vr["Variante"] = vr["abc_test_landing_co"].map(
            lambda x: LAND_LABEL.get(str(x), str(x)))
        vr = vr[["Variante", "Aprobados", "Cierres", "CVR %"]].sort_values(
            "Aprobados", ascending=False)
        cvar1, cvar2 = st.columns([3, 2])
        with cvar1:
            st.dataframe(vr, hide_index=True, use_container_width=True)
        with cvar2:
            fig_v = go.Figure(go.Bar(
                x=vr["Variante"], y=vr["CVR %"], marker_color=PRIMARY,
                text=[f"{v:.1f}%" for v in vr["CVR %"]], textposition="outside",
            ))
            fig_v.update_layout(
                paper_bgcolor=WHITE, plot_bgcolor=WHITE,
                font=dict(family="Inter, sans-serif", color=DEEP, size=10),
                height=240, margin=dict(l=10, r=10, t=10, b=10),
                yaxis=dict(title="CVR %", gridcolor="#ede8f5"),
                xaxis=dict(tickangle=-15),
            )
            st.plotly_chart(fig_v, use_container_width=True, key="bar_variante_cvr")
        st.caption(
            "A = control sin landing · B = landing simple · C = landing compleja "
            "(configurador tipo Tesla). MX dejó 95% B / 5% C tras ganar B; CO usa "
            "100% C. Los aprobados '(sin variante)' son deals previos al test o sin "
            "asignación."
        )

    # ── Periodicidad: CVR por mes de aprobación × variante ──
    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    st.markdown("<h3>Periodicidad — CVR mensual por variante</h3>", unsafe_allow_html=True)
    _per = _ins_base.copy()
    _per["mes"] = pd.to_datetime(_per["fecha_aprobado"], errors="coerce").dt.to_period("M").astype(str)
    _per = _per[_per["mes"] != "NaT"]
    if not _per.empty and "abc_test_landing_co" in _per.columns:
        fig_per = go.Figure()
        _palette = {"A": MED, "B": PRIMARY, "C": ACCENT, NULL_VARIANT_LABEL: "#cbd5e1"}
        for v in ["A", "B", "C", NULL_VARIANT_LABEL]:
            sub = _per[_per["abc_test_landing_co"] == v]
            if sub.empty:
                continue
            gm = sub.groupby("mes").agg(n=("nid", "size"), cvr=("cerro", "mean")).reset_index()
            gm = gm[gm["n"] >= 8]  # ocultar meses con muestra muy chica
            if gm.empty:
                continue
            fig_per.add_trace(go.Scatter(
                x=gm["mes"], y=(gm["cvr"] * 100).round(1),
                mode="lines+markers", name=LAND_LABEL.get(v, v),
                line=dict(color=_palette.get(v, MED), width=3),
                marker=dict(size=9),
                hovertemplate="%{x}<br>CVR: %{y:.1f}%<extra>" + LAND_LABEL.get(v, v) + "</extra>",
            ))
        fig_per.update_layout(
            paper_bgcolor=WHITE, plot_bgcolor=WHITE,
            font=dict(family="Inter, sans-serif", color=DEEP, size=11),
            height=320, margin=dict(l=10, r=10, t=20, b=10),
            yaxis=dict(title="CVR %", gridcolor="#ede8f5", ticksuffix="%"),
            xaxis=dict(title="Mes de aprobación", gridcolor="#ede8f5", type="category"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=10)),
        )
        st.plotly_chart(fig_per, use_container_width=True, key="line_periodicidad")
        st.caption(
            "CVR aprobado→cierre por mes y variante (meses con <8 aprobados ocultos). "
            "Útil para ver cuándo el triple ABC estuvo activo, el periodo apagado y la "
            "estabilidad de la señal de cada variante en el tiempo."
        )

    # ── Escaneo de segmentos: 1, 2 y 3 dimensiones (zona, no municipio) ──
    GROUPINGS = [
        (["razon_venta"], "Razón"),
        (["zona_metropolitana"], "Zona"),
        (["price_bucket"], "Monto"),
        (["razon_venta", "price_bucket"], "Razón × Monto"),
        (["zona_metropolitana", "price_bucket"], "Zona × Monto"),
        (["razon_venta", "zona_metropolitana"], "Razón × Zona"),
        (["razon_venta", "zona_metropolitana", "price_bucket"], "Razón × Zona × Monto"),
    ]

    seg_rows = []
    for dims, fam in GROUPINGS:
        for keys, sub in _ins_base.groupby(dims, dropna=False):
            n1 = len(sub)
            if n1 < MIN_N:
                continue
            c1 = int(sub["cerro"].sum())
            n2 = n_total - n1
            c2 = cierre_total - c1
            cvr_seg = c1 / n1
            lift_pp = (cvr_seg - cvr_base) * 100
            z, p = _two_prop_z(c1, n1, c2, n2)
            keys_t = keys if isinstance(keys, tuple) else (keys,)
            label = " · ".join(str(k) for k in keys_t)
            seg_rows.append({
                "Familia": fam,
                "Segmento": label,
                "dims": dims,
                "vals": keys_t,
                "Aprobados": n1,
                "Cierres": c1,
                "CVR %": round(cvr_seg * 100, 1),
                "vs baseline (pp)": round(lift_pp, 1),
                "p-value": round(p, 4),
                "_absp": abs(lift_pp),
            })

    seg_df = pd.DataFrame(seg_rows)

    st.markdown("<div style='height:22px'></div>", unsafe_allow_html=True)
    st.markdown("<h3>Segmentos con señal estadística</h3>", unsafe_allow_html=True)

    if seg_df.empty:
        st.info(
            f"Ningún segmento alcanza el mínimo de {MIN_N} aprobados con los "
            "filtros actuales."
        )
        sig = pd.DataFrame()
    else:
        # Señal = p < 0.10 (direccional) y lift relevante (>= 5 pp absolutos)
        sig = seg_df[(seg_df["p-value"] < 0.10) & (seg_df["_absp"] >= 5)].copy()
        sig = sig.sort_values(["p-value", "_absp"], ascending=[True, False])

        if sig.empty:
            st.info(
                "Ningún segmento supera el umbral de significancia (p<0.10) y "
                "lift (≥5pp). Muestra los segmentos con mayor desviación abajo."
            )
            show_tbl = seg_df.sort_values("_absp", ascending=False).head(15)
        else:
            show_tbl = sig.head(20)

        def _color_lift(v):
            try:
                p = float(v)
            except Exception:
                return ""
            if p >= 10:
                return "background-color:#16a34a;color:#fff;font-weight:600"
            if p >= 0:
                return "background-color:#dcfce7;color:#166534;font-weight:600"
            if p <= -10:
                return "background-color:#dc2626;color:#fff;font-weight:600"
            return "background-color:#fee2e2;color:#7f1d1d;font-weight:600"

        disp_cols = ["Familia", "Segmento", "Aprobados", "Cierres",
                     "CVR %", "vs baseline (pp)", "p-value"]
        styled_seg = (
            show_tbl[disp_cols].style
            .map(_color_lift, subset=["vs baseline (pp)"])
            .format({"CVR %": "{:.1f}", "vs baseline (pp)": "{:+.1f}",
                     "p-value": "{:.4f}"})
        )
        st.dataframe(styled_seg, hide_index=True, use_container_width=True)
        st.caption(
            f"Baseline CVR = {cvr_base*100:.1f}% sobre {n_total:,} aprobados. "
            "Señal = p<0.10 (z-test de dos proporciones, segmento vs. resto) y "
            "|lift| ≥ 5pp. Verde = convierte mejor; rojo = peor."
        )

    # ── Insights accionables: Hipótesis + Experimento por señal fuerte ──
    st.markdown("<div style='height:26px'></div>", unsafe_allow_html=True)
    st.markdown("<h3>Hipótesis y experimentos propuestos</h3>", unsafe_allow_html=True)

    def _is_actionable(vals: tuple) -> bool:
        bad = {RAZON_NA, CIUDAD_NA, ZONA_NA, "(sin monto)"}
        return not any(str(v) in bad or str(v).startswith("(otra ·") for v in vals)

    def _dedup_overlapping(df_cand: pd.DataFrame) -> pd.DataFrame:
        """Colapsa insights solapados que cuentan la misma historia.

        Un segmento B "explica lo mismo" que A cuando sus valores son un
        superconjunto de los de A, la dirección del lift coincide y el lift es
        similar (el detalle extra no agrega señal nueva). En ese caso preferimos
        el más GENERAL (menos dimensiones) como representante: p. ej.
        «0k–600k» representa a «VdM·0k–600k» y a «Liquidez·VdM·0k–600k» si los
        tres apuntan +~21pp. Así evitamos 3 tarjetas que dicen lo mismo.

        Estrategia: recorrer de más general (menos dims) a más específico;
        descartar un candidato específico si ya existe uno más general aceptado
        cuyo set de valores está contenido en él, misma dirección y |Δlift| ≤ 6pp.
        """
        if df_cand.empty:
            return df_cand
        rows = df_cand.to_dict("records")
        # Orden: primero los más generales (menos dims), luego por |lift| desc
        rows.sort(key=lambda r: (len(r["dims"]), -r["_absp"]))
        kept: list[dict] = []
        for r in rows:
            r_vals = set(str(v) for v in r["vals"])
            r_dir = r["vs baseline (pp)"] > 0
            redundante = False
            for k in kept:
                k_vals = set(str(v) for v in k["vals"])
                same_dir = (k["vs baseline (pp)"] > 0) == r_dir
                # r es más específico si contiene todos los valores del aceptado
                contiene = k_vals.issubset(r_vals) and len(k_vals) < len(r_vals)
                lift_similar = abs(r["vs baseline (pp)"] - k["vs baseline (pp)"]) <= 6
                if contiene and same_dir and lift_similar:
                    redundante = True
                    break
            if not redundante:
                kept.append(r)
        return pd.DataFrame(kept)

    insight_rows = []
    if not seg_df.empty:
        # Solo señales accionables (sin segmentos con dato faltante).
        cand = seg_df[(seg_df["p-value"] < 0.10) & (seg_df["_absp"] >= 5)].copy()
        if not cand.empty:
            # Máscara booleana explícita: si cand quedara vacío, cand["vals"]
            # sería una Serie object vacía y cand[serie] se interpretaría como
            # selección de columnas (df sin columnas → KeyError en sort_values).
            mask_act = cand["vals"].apply(_is_actionable).astype(bool)
            cand = cand[mask_act]
        if not cand.empty:
            # Colapsar solapados ANTES de elegir top: deja un representante por
            # historia (el más general), no 3 que dicen lo mismo.
            cand = _dedup_overlapping(cand)
            cand = cand.sort_values(["_absp", "Aprobados"], ascending=[False, False])
            pos = cand[cand["vs baseline (pp)"] > 0].head(4)
            neg = cand[cand["vs baseline (pp)"] < 0].head(3)
            insight_rows = list(pos.to_dict("records")) + list(neg.to_dict("records"))

    if not insight_rows:
        st.info(
            "Aún no hay segmentos accionables con señal suficiente. A medida que "
            "entren más cierres por segmento, esta sección propondrá hipótesis y "
            "experimentos automáticamente."
        )
    else:
        def _experiment_card(rec: dict, idx: int):
            seg = rec["Segmento"]
            fam = rec["Familia"]
            cvr = rec["CVR %"]
            lift = rec["vs baseline (pp)"]
            n = rec["Aprobados"]
            cierres = rec["Cierres"]
            p = rec["p-value"]
            mejor = lift > 0
            vals = rec["vals"]
            dims = rec["dims"]

            # Componentes legibles del segmento
            partes = {d: v for d, v in zip(dims, vals)}
            razon = partes.get("razon_venta")
            zona = partes.get("zona_metropolitana")
            precio = partes.get("price_bucket")

            seg_desc = []
            if razon:
                seg_desc.append(f"razón de venta <b>{razon}</b>")
            if zona:
                seg_desc.append(f"en <b>{zona}</b>")
            if precio:
                seg_desc.append(f"con préstamo en el rango <b>{precio}</b>")
            if seg_desc:
                first = seg_desc[0]
                if first.startswith("razón"):
                    seg_txt = "clientes con " + " ".join(seg_desc)
                else:
                    seg_txt = "clientes " + " ".join(seg_desc)
            else:
                seg_txt = seg
            zona_txt = zona or "la zona"
            razon_txt = razon or "su motivo de venta"

            signo = "↑ mejor" if mejor else "↓ peor"
            color = "#16a34a" if mejor else "#dc2626"

            # Tamaño de muestra para experimento: regla práctica de proporciones.
            # Para detectar un lift de ~10pp con poder ~80% y alfa 0.05 se necesitan
            # ~300 por brazo; lo bajamos a un objetivo operativo de cierres por brazo.
            n_objetivo = 30 if abs(lift) >= 15 else 50

            # Variante de landing dominante DENTRO del segmento (contexto ABC test).
            # Permite proponer experimentos concretos: B=landing simple ganó en MX,
            # C=landing compleja (Tesla) ganó en CO. A=control sin landing.
            var_ctx = ""
            try:
                _seg_mask = pd.Series(True, index=_ins_base.index)
                for d, v in partes.items():
                    _seg_mask &= (_ins_base[d].astype(str) == str(v))
                _seg_sub = _ins_base[_seg_mask]
                if "abc_test_landing_co" in _seg_sub.columns and len(_seg_sub) >= 10:
                    _vc = (_seg_sub.groupby("abc_test_landing_co")["cerro"]
                           .agg(["size", "mean"]))
                    _vc = _vc[_vc["size"] >= 8]
                    if not _vc.empty:
                        _best = _vc["mean"].idxmax()
                        _bestcvr = _vc["mean"].max() * 100
                        LAND = {"A": "A (control sin landing)", "B": "B (landing simple)",
                                "C": "C (landing compleja tipo Tesla)"}
                        var_ctx = (
                            f" En este segmento, la variante que más cierra es "
                            f"<b>{LAND.get(str(_best), str(_best))}</b> ({_bestcvr:.0f}% "
                            "CVR)."
                        )
            except Exception:
                var_ctx = ""

            # ── Framework PM ──
            # 1) Insight (evidencia observada)
            insight = (
                f"El segmento de {seg_txt} cierra a <b>{cvr:.1f}%</b> "
                f"({signo} que el baseline de {cvr_base*100:.1f}%, "
                f"<b>{lift:+.1f} pp</b>; n={n} aprobados, {cierres} cierres, "
                f"p={p:.3f}).{var_ctx}"
            )

            # 2) Supuesto riesgoso (lo que asumimos y que el experimento valida)
            if mejor:
                supuesto = (
                    f"Asumimos que el alto cierre de {zona_txt} en este perfil es "
                    "<b>causado por el ajuste oferta-cliente</b> (precio acorde, "
                    "menor competencia, urgencia real) y no un artefacto de mezcla "
                    "(p. ej. inventario o equipo comercial). Riesgo Value/Viability: "
                    "que personalizar la landing no añada lift porque el cierre ya "
                    "está saturado en ese segmento."
                )
            else:
                supuesto = (
                    f"Asumimos que el bajo cierre de {zona_txt} en este perfil es "
                    "<b>un problema de propuesta/fricción atacable desde la landing</b> "
                    "(precio percibido, claridad del trámite, confianza) y no un "
                    "límite estructural de demanda en la zona. Riesgo Value: que la "
                    "brecha venga de factores fuera de la landing (competencia de "
                    "precio, perfil de riesgo) y el rediseño no la cierre."
                )

            # 3) Hipótesis (formato Creemos que… / Lo sabremos si…)
            if mejor:
                hipo = (
                    f"<i>Creemos que</i> si a {seg_txt} le enviamos una landing "
                    f"zonificada para {zona_txt} (prueba social local, comparables "
                    f"de la zona y encuadre del beneficio según «{razon_txt}»), "
                    "<i>entonces</i> el CVR aprobado→cierre subirá por encima del "
                    f"{cvr:.0f}% actual. <i>Lo sabremos cuando</i> el brazo "
                    "tratamiento supere al control con significancia (z-test, "
                    "p<0.05)."
                )
            else:
                hipo = (
                    f"<i>Creemos que</i> si a {seg_txt} le rediseñamos la landing "
                    f"(encuadre de precio acorde a {precio or 'su rango'}, refuerzo "
                    f"de valor para «{razon_txt}», trámite y CTA simplificados), "
                    "<i>entonces</i> recuperaremos parte de la brecha de "
                    f"{abs(lift):.1f} pp frente al baseline. <i>Lo sabremos cuando</i> "
                    f"el CVR del segmento pase del {cvr:.0f}% hacia el "
                    f"{cvr_base*100:.0f}% (baseline) con p<0.05."
                )

            # 4) Experimento (método, métrica, umbral, guardrail)
            if mejor:
                metodo = (
                    f"<b>A/B test zonificado en producción</b> sobre {zona_txt}, "
                    "filtrando al perfil del segmento. Sobre la variante ganadora "
                    "vigente (en MX hoy es <b>B, landing simple</b>; 95/5 vs C), "
                    f"crear un tratamiento <b>personalizado</b>: copy según «{razon_txt}»"
                    + (f", comparables y oferta calibrados a {precio}" if precio else "")
                    + ", prueba social local de la zona. Control = landing estándar "
                    "actual. Asignación 50/50 entre nuevos aprobados del segmento. "
                    "Mitigación: solo cambia contenido de la landing (reversible), no "
                    "la oferta ni el motor."
                )
                umbral = (
                    f"<b>Éxito:</b> CVR tratamiento ≥ CVR control + 5 pp con p<0.05. "
                    f"<b>Muestra objetivo:</b> ≥{n_objetivo} cierres por brazo o 4 "
                    "semanas (lo que llegue primero)."
                )
            else:
                metodo = (
                    f"<b>Paso 1 — Discovery:</b> 5–8 entrevistas a aprobados que NO "
                    f"cerraron en {zona_txt} para ubicar la fricción (precio, "
                    "confianza, trámite). <b>Paso 2 — A/B test</b> de landing "
                    f"rediseñada contra la actual sobre el perfil del segmento. Como "
                    "<b>C (landing compleja tipo Tesla)</b> ganó en CO, vale probar "
                    "subir su peso aquí (hoy 5% en MX) contra la B simple en este "
                    "segmento concreto. Mitigación: discovery barato antes de invertir "
                    "en build; el A/B solo toca contenido de landing."
                )
                umbral = (
                    f"<b>Éxito:</b> CVR del tratamiento sube ≥5 pp sobre el "
                    f"{cvr:.0f}% actual con p<0.05, sin degradar la tasa de "
                    f"respuesta (<b>guardrail</b>). <b>Muestra objetivo:</b> "
                    f"≥{n_objetivo} cierres por brazo o 4 semanas."
                )

            with st.container():
                st.markdown(
                    f"<div style='border-left:4px solid {color};padding:12px 18px;"
                    f"background:{WHITE};border-radius:8px;margin-bottom:16px;"
                    f"box-shadow:0 1px 3px rgba(0,0,0,0.06)'>"
                    f"<div style='font-weight:700;color:{DEEP};font-size:0.95rem;"
                    f"margin-bottom:6px'>Insight {idx} · {fam}</div>"
                    f"<div style='font-size:0.86rem;color:#333;margin-bottom:10px'>"
                    f"{insight}</div>"
                    f"<div style='font-size:0.8rem;color:{DEEP};font-weight:700;"
                    f"text-transform:uppercase;letter-spacing:.04em'>Supuesto riesgoso</div>"
                    f"<div style='font-size:0.84rem;color:#333;margin-bottom:8px'>"
                    f"{supuesto}</div>"
                    f"<div style='font-size:0.8rem;color:{DEEP};font-weight:700;"
                    f"text-transform:uppercase;letter-spacing:.04em'>Hipótesis</div>"
                    f"<div style='font-size:0.84rem;color:#333;margin-bottom:8px'>"
                    f"{hipo}</div>"
                    f"<div style='font-size:0.8rem;color:{DEEP};font-weight:700;"
                    f"text-transform:uppercase;letter-spacing:.04em'>Experimento · método</div>"
                    f"<div style='font-size:0.84rem;color:#333;margin-bottom:6px'>"
                    f"{metodo}</div>"
                    f"<div style='font-size:0.84rem;color:#166534'>{umbral}</div>"
                    "</div>",
                    unsafe_allow_html=True,
                )

        for i, rec in enumerate(insight_rows, start=1):
            _experiment_card(rec, i)

        st.caption(
            "Insights, supuestos, hipótesis y experimentos generados con framework "
            "de Product Discovery (supuesto riesgoso → hipótesis Creemos/Lo sabremos "
            "→ experimento con método, umbral de éxito y guardrail) sobre las señales "
            "estadísticas del universo activo. El tamaño de muestra por segmento "
            "condiciona la confianza; trátalos como punto de partida priorizable."
        )


st.divider()
st.caption(
    f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    "TTL cache: 24h. Rango activo: "
    f"{date_from.isoformat()} → {date_to.isoformat()} · Variantes: {', '.join(sel_variants)}."
)
