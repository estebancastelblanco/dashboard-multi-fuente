"""Pre-Oferta · dashboard live (HubSpot + Sheets + BigQuery funnel)."""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def _bootstrap_from_st_secrets() -> None:
    keys = [
        "HUBSPOT_ACCESS_TOKEN",
        "GOOGLE_SHEETS_CREDENTIALS",
        "BQ_PROJECT_ID", "BQ_DATASET_PROJECT", "BQ_DATASET", "BQ_TABLE",
        "GOOGLE_APPLICATION_CREDENTIALS_JSON",
        "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
    ]
    try:
        for k in keys:
            if k in st.secrets and not os.environ.get(k):
                os.environ[k] = str(st.secrets[k])
    except Exception:
        return


_bootstrap_from_st_secrets()

import importlib
from src.experiments import REGISTRY
from src.sources import bigquery as bq_src
from src.sources import gsheets as gs_src
from src.sources import hubspot as hs_src
from src.sources import supabase as sb_src

# Streamlit Cloud retiene sys.modules entre reruns; tras un deploy con cambios
# en src/sources, fuerza una recarga para que las nuevas firmas se vean.
hs_src = importlib.reload(hs_src)
bq_src = importlib.reload(bq_src)
gs_src = importlib.reload(gs_src)
sb_src = importlib.reload(sb_src)
from src.styling import (
    inject_base_css, kpi_card,
    DEEP, PRIMARY, MED, ACCENT, LIGHT, PALE, WHITE,
    GREEN_DARK, GREEN_LIGHT, YELLOW, GREY, RED,
)

st.set_page_config(page_title="Pre-Oferta", layout="wide")
inject_base_css()

# Spacing extra solo para esta página: más aire entre secciones, KPIs y gráficos.
st.markdown("""
<style>
  /* Encabezados con más separación */
  [data-testid="stMain"] h2 { margin-top: 44px !important; margin-bottom: 18px !important; }
  [data-testid="stMain"] h3 { margin-top: 28px !important; margin-bottom: 14px !important; }
  /* Cada bloque horizontal de KPIs/gráficos respira */
  [data-testid="stMain"] [data-testid="stHorizontalBlock"] { margin-bottom: 14px; }
  /* Gap entre cards horizontales */
  [data-testid="stMain"] .kcard { margin-bottom: 6px; }
  /* Margen alrededor de los plotly charts */
  [data-testid="stMain"] [data-testid="stPlotlyChart"] { margin-top: 8px; margin-bottom: 8px; }
  /* Caption descriptivo más pequeño */
  [data-testid="stMain"] [data-testid="stCaptionContainer"] { margin-top: 4px; margin-bottom: 18px; }
</style>
""", unsafe_allow_html=True)

EXPERIMENT = next(e for e in REGISTRY if e.slug == "preoferta-temprana")

st.markdown(
    f"<h1 style='color:{DEEP};font-size:1.5rem;font-weight:700;margin-bottom:0'>"
    f"Pre-Oferta</h1>"
    f"<div style='color:{MED};font-size:0.8rem;margin-bottom:20px'>"
    f"Pre-oferta temprana vía WhatsApp · MX · lanzado {EXPERIMENT.start_date} · split 95/5</div>",
    unsafe_allow_html=True,
)


DAY = 86400
SHORT = 120
START_DATE = EXPERIMENT.start_date
WA_DELIVERY = EXPERIMENT.funnel_baseline.get("wa_delivery_ratio", 0.80)
LANDING_SHEET_ID = EXPERIMENT.funnel_baseline.get("landing_sheet_id")
JUAN_OWNER_EMAIL = "juanquinones@habi.co"

# Pipelines del experimento (Sellers MX → Market Maker MX NUEVO)
PIPELINE_ORIGEN = "15290604"     # Sellers MX (donde nacen los deals)
PIPELINE_DESTINO = "731899270"   # Sellers - Market Maker MX (NUEVO) — meta
DEALSTAGE_ASIGNADO = "1066429804"
MAX_DIAS_SIN_ASIGNAR = 4         # del diseño: si pasaron 4 dias debe asignarse si o si
MAX_ENVIOS = 4                   # workflow HubSpot manda 4 plantillas (dia 1..4)

# Estados (propiedad `estado`) que SI permiten asignar al market maker.
# Cualquier otro estado significa que el deal no debe asignarse aun (esta
# en pricing, descartado, vendido, etc.)
ESTADOS_ASIGNABLES: dict[str, str] = {
    "20": "No gestionado",
    "36": "No hay suficientes datos para comparar",
    "63": "Sin pricing inicial",
}


UUID_RX = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
EXP_DOMAIN = "ofertadesdeasignado"


def _extract_uuid_from_row(row) -> str | None:
    duuid = str(row.get("Deal_uuid", "") or "").strip()
    if UUID_RX.fullmatch(duuid):
        return duuid.lower()
    for col in ("base_url", "full_url", "url"):
        m = UUID_RX.search(str(row.get(col, "") or ""))
        if m:
            return m.group(1).lower()
    return None


def _is_exp_row(row) -> bool:
    for col in ("base_url", "full_url", "url"):
        if EXP_DOMAIN in str(row.get(col, "") or "").lower():
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Loaders cacheados
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=DAY, show_spinner="HubSpot · deals seller (tratamiento)…", persist="disk")
def load_seller_deals(since_iso: str, until_iso: str) -> pd.DataFrame:
    """Tratamiento: contacto_digital=seller."""
    return hs_src.fetch_preoferta_deals(since_iso, until_iso, contacto_digital="seller")


@st.cache_data(ttl=DAY, show_spinner="HubSpot · deals no-seller (control)…", persist="disk")
def load_control_deals(since_iso: str, until_iso: str) -> pd.DataFrame:
    """Control: contacto_digital != seller (gabi + chatbot + null).

    HubSpot API no soporta NEQ ni NOT_IN limpio sobre enum vacía, así que
    traemos todo y filtramos en cliente. Se cachea por separado de seller.
    """
    df = hs_src.fetch_preoferta_deals(since_iso, until_iso, contacto_digital=None)
    if df.empty or "contacto_digital" not in df.columns:
        return df
    return df[df["contacto_digital"].astype(str).str.lower() != "seller"].copy()


@st.cache_data(ttl=SHORT, show_spinner="Sheets · logs de aperturas…")
def load_landing_logs() -> pd.DataFrame:
    if not LANDING_SHEET_ID:
        return pd.DataFrame()
    df = gs_src.fetch_tab("LOGS", sheet_id=LANDING_SHEET_ID)
    if df.empty:
        return df
    df["is_exp"] = df.apply(_is_exp_row, axis=1)
    df = df[df["is_exp"]].copy()
    df["uuid"] = df.apply(_extract_uuid_from_row, axis=1)
    df = df[df["uuid"].notna()]
    return df


@st.cache_data(ttl=DAY, show_spinner="BigQuery · nid mapping…", persist="disk")
def load_nid_mapping(deal_uuids: tuple[str, ...]) -> pd.DataFrame:
    return bq_src.fetch_nid_for_uuids(list(deal_uuids))


@st.cache_data(ttl=DAY, show_spinner="BigQuery · funnel etapas MX…", persist="disk")
def load_funnel_mex(nids: tuple[int, ...], date_from: str, date_to: str) -> pd.DataFrame:
    return bq_src.fetch_funnel_mex(list(nids), date_from=date_from, date_to=date_to)


@st.cache_data(ttl=DAY, show_spinner="BigQuery · A/B funnel MX…")
def load_ab_funnel_mex_v2(since_iso: str, until_iso: str) -> pd.DataFrame:
    """Funnel A/B armado en BQ: cada (nid, grupo) con sus etapas.

    Renombrada con _v2 para invalidar el cache de disco que tenía la versión
    vieja donde A se restringía a deals creados en la ventana del experimento.
    Ahora A cuenta TODO el funnel MX no-seller en el rango.
    """
    return bq_src.fetch_ab_funnel_mex(since_iso, until_iso)


@st.cache_data(ttl=DAY, show_spinner="BigQuery · funnel mensual MX…")
def load_funnel_monthly_mex_ab(since_iso: str, until_iso: str) -> pd.DataFrame:
    """Funnel mensual A/B. Renombrada de load_funnel_monthly_mex para invalidar
    el cache de disco que tenía la versión sin la columna `grupo`."""
    return bq_src.fetch_funnel_monthly_mex(since_iso, until_iso)


@st.cache_data(ttl=SHORT, show_spinner=False)
def enrich_deals_df(
    df: pd.DataFrame,
    owner_email_map: dict[str, str],
    opened_uuids: frozenset[str],
    juan_email: str,
) -> pd.DataFrame:
    """Calcula columnas derivadas pesadas (interacción, asignación, abrió link)
    una sola vez por (df, mapas) y las cachea en memoria. Evita reprocesar
    269+ filas con apply en cada rerun.

    Definicion vigente de 'asignado' (post EXP-003 lanzamiento):
        pipeline == PIPELINE_DESTINO ("Sellers - Market Maker MX (NUEVO)")
    porque la asignacion automatica al MM (juanquinones@habi.co) mueve
    el deal a ese pipeline + stage Asignado + prioridad B.
    """
    df = df.copy()
    df["interaccion"] = df.apply(_interaccion, axis=1)
    df["owner_email"] = df["hubspot_owner_id"].astype(str).map(owner_email_map).fillna("")
    df["asignado"] = df["pipeline"].astype(str) == PIPELINE_DESTINO
    df["asignacion_label"] = df["asignado"].map({True: "Asignado", False: "No asignado"})
    df["abrió_link"] = df["deal_uuid"].astype(str).str.lower().isin(opened_uuids)
    df["owner_label"] = df["hubspot_owner_id"].astype(str).map(
        lambda oid: owner_email_map.get(oid, oid) if oid and oid != "nan" else "(sin owner)"
    )
    # Dias desde createdate (UTC vs UTC) — util para alerta de >4 dias sin asignar
    if "createdate" in df.columns:
        now = pd.Timestamp.now(tz="UTC")
        cd = pd.to_datetime(df["createdate"], errors="coerce", utc=True)
        df["dias_desde_creacion"] = (now - cd).dt.total_seconds() / 86400.0
    else:
        df["dias_desde_creacion"] = pd.NA
    if "preofertaflag1" in df.columns:
        df["preofertaflag1"] = pd.to_numeric(df["preofertaflag1"], errors="coerce").fillna(0).astype(int)
    else:
        df["preofertaflag1"] = 0
    return df


def _interaccion(row) -> str:
    """Jerarquía CTA: quiero oferta > preguntas > error > sin interacción."""
    if int(row.get("quiero_recibir_oferta_formal", 0) or 0) > 0:
        return "Quiero oferta"
    if int(row.get("tengo_preguntas", 0) or 0) > 0:
        return "Tengo preguntas"
    if int(row.get("error_preoferta", 0) or 0) > 0:
        return "Error"
    return "Sin interacción"


@st.cache_data(ttl=DAY, show_spinner="HubSpot · opciones de Estado…", persist="disk")
def load_estado_options() -> dict[str, str]:
    """Mapping value(str) -> label de la propiedad `estado` de Deal."""
    try:
        return hs_src.fetch_property_options("estado")
    except Exception:
        return {}


@st.cache_data(ttl=SHORT, show_spinner="Supabase · envios WA…")
def load_whatsapp_sends(since_iso: str, until_iso: str) -> pd.DataFrame:
    try:
        return sb_src.fetch_whatsapp_sends(since_iso, until_iso)
    except Exception as exc:
        st.warning(f"Supabase whatsapp_sends: {type(exc).__name__}: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=SHORT, show_spinner="Supabase · interacciones CTAs…")
def load_deal_interactions(since_iso: str, until_iso: str) -> pd.DataFrame:
    try:
        return sb_src.fetch_deal_interactions(since_iso, until_iso)
    except Exception as exc:
        st.warning(f"Supabase deal_interactions: {type(exc).__name__}: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=SHORT, show_spinner="Supabase · asignaciones automaticas…")
def load_deal_assignments(since_iso: str, until_iso: str) -> pd.DataFrame:
    try:
        return sb_src.fetch_deal_assignments(since_iso, until_iso)
    except Exception as exc:
        st.warning(f"Supabase deal_assignments: {type(exc).__name__}: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=DAY, show_spinner="HubSpot · catálogo owners…", persist="disk")
def load_owner_emails(owner_ids: tuple[str, ...]) -> dict[str, str]:
    """owner_id → email. Solo lo que usamos para detectar Juan Quiñones."""
    import requests
    headers = {
        "Authorization": f"Bearer {os.environ['HUBSPOT_ACCESS_TOKEN']}",
    }
    out: dict[str, str] = {}
    for oid in owner_ids:
        if not oid:
            continue
        try:
            r = requests.get(
                f"https://api.hubapi.com/crm/v3/owners/{oid}",
                headers=headers, timeout=10,
            )
            if r.status_code == 200:
                out[oid] = r.json().get("email", "")
        except Exception:
            out[oid] = ""
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar · filtros
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    if st.button("Actualizar datos", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.markdown("---")
    st.markdown(
        f"<div style='color:{LIGHT};font-weight:700;font-size:0.9rem;margin-bottom:14px'>"
        f"Filtros</div>",
        unsafe_allow_html=True,
    )

    st.markdown("### Fecha de creación")
    today = date.today()
    default_from = date(2026, 5, 7)
    date_range = st.date_input(
        "rango_fecha",
        value=(default_from, today),
        min_value=date(2026, 1, 1),
        max_value=today,
        label_visibility="collapsed",
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        date_from, date_to = date_range
    else:
        date_from, date_to = default_from, today

    st.markdown("### Asignación")
    ASIGN_OPTS = ["Asignado", "No asignado"]
    sel_asign = st.multiselect(
        "asignacion", ASIGN_OPTS, default=ASIGN_OPTS,
        label_visibility="collapsed",
        help=f"Asignado = owner ≠ {JUAN_OWNER_EMAIL} AND prioridad_gestion_mm conocida",
    )

    st.markdown("### Interacción CTA")
    CTA_OPTS = ["Quiero oferta", "Tengo preguntas", "Error", "Sin interacción"]
    sel_cta = st.multiselect(
        "cta", CTA_OPTS, default=CTA_OPTS,
        label_visibility="collapsed",
        help="Jerarquía: oferta > preguntas > error",
    )


def _applied(sel: list, opts: list) -> bool:
    return bool(sel) and len(sel) < len(opts)


since_iso = date_from.isoformat()
until_iso = date_to.isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Carga base
# ─────────────────────────────────────────────────────────────────────────────
try:
    df_t = load_seller_deals(since_iso, until_iso)
except Exception as exc:
    st.error(f"HubSpot tratamiento: {type(exc).__name__}: {exc}")
    st.stop()

if df_t.empty:
    st.warning(f"No hay deals seller en {since_iso} → {until_iso}.")
    st.stop()

try:
    df_c = load_control_deals(since_iso, until_iso)
except Exception as exc:
    df_c = pd.DataFrame()
    st.warning(f"Control HS: {type(exc).__name__}: {exc}")

try:
    df_logs = load_landing_logs()
except Exception as exc:
    df_logs = pd.DataFrame()
    st.warning(f"Sheets logs: {type(exc).__name__}: {exc}")

# Owner emails (para detectar Juan Quiñones)
owner_ids_t = tuple(sorted(set(df_t["hubspot_owner_id"].dropna().astype(str).tolist())))
try:
    owner_email_map = load_owner_emails(owner_ids_t)
except Exception:
    owner_email_map = {}

# nid mapping vía BQ (solo tratamiento por ahora — control es solo para
# distribuciones y A/B, no necesita funnel completo)
uuids_t = tuple(sorted(df_t["deal_uuid"].dropna().astype(str).str.lower().unique().tolist()))
try:
    df_nid_t = load_nid_mapping(uuids_t)
except Exception as exc:
    df_nid_t = pd.DataFrame(columns=["deal_uuid", "nid"])
    st.warning(f"BQ nid mapping: {type(exc).__name__}: {exc}")

if not df_nid_t.empty:
    df_t["deal_uuid_lc"] = df_t["deal_uuid"].astype(str).str.lower()
    df_nid_t["deal_uuid_lc"] = df_nid_t["deal_uuid"].astype(str).str.lower()
    df_t = df_t.merge(df_nid_t[["deal_uuid_lc", "nid"]], on="deal_uuid_lc", how="left", suffixes=("_hs", ""))
    if "nid" not in df_t.columns:
        df_t["nid"] = None

# Funnel BQ
nids_t = tuple(int(n) for n in df_t["nid"].dropna().astype(int).tolist() if not pd.isna(n))
try:
    df_funnel = load_funnel_mex(nids_t, since_iso, until_iso) if nids_t else pd.DataFrame()
except Exception as exc:
    df_funnel = pd.DataFrame()
    st.warning(f"BQ funnel: {type(exc).__name__}: {exc}")

# Tablas Supabase (envios WA, interacciones, asignaciones)
df_sends = load_whatsapp_sends(since_iso, until_iso)
df_interactions = load_deal_interactions(since_iso, until_iso)
df_assignments = load_deal_assignments(since_iso, until_iso)

# Labels legibles para los codigos de `estado` (necesario para el kanban)
estado_options = load_estado_options()


# ─────────────────────────────────────────────────────────────────────────────
# Derivados: cacheados en memoria con enrich_deals_df
# ─────────────────────────────────────────────────────────────────────────────
opened_uuids_set: frozenset[str] = frozenset()
if not df_logs.empty and "uuid" in df_logs.columns:
    opened_uuids_set = frozenset(df_logs["uuid"].dropna().astype(str).str.lower())

df_t = enrich_deals_df(df_t, owner_email_map, opened_uuids_set, JUAN_OWNER_EMAIL)
opened_uuids = set(opened_uuids_set)

# Decodear `estado` (HubSpot devuelve el value, no el label)
if "estado" in df_t.columns:
    df_t["estado_code"] = df_t["estado"].astype(str)
    df_t["estado_label"] = df_t["estado_code"].map(estado_options).fillna("(sin estado)")
    df_t.loc[df_t["estado_code"] == "nan", "estado_label"] = "(sin estado)"
else:
    df_t["estado_code"] = ""
    df_t["estado_label"] = "(sin estado)"
df_t["es_asignable_estado"] = df_t["estado_code"].isin(ESTADOS_ASIGNABLES.keys())


# Aplicar filtros del sidebar al tratamiento
df_t_f = df_t.copy()
if _applied(sel_asign, ASIGN_OPTS):
    df_t_f = df_t_f[df_t_f["asignacion_label"].isin(sel_asign)]
if _applied(sel_cta, CTA_OPTS):
    df_t_f = df_t_f[df_t_f["interaccion"].isin(sel_cta)]


# ─────────────────────────────────────────────────────────────────────────────
# KPIs
# ─────────────────────────────────────────────────────────────────────────────
n_universo = len(df_t_f)
n_wa = int(round(n_universo * WA_DELIVERY))
n_opened = int(df_t_f["abrió_link"].sum())
n_oferta = int((df_t_f["interaccion"] == "Quiero oferta").sum())
n_preg = int((df_t_f["interaccion"] == "Tengo preguntas").sum())
n_err = int((df_t_f["interaccion"] == "Error").sum())
n_asig = int(df_t_f["asignado"].sum())

# Los KPIs descriptivos (Universo, Enviados WA, Abrieron link, Asignados,
# Quiero oferta, Tengo preguntas) se omiten arriba a propósito: la información
# vive en el embudo principal y se puede filtrar desde el sidebar. Las cifras
# n_universo / n_wa / n_opened / n_asig / n_oferta / n_preg siguen calculadas
# arriba porque los gráficos posteriores las consumen.


# Paleta verde del tratamiento (embudo principal + comparativa A/B · B).
PALETTE_TRATAMIENTO = [
    GREEN_DARK, "#0f5535", "#1f7a2a", "#2e8b3a", "#4daa5a",
    "#7eb37e", GREEN_LIGHT, "#cce4ce", "#86efac", "#a7f3b0", "#dcfce7",
]

# ─────────────────────────────────────────────────────────────────────────────
# Funnel 1 · general del tratamiento (universo → cierre)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Embudo del experimento · tratamiento</h2>", unsafe_allow_html=True)

# Helper para contar leads en una etapa del funnel BQ, restringido a nids
# del df filtrado.
nids_f = set(df_t_f["nid"].dropna().astype(int).tolist()) if "nid" in df_t_f.columns else set()


def _count_stage(stage: str) -> int:
    if df_funnel.empty:
        return 0
    sub = df_funnel[df_funnel["valor"] == stage]
    if not nids_f:
        return int(sub["nid"].nunique())
    return int(sub[sub["nid"].isin(nids_f)]["nid"].nunique())


def _count_stages_union(stages: list[str]) -> int:
    """Nids únicos que pasaron por al menos una de las etapas (sin doble contar)."""
    if df_funnel.empty:
        return 0
    sub = df_funnel[df_funnel["valor"].isin(stages)]
    if nids_f:
        sub = sub[sub["nid"].isin(nids_f)]
    return int(sub["nid"].nunique())


n_cita_agendada = _count_stage("Cita Agendada (hubspot)")
n_visita = _count_stage("Visita Efectuada (hubspot)")
n_pre_comite = _count_stage("Pre-comite validado")
n_aprobado = _count_stage("Aprobado General")
n_pendiente_oferta = _count_stage("Pendiente respuesta oferta")
n_aceptada = _count_stage("Acepto Oferta - Pendiente firma")
n_cierre = _count_stages_union(["Cierre - Comprado", "Cierre  OCD"])

# Embudo general (sin interacciones CTA: las ves en KPIs + filtro sidebar).
stages = [
    ("Universo (Seller MX)",            n_universo,         "HubSpot · contacto_digital=seller"),
    ("Enviados WA",                     n_wa,               f"Estimado · {int(WA_DELIVERY*100)}% × Universo"),
    ("Abrieron link",                   n_opened,           "Sheets LOGS · dedup por uuid"),
    ("Asignados",                       n_asig,             "pipeline = Market Maker MX (NUEVO)"),
    ("Cita Agendada",                   n_cita_agendada,    "BQ · Cita Agendada (hubspot)"),
    ("Visita Efectuada",                n_visita,           "BQ · Visita Efectuada (hubspot)"),
    ("Pre-comité validado",             n_pre_comite,       "BQ · Pre-comite validado"),
    ("Aprobado General",                n_aprobado,         "BQ · Aprobado General"),
    ("Pendiente respuesta oferta",      n_pendiente_oferta, "BQ · Pendiente respuesta oferta"),
    ("Acepto oferta · pendiente firma", n_aceptada,         "BQ · Acepto Oferta - Pendiente firma"),
    ("Cierre",                          n_cierre,           "BQ · Cierre - Comprado + Cierre OCD"),
]

f_labels = [s[0] for s in stages]
f_vals = [s[1] for s in stages]
f_sources = [s[2] for s in stages]
# Bar horizontal con escala log: cuando un paso tiene 500 y el siguiente 2, el
# funnel tradicional aplasta visualmente las etapas finales. La escala log
# preserva el contraste y permite ver el cierre incluso con 1 lead.
f_text = [
    f"{v:,}" + (f"  ·  {v/f_vals[i-1]*100:.0f}% CVR" if i > 0 and f_vals[i-1] > 0 else "")
    for i, v in enumerate(f_vals)
]
nonzero = [v for v in f_vals if v > 0]
use_log = (max(f_vals) if f_vals else 0) > 100 and (min(nonzero) if nonzero else 0) > 0
fig_funnel = go.Figure(go.Bar(
    x=f_vals, y=f_labels, orientation="h",
    marker_color=PALETTE_TRATAMIENTO[:len(stages)],
    text=f_text,
    textposition="outside", textfont=dict(size=11, color=DEEP),
    customdata=f_sources,
    hovertemplate="<b>%{y}</b><br>%{x:,} leads · %{customdata}<extra></extra>",
))
fig_funnel.update_layout(
    paper_bgcolor=WHITE, plot_bgcolor=WHITE,
    font=dict(family="Inter, sans-serif", color=DEEP, size=11),
    height=520, margin=dict(l=10, r=280, t=10, b=10),
    xaxis=dict(
        type="log" if use_log else "linear",
        title="Leads" + (" (escala log)" if use_log else ""),
        gridcolor="#ede8f5", tickformat=",d",
    ),
    yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig_funnel, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Funnel 2 · comparativo A/B (control vs tratamiento)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Comparativa A/B · Control vs Tratamiento</h2>", unsafe_allow_html=True)

# Cargamos el funnel A/B directamente desde BQ (mucho más rápido y preciso
# que paginar 9000+ deals desde HubSpot). El control queda definido como
# "todo MX no-seller" y el tratamiento como contacto_digital='seller'.
try:
    df_ab = load_ab_funnel_mex_v2(since_iso, until_iso)
except Exception as exc:
    df_ab = pd.DataFrame()
    st.warning(f"BQ A/B funnel: {type(exc).__name__}: {exc}")


# Etapas comparables. Las stages se alinean con la query oficial de Habi
# (`bi_mx.seguimiento_funnel_mex`). Cuando la columna `stage` es una lista,
# contamos la unión (caso Cierre que suma Comprado + OCD).
COMPARE_STAGES: list[tuple[str, list[str] | str]] = [
    ("Lead llega",       "_universe"),
    ("Asignación",       ["Primer asignacion"]),
    ("Cita Agendada",    ["Cita Agendada (hubspot)", "Cita Agendada"]),
    ("Visita Efectuada", ["Visita Efectuada (hubspot)", "Visita Efectuada"]),
    ("Pre-comité",       ["Pre-comite validado"]),
    ("Aprobado",         ["Aprobado General"]),
    ("Oferta",           ["Pendiente respuesta oferta"]),
    ("Acepto · pendiente firma", ["Acepto Oferta - Pendiente firma"]),
    ("Cierre",           ["Cierre - Comprado", "Cierre  OCD"]),
]


def _count_stages_for_group(
    df_ab: pd.DataFrame,
    grupo: str,
    stages: list[str],
    *,
    nids_only: set[int] | None = None,
) -> int:
    if df_ab.empty:
        return 0
    sub = df_ab[(df_ab["grupo"] == grupo) & (df_ab["valor"].isin(stages))]
    if nids_only is not None:
        sub = sub[sub["nid"].isin(nids_only)]
    return int(sub["nid"].nunique())


def _build_funnel_counts(
    df_ab: pd.DataFrame,
    grupo: str,
    *,
    nids_only: set[int] | None = None,
) -> list[int]:
    df_g = df_ab[df_ab["grupo"] == grupo]
    if nids_only is not None:
        df_g = df_g[df_g["nid"].isin(nids_only)]
    universe = int(df_g["nid"].nunique()) if not df_g.empty else 0
    counts: list[int] = []
    for _label, stages in COMPARE_STAGES:
        if stages == "_universe":
            counts.append(universe)
        elif isinstance(stages, list):
            counts.append(_count_stages_for_group(df_ab, grupo, stages, nids_only=nids_only))
        else:
            counts.append(_count_stages_for_group(df_ab, grupo, [stages], nids_only=nids_only))
    return counts


vals_c = _build_funnel_counts(df_ab, "A")
# Tratamiento B: mismo cohorte de nids que el embudo superior (df_t_f + funnel BQ).
vals_t = _build_funnel_counts(df_ab, "B", nids_only=nids_f if nids_f else None)

col_c, col_t = st.columns(2)
labels = [s[0] for s in COMPARE_STAGES]


def _funnel_chart(values: list[int], title: str, palette: list[str]):
    """Bar horizontal en escala log con conversion etapa-a-etapa y % vs Universo.

    `go.Funnel` se ve mal cuando hay disparidad gigante (9886 → 2): la cola
    queda invisible. Por eso usamos `go.Bar` con xaxis log + etiquetas con
    absoluto, % vs anterior y % vs universo.
    """
    universo = values[0] if values else 0
    text = []
    for i, v in enumerate(values):
        if i == 0 or universo == 0:
            text.append(f"{v:,}")
        else:
            prev = values[i - 1]
            pct_prev = (v / prev * 100) if prev > 0 else 0
            text.append(f"{v:,}  ·  {pct_prev:.0f}% CVR")
    nonzero = [v for v in values if v > 0]
    use_log = (max(values) if values else 0) > 50 and (min(nonzero) if nonzero else 0) > 0
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker_color=palette[:len(values)],
        text=text,
        textposition="outside", textfont=dict(size=10, color=DEEP),
        hovertemplate="<b>%{y}</b><br>%{x:,} leads<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color=DEEP, family="Inter")),
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=10),
        height=460, margin=dict(l=10, r=240, t=44, b=10),
        xaxis=dict(
            type="log" if use_log else "linear",
            title="Leads" + (" (log)" if use_log else ""),
            gridcolor="#ede8f5", tickformat=",d",
        ),
        yaxis=dict(autorange="reversed"),
    )
    return fig


# Control (A) izquierda · Tratamiento (B) derecha
PALETTE_CONTROL = [DEEP, "#3a1956", PRIMARY, MED, ACCENT, LIGHT, "#bd8be3", PALE, "#c4b5fd"]
with col_c:
    st.plotly_chart(
        _funnel_chart(vals_c, "A · Control (sin pre-oferta)", PALETTE_CONTROL),
        use_container_width=True,
    )
with col_t:
    st.plotly_chart(
        _funnel_chart(vals_t, "B · Tratamiento (con pre-oferta)", PALETTE_TRATAMIENTO),
        use_container_width=True,
    )

cvr_c = vals_c[-1] / vals_c[1] if len(vals_c) > 1 and vals_c[1] > 0 else 0.0
cvr_t = vals_t[-1] / vals_t[1] if len(vals_t) > 1 and vals_t[1] > 0 else 0.0
delta_cvr_main_pp = (cvr_t - cvr_c) * 100
delta_color = GREEN_DARK if delta_cvr_main_pp >= 0 else RED
st.markdown(
    f"<div style='display:flex;gap:14px;margin-top:6px'>"
    f"<div style='flex:1;background:{WHITE};border-left:4px solid {PRIMARY};"
    f"padding:10px 14px;border-radius:6px;font-size:0.82rem'>"
    f"<b>Control · CVR asignación→cierre</b>"
    f"<div style='font-size:1.3rem;font-weight:700;color:{PRIMARY}'>"
    f"{cvr_c*100:.2f}% · {vals_c[-1]}/{vals_c[1]}</div></div>"
    f"<div style='flex:1;background:{WHITE};border-left:4px solid {GREEN_DARK};"
    f"padding:10px 14px;border-radius:6px;font-size:0.82rem'>"
    f"<b>Tratamiento · CVR asignación→cierre</b>"
    f"<div style='font-size:1.3rem;font-weight:700;color:{GREEN_DARK}'>"
    f"{cvr_t*100:.2f}% · {vals_t[-1]}/{vals_t[1]}</div></div>"
    f"<div style='flex:1;background:{WHITE};border-left:4px solid {delta_color};"
    f"padding:10px 14px;border-radius:6px;font-size:0.82rem'>"
    f"<b>Δ Tratamiento - Control</b>"
    f"<div style='font-size:1.3rem;font-weight:700;color:{delta_color}'>"
    f"{delta_cvr_main_pp:+.2f}pp</div></div>"
    f"</div>",
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────────────────────────────────────
# Funnel mensual MX · contexto histórico (últimos 12 meses)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Funnel mensual MX · A control vs B tratamiento</h2>", unsafe_allow_html=True)

try:
    df_monthly = load_funnel_monthly_mex_ab(since_iso, until_iso)
except Exception as exc:
    df_monthly = pd.DataFrame()
    st.warning(f"BQ funnel mensual: {type(exc).__name__}: {exc}")

# Defensa contra cache antiguo o respuestas inconsistentes
if not df_monthly.empty and "grupo" not in df_monthly.columns:
    df_monthly = df_monthly.assign(grupo="A")

# Helper: aclarar un color hex en N% para diferenciar control (claro) del
# tratamiento (intenso) manteniendo la familia de color.
def _lighten(hex_color: str, factor: float = 0.55) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    r, g, b = (int(h[i:i+2], 16) for i in (0, 2, 4))
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


if not df_monthly.empty:
    # Stages a graficar (orden lógico del funnel). Color intenso = tratamiento.
    MONTHLY_STAGES = [
        ("Cita Agendada (hubspot)",         "#ff6b6b"),
        ("Cita Agendada",                   PRIMARY),
        ("Visita Efectuada (hubspot)",      "#ff8c5a"),
        ("Visita Efectuada",                "#4fb0c6"),
        ("Pre-comite validado",             GREEN_LIGHT),
        ("Aprobado General",                "#7f7f7f"),
        ("Pendiente respuesta oferta",      GREEN_DARK),
        ("Acepto Oferta - Pendiente firma", "#444"),
        ("Cierre - Comprado",               "#e63946"),
        ("Cierre  OCD",                     DEEP),
    ]
    df_monthly["mes_str"] = pd.to_datetime(df_monthly["mes"]).dt.strftime("%b %Y").str.lower()
    meses_ordenados = (
        df_monthly[["mes", "mes_str"]]
        .drop_duplicates()
        .sort_values("mes")["mes_str"].tolist()
    )

    fig_monthly = go.Figure()
    for stage, color_dark in MONTHLY_STAGES:
        sub = df_monthly[df_monthly["valor"] == stage].copy()
        if sub.empty:
            continue
        if "grupo" not in sub.columns:
            sub = sub.assign(grupo="A")
        color_light = _lighten(color_dark, 0.55)

        # Pivot: una fila por mes, columnas A y B
        piv = (
            sub.pivot_table(index="mes_str", columns="grupo", values="leads", aggfunc="sum")
            .reindex(meses_ordenados)
            .fillna(0)
            .astype(int)
        )
        vals_a = piv.get("A", pd.Series(0, index=meses_ordenados)).tolist()
        vals_b = piv.get("B", pd.Series(0, index=meses_ordenados)).tolist()

        # Barra control (claro) abajo
        fig_monthly.add_trace(go.Bar(
            x=meses_ordenados, y=vals_a,
            name=f"{stage} · A",
            marker_color=color_light,
            legendgroup=stage, showlegend=True,
            text=[f"{v:,}" if v > 0 else "" for v in vals_a],
            textposition="outside",
            textfont=dict(size=9, color=color_dark),
            hovertemplate=f"<b>{stage} · Control</b><br>%{{x}}<br>%{{y:,}} leads<extra></extra>",
            offsetgroup=stage,
        ))
        # Barra tratamiento (intenso) encima
        fig_monthly.add_trace(go.Bar(
            x=meses_ordenados, y=vals_b,
            name=f"{stage} · B",
            marker_color=color_dark,
            legendgroup=stage, showlegend=True,
            text=[f"{v:,}" if v > 0 else "" for v in vals_b],
            textposition="outside",
            textfont=dict(size=9, color=DEEP),
            hovertemplate=f"<b>{stage} · Tratamiento</b><br>%{{x}}<br>%{{y:,}} leads<extra></extra>",
            offsetgroup=stage,
        ))

    fig_monthly.update_layout(
        barmode="stack",
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=10),
        height=520,
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    font=dict(size=9), traceorder="grouped"),
        xaxis=dict(title="Mes", gridcolor="#ede8f5", categoryorder="array",
                   categoryarray=meses_ordenados),
        yaxis=dict(title="Leads únicos", gridcolor="#ede8f5", tickformat=",d"),
        uniformtext_minsize=8, uniformtext_mode="show",
    )
    st.plotly_chart(fig_monthly, use_container_width=True)
else:
    st.info("Sin datos del funnel mensual MX en el rango seleccionado.")


# ─────────────────────────────────────────────────────────────────────────────
# Split A/B · 95/5
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Split A/B · Control vs Tratamiento</h2>", unsafe_allow_html=True)

col_ab, _col_filler = st.columns([1, 1])
with col_ab:
    n_t_total = len(df_t)
    n_c_total = len(df_c)
    total = n_t_total + n_c_total
    if total > 0:
        share_t = n_t_total / total * 100
        share_c = n_c_total / total * 100
        # Pie donut, ordenando tratamiento PRIMERO con rotación 270° para que
        # el slice pequeño (verde) quede arriba-izquierda y el label se lea
        # sin tocar el borde del contenedor.
        fig_ab = go.Figure(go.Pie(
            labels=["Tratamiento (Seller)", "Control (no-Seller)"],
            values=[n_t_total, n_c_total],
            hole=0.5,
            sort=False,
            direction="clockwise",
            rotation=-90,  # tratamiento sale en la zona superior-izquierda
            marker_colors=[GREEN_DARK, PRIMARY],
            textinfo="label+percent+value",
            textfont=dict(size=11, color="#fff"),
            textposition="inside",
            insidetextorientation="horizontal",
            hovertemplate="<b>%{label}</b><br>%{value:,} leads · %{percent}<extra></extra>",
        ))
        target_line = f"Target 95/5 · actual {share_c:.1f}/{share_t:.1f}"
        fig_ab.update_layout(
            paper_bgcolor=WHITE, showlegend=False,
            title=dict(text=f"Split A/B · {target_line}",
                       font=dict(size=13, color=DEEP, family="Inter")),
            height=320, margin=dict(l=5, r=5, t=44, b=5),
        )
        st.plotly_chart(fig_ab, use_container_width=True)
        st.caption(
            f"Universo total {total:,} leads MX en {since_iso} → {until_iso}. "
            f"Tratamiento (seller) {share_t:.2f}% vs Control (no-seller) {share_c:.2f}%."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Métricas de alarma
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Métricas de alarma</h2>", unsafe_allow_html=True)

# CVR asignado → cierre
n_asig_t = vals_t[1] if len(vals_t) > 1 else 0
n_cierre_t = vals_t[-1] if vals_t else 0
n_asig_c = vals_c[1] if len(vals_c) > 1 else 0
n_cierre_c = vals_c[-1] if vals_c else 0
cvr_t_val = n_cierre_t / n_asig_t if n_asig_t > 0 else 0.0
cvr_c_val = n_cierre_c / n_asig_c if n_asig_c > 0 else 0.0
delta_cvr_pp = (cvr_t_val - cvr_c_val) * 100

# Tasa de abandono: aperturas sin interacción CTA
abrieron = int(df_t_f["abrió_link"].sum())
con_cta = int(df_t_f[df_t_f["abrió_link"]]["interaccion"].isin(["Quiero oferta", "Tengo preguntas"]).sum())
tasa_abandono = (abrieron - con_cta) / abrieron if abrieron > 0 else 0.0

# Umbral: -3pp en CVR (~7 cierres perdidos sobre 265 leads)
UMBRAL_PP = -3.0
alarma_cvr = delta_cvr_pp <= UMBRAL_PP

m1, m2, m3, m4 = st.columns(4)

def _alarm_card(label: str, value: str, sub: str, is_alarm: bool):
    border = RED if is_alarm else GREEN_DARK
    return (
        f"<div class='kcard' style='border-left:4px solid {border}'>"
        f"<div class='kval' style='color:{border}'>{value}</div>"
        f"<div class='klbl'>{label}</div>"
        f"<div class='ksub'>{sub}</div>"
        f"</div>"
    )


m1.markdown(_alarm_card(
    "Δ CVR asignado→cierre",
    f"{delta_cvr_pp:+.1f}pp",
    f"T {cvr_t_val*100:.1f}% vs C {cvr_c_val*100:.1f}% · umbral ≤ {UMBRAL_PP}pp",
    alarma_cvr,
), unsafe_allow_html=True)

m2.markdown(_alarm_card(
    "CVR tratamiento",
    f"{cvr_t_val*100:.2f}%",
    f"{n_cierre_t} cierres / {n_asig_t} asignados",
    n_asig_t > 0 and cvr_t_val < cvr_c_val,
), unsafe_allow_html=True)

m3.markdown(_alarm_card(
    "Tasa abandono post-apertura",
    f"{tasa_abandono*100:.1f}%",
    f"{abrieron - con_cta} sin CTA / {abrieron} abrieron",
    tasa_abandono > 0.85,
), unsafe_allow_html=True)

m4.markdown(_alarm_card(
    "Volumen cierres absolutos",
    f"{n_cierre_t}",
    f"{n_cierre_t - n_cierre_c:+d} vs control",
    n_cierre_t < n_cierre_c,
), unsafe_allow_html=True)

with st.expander("Cómo se calculan estos umbrales"):
    st.markdown(
        """
- **Δ CVR ≤ -3pp**: del documento de diseño. Equivale a ~7 cierres perdidos
  sobre los 265 leads del tratamiento. Calibrar con los primeros 50 leads.
- **Tasa abandono > 85%**: porcentaje de quienes abrieron el link pero no
  clicaron ningún CTA. Si supera 85% sugiere que el copy no engancha o que
  el filtrado temprano es demasiado agresivo.
- **Margin variance**: necesita datos de cierre (precio real compra vs
  precio_base del armado). Se calculará cuando lleguen los primeros cierres.
"""
    )


# ─────────────────────────────────────────────────────────────────────────────
# Evolución de los 4 envíos · plantilla WhatsApp
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Evolución de los 4 envíos · plantilla WhatsApp</h2>", unsafe_allow_html=True)

flag_series = (
    df_t_f["preofertaflag1"].fillna(0).astype(int).clip(lower=0, upper=MAX_ENVIOS)
)
flag_counts = flag_series.value_counts().reindex(range(0, MAX_ENVIOS + 1), fill_value=0)
total_uni = max(1, len(df_t_f))

# KPIs compactos: cuantos recibieron AL MENOS N envios
def _at_least(n: int) -> int:
    return int(sum(flag_counts.get(i, 0) for i in range(n, MAX_ENVIOS + 1)))

received_at_least = {n: _at_least(n) for n in range(0, MAX_ENVIOS + 1)}

# 5 KPI cards horizontales con conteo + % del universo
kpi_cols = st.columns(MAX_ENVIOS + 1)
KPI_LABELS = {
    0: ("Universo",        "leads en tratamiento"),
    1: ("≥ 1 envío",       "han recibido al menos el primero"),
    2: ("≥ 2 envíos",      "siguen vivos al envío 2"),
    3: ("≥ 3 envíos",      "siguen vivos al envío 3"),
    4: ("≥ 4 envíos",      "completaron la secuencia"),
}
for i, col in enumerate(kpi_cols):
    val = received_at_least[i]
    pct = val / total_uni * 100
    label, sub = KPI_LABELS[i]
    col.markdown(
        kpi_card(label, f"{val:,}", f"{pct:.1f}% · {sub}"),
        unsafe_allow_html=True,
    )

# Doble bar chart horizontal: Actual (en este paso ahora) + Acumulado (≥N)
LABELS_FLAG = {
    0: "Sin enviar",
    1: "Recibió 1 · esperando 2",
    2: "Recibió 2 · esperando 3",
    3: "Recibió 3 · esperando 4",
    4: "Recibió los 4",
}
labels_full = [LABELS_FLAG[i] for i in range(0, MAX_ENVIOS + 1)]
vals_now = [int(flag_counts.get(i, 0)) for i in range(0, MAX_ENVIOS + 1)]
vals_acum = [received_at_least[i] for i in range(0, MAX_ENVIOS + 1)]

fig_envios = go.Figure()
fig_envios.add_trace(go.Bar(
    x=vals_acum, y=labels_full, orientation="h",
    marker_color=PALE,
    text=[f"{v:,}  ({v/total_uni*100:.0f}%)" for v in vals_acum],
    textposition="outside", textfont=dict(size=10, color=MED),
    name="Acumulado (≥ N)",
    hovertemplate="Acumulado %{y}: %{x:,}<extra></extra>",
))
fig_envios.add_trace(go.Bar(
    x=vals_now, y=labels_full, orientation="h",
    marker_color=PRIMARY,
    text=[f"{v:,}" for v in vals_now],
    textposition="inside", textfont=dict(size=10, color="#fff"),
    name="Actualmente en este paso",
    hovertemplate="Hoy en %{y}: %{x:,}<extra></extra>",
))
fig_envios.update_layout(
    barmode="overlay",
    paper_bgcolor=WHITE, plot_bgcolor=WHITE,
    font=dict(family="Inter, sans-serif", color=DEEP, size=11),
    height=300, margin=dict(l=10, r=100, t=10, b=10),
    xaxis=dict(title="Leads", gridcolor="#ede8f5", tickformat=",d"),
    yaxis=dict(autorange="reversed"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=10)),
)
st.plotly_chart(fig_envios, use_container_width=True)

# Insights: tasa de progresion entre pasos
prog_cols = st.columns(MAX_ENVIOS)
for n in range(1, MAX_ENVIOS + 1):
    prev = received_at_least[n - 1] if n > 1 else total_uni
    curr = received_at_least[n]
    pct = (curr / prev * 100) if prev > 0 else 0.0
    prog_cols[n - 1].markdown(kpi_card(
        f"Progresión {n - 1 if n > 1 else 'Universo'} → {n}",
        f"{pct:.0f}%",
        f"{curr:,} de {prev:,} avanzaron",
    ), unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline de no-asignados · kanban estilo CRM
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(
    f"<h2>Pipeline de no-asignados · alerta &gt; {MAX_DIAS_SIN_ASIGNAR} días</h2>",
    unsafe_allow_html=True,
)

# IMPORTANTE: ignoramos filtros del sidebar — queremos ver TODO lo no asignado.
df_no_asig_all = df_t[df_t["pipeline"].astype(str) == PIPELINE_ORIGEN].copy()
df_no_asig_all = df_no_asig_all.sort_values("dias_desde_creacion", ascending=False)

# Layout dos columnas: filtro a la izquierda + kanban a la derecha
side_col, board_col = st.columns([1, 5])

with side_col:
    st.markdown(
        f"<div style='color:{MED};font-weight:600;font-size:0.75rem;"
        f"text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px'>"
        f"Filtro</div>",
        unsafe_allow_html=True,
    )
    kanban_mode = st.radio(
        "kanban_mode",
        ["Asignables", "No asignables"],
        index=0,
        label_visibility="collapsed",
    )

    if kanban_mode == "Asignables":
        df_kanban = df_no_asig_all[df_no_asig_all["es_asignable_estado"]].copy()
        cols_order: list[tuple[str, str]] = list(ESTADOS_ASIGNABLES.items())
    else:
        df_kanban = df_no_asig_all[~df_no_asig_all["es_asignable_estado"]].copy()
        # Una columna por estado encontrado (top 6 para no saturar)
        top_estados = (
            df_kanban["estado_code"].fillna("").value_counts().head(6).index.tolist()
        )
        cols_order = [
            (code, estado_options.get(code, code) or "(sin estado)")
            for code in top_estados
        ]

    n_kanban = len(df_kanban)
    n_urgentes_k = int((df_kanban["dias_desde_creacion"] >= MAX_DIAS_SIN_ASIGNAR).sum())
    prom_dias_k = float(df_kanban["dias_desde_creacion"].mean()) if n_kanban else 0.0
    st.markdown(kpi_card("Total", f"{n_kanban:,}", f"{kanban_mode.lower()}"),
                unsafe_allow_html=True)
    st.markdown(_alarm_card(
        f"Urgentes ≥ {MAX_DIAS_SIN_ASIGNAR}d",
        f"{n_urgentes_k:,}",
        "deben moverse hoy",
        n_urgentes_k > 0,
    ), unsafe_allow_html=True)
    st.markdown(kpi_card("Días promedio", f"{prom_dias_k:.1f}",
                         "desde creación"), unsafe_allow_html=True)


def _kanban_card(row) -> str:
    """Card estilo Pipefy/Trello: ID grande, estado, envios, dias sin asignar.

    Diseño:
      - Header: deal_id (#xxxx) + badge dias (rojo si urgente, gris si no)
      - Body: estado del negocio (label legible) + nombre breve
      - Footer: chip envíos + chip interaccion + tel breve
    """
    dias = float(row.get("dias_desde_creacion") or 0)
    urgent = dias >= MAX_DIAS_SIN_ASIGNAR
    border_color = RED if urgent else MED
    bg_color = "#fff7f7" if urgent else WHITE
    name = str(row.get("dealname") or "(sin nombre)")[:38]
    deal_id = str(row.get("hs_object_id") or "")
    estado_label = str(row.get("estado_label") or "—")
    flag = int(row.get("preofertaflag1") or 0)
    inter = str(row.get("interaccion") or "")
    phone = str(row.get("phone") or "").strip()
    phone_short = phone[-10:] if len(phone) >= 10 else (phone or "—")

    # Badge de dias
    days_bg = RED if urgent else "#eee5fa"
    days_fg = WHITE if urgent else DEEP
    days_text = f"{dias:.1f}d" + (" URG" if urgent else "")

    # Badge de envios
    envios_color = GREEN_DARK if flag >= 1 else "#9aa0a6"
    envios_text = f"{flag}/{MAX_ENVIOS}" if flag >= 1 else "Sin enviar"
    envios_icon = "✉" if flag >= 1 else "✗"

    # Badge de interaccion
    inter_html = ""
    if inter == "Quiero oferta":
        inter_html = (f"<span style='background:{GREEN_DARK};color:#fff;padding:2px 7px;"
                      f"border-radius:10px;font-size:0.65rem;font-weight:600'>oferta</span>")
    elif inter == "Tengo preguntas":
        inter_html = (f"<span style='background:{ACCENT};color:#fff;padding:2px 7px;"
                      f"border-radius:10px;font-size:0.65rem;font-weight:600'>preguntas</span>")
    elif inter == "Error":
        inter_html = (f"<span style='background:{RED};color:#fff;padding:2px 7px;"
                      f"border-radius:10px;font-size:0.65rem;font-weight:600'>error</span>")

    return (
        f"<div style='background:{bg_color};border:1px solid #ece4f6;"
        f"border-left:3px solid {border_color};border-radius:8px;"
        f"padding:10px 12px;margin-bottom:8px;"
        f"box-shadow:0 1px 4px rgba(46,17,71,0.06);"
        f"color:{DEEP};font-size:0.78rem'>"

        # Header: deal_id + dias
        f"<div style='display:flex;justify-content:space-between;align-items:center;"
        f"margin-bottom:6px'>"
        f"<div style='font-family:monospace;color:{MED};font-size:0.7rem;font-weight:600'>"
        f"#{deal_id}</div>"
        f"<div style='background:{days_bg};color:{days_fg};padding:2px 7px;"
        f"border-radius:10px;font-size:0.65rem;font-weight:700'>{days_text}</div>"
        f"</div>"

        # Nombre del lead
        f"<div style='font-weight:600;font-size:0.85rem;line-height:1.25;"
        f"margin-bottom:6px;color:{DEEP}'>{name}</div>"

        # Estado (label)
        f"<div style='background:{PALE};color:{DEEP};padding:3px 8px;"
        f"border-radius:6px;font-size:0.7rem;font-weight:500;"
        f"display:inline-block;margin-bottom:8px'>● {estado_label}</div>"

        # Footer chips
        f"<div style='display:flex;gap:6px;align-items:center;flex-wrap:wrap'>"
        f"<span style='background:{envios_color};color:#fff;padding:2px 7px;"
        f"border-radius:10px;font-size:0.65rem;font-weight:600'>{envios_icon} {envios_text}</span>"
        f"{inter_html}"
        f"<span style='color:#888;font-size:0.65rem;margin-left:auto'>📞 {phone_short}</span>"
        f"</div>"

        f"</div>"
    )


with board_col:
    if not cols_order or n_kanban == 0:
        st.info(
            "No hay deals en este filtro. "
            + ("¡Limpio! Nada urgente sin asignar." if kanban_mode == "Asignables"
               else "Ningún deal no-asignable en el rango.")
        )
    else:
        board_cols = st.columns(len(cols_order))
        for (code, label), col in zip(cols_order, board_cols):
            sub = df_kanban[df_kanban["estado_code"] == code].sort_values(
                "dias_desde_creacion", ascending=False
            )
            n_col = len(sub)
            n_urg_col = int((sub["dias_desde_creacion"] >= MAX_DIAS_SIN_ASIGNAR).sum())
            with col:
                accent = RED if n_urg_col > 0 else PRIMARY
                # Header columna con contador y porcentaje del filtro
                pct_col = n_col / max(1, n_kanban) * 100
                st.markdown(
                    f"<div style='background:{DEEP};color:#fff;"
                    f"padding:10px 12px;border-radius:8px 8px 0 0;"
                    f"border-bottom:3px solid {accent};margin-bottom:10px'>"
                    f"<div style='display:flex;justify-content:space-between;"
                    f"align-items:flex-start;gap:8px'>"
                    f"<div style='font-size:0.78rem;font-weight:700;"
                    f"line-height:1.2;flex:1'>{label}</div>"
                    f"<div style='background:{accent};color:#fff;padding:1px 7px;"
                    f"border-radius:10px;font-size:0.7rem;font-weight:700'>{n_col}</div>"
                    f"</div>"
                    f"<div style='font-size:0.65rem;opacity:0.8;margin-top:4px;"
                    f"letter-spacing:.04em'>{pct_col:.0f}% del filtro · "
                    f"{n_urg_col} urgentes</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if n_col == 0:
                    st.markdown(
                        f"<div style='background:{WHITE};border:1px dashed #d9cfee;"
                        f"border-radius:8px;color:{MED};font-size:0.72rem;"
                        f"font-style:italic;padding:14px;text-align:center'>"
                        f"Sin deals en este estado</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    # Contenedor con scroll vertical estilo Pipefy: todas las
                    # cards quedan adentro y el usuario hace scroll dentro de
                    # la columna sin saturar la página.
                    cards_html = "".join(_kanban_card(row) for _, row in sub.iterrows())
                    st.markdown(
                        f"<div style='max-height:70vh;overflow-y:auto;"
                        f"padding-right:6px;scrollbar-width:thin;"
                        f"scrollbar-color:{MED} {PALE}'>"
                        f"{cards_html}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )


# ─────────────────────────────────────────────────────────────────────────────
# Tracking Supabase · envíos, interacciones, asignaciones automáticas
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Tracking Supabase · envíos, interacciones y asignaciones</h2>", unsafe_allow_html=True)

# Restringimos las tablas Supabase a los deal_id que pertenecen al tratamiento
# del rango (df_t en HubSpot). Esto evita contaminar con pruebas o con leads
# fuera del experimento.
deal_ids_t = set(df_t["hs_object_id"].astype(str)) if "hs_object_id" in df_t.columns else set()
df_sends_t = sb_src.filter_deals(df_sends, deal_ids_t) if not df_sends.empty else df_sends
df_inter_t = sb_src.filter_deals(df_interactions, deal_ids_t) if not df_interactions.empty else df_interactions
df_asig_t = sb_src.filter_deals(df_assignments, deal_ids_t) if not df_assignments.empty else df_assignments

sb1, sb2, sb3, sb4 = st.columns(4)
sb1.markdown(kpi_card(
    "Envíos WA registrados",
    f"{len(df_sends_t):,}",
    "supabase.whatsapp_sends",
), unsafe_allow_html=True)
sb2.markdown(kpi_card(
    "Interacciones del cliente",
    f"{len(df_inter_t):,}",
    "supabase.deal_interactions",
), unsafe_allow_html=True)
sb3.markdown(kpi_card(
    "Asignaciones automáticas",
    f"{len(df_asig_t):,}",
    "supabase.deal_assignments",
), unsafe_allow_html=True)
ultimo_envio = (
    df_sends_t["sent_at"].max().strftime("%Y-%m-%d %H:%M")
    if not df_sends_t.empty else "—"
)
sb4.markdown(kpi_card("Último envío WA", ultimo_envio, "max(sent_at)"),
             unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Gráfico 1: Distribución de envíos reales por número de mensaje
#   (data Supabase whatsapp_sends, NO HubSpot flag)
# ---------------------------------------------------------------------------
if not df_sends_t.empty and "send_number" in df_sends_t.columns:
    s_send = pd.to_numeric(df_sends_t["send_number"], errors="coerce").fillna(0).astype(int)
    s_send = s_send.clip(0, MAX_ENVIOS)
    send_counts = s_send.value_counts().reindex(range(1, MAX_ENVIOS + 1), fill_value=0)

    SEND_LABELS = {
        1: "Mensaje 1 (día 1)",
        2: "Mensaje 2 (día 2)",
        3: "Mensaje 3 (día 3)",
        4: "Mensaje 4 (día 4)",
    }
    s_labels = [SEND_LABELS[i] for i in range(1, MAX_ENVIOS + 1)]
    s_vals = [int(send_counts.get(i, 0)) for i in range(1, MAX_ENVIOS + 1)]
    total_sends = sum(s_vals)
    s_pct = [(v / total_sends * 100) if total_sends else 0 for v in s_vals]

    fig_sends = go.Figure(go.Bar(
        x=s_labels, y=s_vals,
        marker_color=[GREEN_LIGHT, "#7eb37e", "#1f7a2a", GREEN_DARK],
        text=[f"{v:,}<br><span style='font-size:0.7rem;opacity:0.85'>{p:.1f}%</span>"
              for v, p in zip(s_vals, s_pct)],
        textposition="outside",
        textfont=dict(size=11, color=DEEP),
        hovertemplate="<b>%{x}</b><br>%{y:,} envíos<extra></extra>",
    ))
    fig_sends.update_layout(
        title=dict(text=f"Envíos realizados por número de mensaje · total {total_sends:,}",
                   font=dict(size=13, color=DEEP, family="Inter")),
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=11),
        height=320, margin=dict(l=10, r=10, t=50, b=10),
        xaxis=dict(gridcolor="#ede8f5"),
        yaxis=dict(title="Envíos", gridcolor="#ede8f5", tickformat=",d"),
    )
    st.plotly_chart(fig_sends, use_container_width=True)
else:
    st.info("Aún no hay envíos registrados en `whatsapp_sends`.")


# ---------------------------------------------------------------------------
# Gráfico 2: Timeline de envíos por día (todas las plantillas)
# ---------------------------------------------------------------------------
if not df_sends_t.empty and "sent_at" in df_sends_t.columns:
    df_timeline = df_sends_t.copy()
    df_timeline["dia"] = pd.to_datetime(df_timeline["sent_at"], utc=True, errors="coerce").dt.date
    by_day = (
        df_timeline.dropna(subset=["dia"])
        .groupby(["dia", "send_number"]).size()
        .reset_index(name="envios")
    )
    if not by_day.empty:
        fig_timeline = go.Figure()
        for sn, color in zip([1, 2, 3, 4], [GREEN_LIGHT, "#7eb37e", "#1f7a2a", GREEN_DARK]):
            sub = by_day[by_day["send_number"] == sn]
            fig_timeline.add_trace(go.Bar(
                x=sub["dia"], y=sub["envios"],
                name=f"Mensaje {sn}",
                marker_color=color,
                hovertemplate="<b>Mensaje " + str(sn) + "</b><br>%{x|%Y-%m-%d}<br>%{y} envíos<extra></extra>",
            ))
        fig_timeline.update_layout(
            barmode="stack",
            title=dict(text="Envíos por día · apilado por número de mensaje",
                       font=dict(size=13, color=DEEP, family="Inter")),
            paper_bgcolor=WHITE, plot_bgcolor=WHITE,
            font=dict(family="Inter, sans-serif", color=DEEP, size=11),
            height=300, margin=dict(l=10, r=10, t=50, b=10),
            xaxis=dict(title="Día", gridcolor="#ede8f5"),
            yaxis=dict(title="Envíos", gridcolor="#ede8f5", tickformat=",d"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=10)),
        )
        st.plotly_chart(fig_timeline, use_container_width=True)


# ---------------------------------------------------------------------------
# Gráfico 3 y 4: dónde clickeó / dónde se asignó (Supabase deal_*)
# ---------------------------------------------------------------------------
def _send_number_dist(df: pd.DataFrame, title: str, color: str):
    if df.empty or "send_number" not in df.columns:
        st.markdown(
            f"<div style='background:{WHITE};border:1px dashed #d9cfee;border-radius:8px;"
            f"padding:30px 16px;text-align:center;color:{MED};font-size:0.78rem'>"
            f"<b>{title}</b><br><span style='font-style:italic;font-size:0.72rem'>"
            f"Sin datos en el rango</span></div>",
            unsafe_allow_html=True,
        )
        return
    s = pd.to_numeric(df["send_number"], errors="coerce").fillna(0).astype(int).clip(0, MAX_ENVIOS)
    counts = s.value_counts().reindex(range(1, MAX_ENVIOS + 1), fill_value=0)
    labels = [f"Tras envío {i}" for i in range(1, MAX_ENVIOS + 1)]
    vals = [int(counts.get(i, 0)) for i in range(1, MAX_ENVIOS + 1)]
    fig = go.Figure(go.Bar(
        x=labels, y=vals,
        marker_color=color,
        text=[f"{v:,}" for v in vals], textposition="outside",
        textfont=dict(size=11, color=DEEP),
    ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color=DEEP, family="Inter")),
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=10),
        height=280, margin=dict(l=10, r=10, t=44, b=10),
        xaxis=dict(gridcolor="#ede8f5"),
        yaxis=dict(title="Eventos", gridcolor="#ede8f5", tickformat=",d"),
    )
    st.plotly_chart(fig, use_container_width=True)


dist_c1, dist_c2 = st.columns(2)
with dist_c1:
    if not df_inter_t.empty and "property" in df_inter_t.columns:
        df_inter_pos = df_inter_t[df_inter_t["property"].isin(
            ["quiero_recibir_oferta_formal", "tengo_preguntas"]
        )]
    else:
        df_inter_pos = df_inter_t
    _send_number_dist(
        df_inter_pos,
        "¿Tras cuál envío clickeó? (oferta + preguntas)",
        PRIMARY,
    )

with dist_c2:
    _send_number_dist(
        df_asig_t.rename(columns={"send_number_at_assignment": "send_number"})
        if not df_asig_t.empty else df_asig_t,
        "¿Tras cuál envío se asignó al MM?",
        ACCENT,
    )


# ---------------------------------------------------------------------------
# Gráfico 5: Razones de asignación automática
# ---------------------------------------------------------------------------
if not df_asig_t.empty and "reason" in df_asig_t.columns:
    reason_counts = df_asig_t["reason"].fillna("desconocido").value_counts()
    reason_df = reason_counts.reset_index()
    reason_df.columns = ["reason", "N"]
    fig_reason = go.Figure(go.Pie(
        labels=reason_df["reason"], values=reason_df["N"],
        hole=0.45, marker_colors=[GREEN_DARK, PRIMARY, MED, LIGHT, ACCENT, GREY],
        textinfo="label+percent+value", textfont_size=11,
    ))
    fig_reason.update_layout(
        paper_bgcolor=WHITE, showlegend=False,
        title=dict(text="Razón de la asignación automática",
                   font=dict(size=13, color=DEEP)),
        height=320, margin=dict(l=5, r=5, t=44, b=5),
    )
    st.plotly_chart(fig_reason, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Desglose · tabla detallada
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(f"<h2>Desglose ({len(df_t_f):,})</h2>", unsafe_allow_html=True)

# Conteo de aperturas
if not df_logs.empty and "uuid" in df_logs.columns:
    aperturas = df_logs.groupby("uuid").size().reset_index(name="n_aperturas")
    df_t_f = df_t_f.merge(
        aperturas.rename(columns={"uuid": "deal_uuid_lc"}),
        left_on=df_t_f["deal_uuid"].astype(str).str.lower(),
        right_on="deal_uuid_lc",
        how="left",
    )
df_t_f["n_aperturas"] = df_t_f.get("n_aperturas", pd.Series(dtype=int)).fillna(0).astype(int)

show_cols = [
    ("dealname",                       "Nombre"),
    ("phone",                          "Teléfono"),
    ("createdate",                     "Fecha creación"),
    ("dealstage",                      "Etapa"),
    ("owner_label",                    "Comercial"),
    ("prioridad_gestion_market_maker", "Categoría"),
    ("asignacion_label",               "Asignación"),
    ("interaccion",                    "Interacción"),
    ("preofertaflag1",                 "Envíos WA"),
    ("abrió_link",                     "Abrió link"),
    ("n_aperturas",                    "# aperturas"),
    ("quiero_recibir_oferta_formal",   "# Quiero oferta"),
    ("tengo_preguntas",                "# Preguntas"),
    ("error_preoferta",                "# Error"),
    ("precio_maximo_prestamo",         "Precio máximo"),
    ("nid",                            "nid"),
    ("deal_uuid",                      "deal_uuid"),
]
present = [(src, lab) for src, lab in show_cols if src in df_t_f.columns]
df_display = df_t_f[[s for s, _ in present]].rename(columns=dict(present))
st.dataframe(df_display, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Decisión · ÉXITO / INCONCLUSO / FRACASO  (con predicción)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Decisión · Marco post-experimento</h2>", unsafe_allow_html=True)

# Lógica:
#   ÉXITO     : ΔCVR > 0pp y volumen cierres T ≥ C
#   FRACASO   : ΔCVR < -3pp o cierres T < C * 0.95
#   INCONCLUSO: resto
if n_cierre_t == 0 and n_cierre_c == 0:
    decision = "INCONCLUSO"
    decision_reason = "Aún no hay cierres en ninguna rama. Esperar primeras semanas."
elif delta_cvr_pp >= 0 and n_cierre_t >= n_cierre_c:
    decision = "ÉXITO"
    decision_reason = (
        f"ΔCVR {delta_cvr_pp:+.1f}pp · "
        f"cierres T={n_cierre_t} ≥ C={n_cierre_c}. Recomendación: rollout."
    )
elif delta_cvr_pp <= UMBRAL_PP or n_cierre_t < n_cierre_c * 0.95:
    decision = "FRACASO"
    decision_reason = (
        f"ΔCVR {delta_cvr_pp:+.1f}pp ≤ {UMBRAL_PP}pp o cierres T={n_cierre_t} < C={n_cierre_c}. "
        "Recomendación: revertir e iterar copy."
    )
else:
    decision = "INCONCLUSO"
    decision_reason = (
        f"ΔCVR {delta_cvr_pp:+.1f}pp en rango neutro. "
        "Revisar mecanismos de transmisión, composición y entrega del mensaje."
    )

# Cards arriba: muestran el escenario actual resaltado en color, los otros 2 atenuados
SCENARIO_DEFS = [
    ("ÉXITO",      "Rollout",
     "CVR asignado→cierre del tratamiento ≥ control · Margin variance neutro o positivo · Sin caída en cierres absolutos",
     DEEP),
    ("INCONCLUSO", "Revisar mecanismos",
     "CVR ≈ control y margin variance ≈ control · Revisar entrega del mensaje, anclaje y composición de muestra",
     PRIMARY),
    ("FRACASO",    "Revertir",
     "CVR < control, o margin variance significativamente negativo · Conservar evidencia para iterar copy y precio",
     LIGHT),
]

cards_html = "<div style='display:flex;gap:14px;margin-bottom:14px'>"
for name, sub, body, color in SCENARIO_DEFS:
    active = (name == decision)
    cards_html += (
        f"<div style='flex:1;background:{color if active else PALE};"
        f"color:{WHITE if active else DEEP};"
        f"padding:18px 20px;border-radius:8px;"
        f"border:{('3px solid ' + color) if active else '1px solid ' + PALE};"
        f"opacity:{1.0 if active else 0.75}'>"
        f"<div style='font-size:1.4rem;font-weight:700;letter-spacing:0.05em'>{name}</div>"
        f"<div style='font-size:0.85rem;font-style:italic;opacity:0.9;margin:6px 0 10px 0'>{sub}</div>"
        f"<div style='font-size:0.78rem;line-height:1.5'>{body}</div>"
        f"</div>"
    )
cards_html += "</div>"
st.markdown(cards_html, unsafe_allow_html=True)

hl_color = next(c for n, _, _, c in SCENARIO_DEFS if n == decision)
st.markdown(
    f"<div style='background:{hl_color};color:white;padding:14px 18px;"
    f"border-radius:8px;margin-bottom:18px'>"
    f"<div style='font-size:0.75rem;opacity:0.85;letter-spacing:0.08em;"
    f"text-transform:uppercase'>Lectura actual</div>"
    f"<div style='font-size:1.6rem;font-weight:700;margin:4px 0'>{decision}</div>"
    f"<div style='font-size:0.85rem;opacity:0.95'>{decision_reason}</div>"
    f"</div>",
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────────────────────────────────────
# Predicción · cuándo se podría concluir
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(
    "<h3 style='margin-top:10px;color:" + DEEP + ";'>Predicción de cierre del experimento</h3>",
    unsafe_allow_html=True,
)

# Heurística: con CVR ~1% necesitamos del orden de 25–30 cierres por rama para
# detectar Δ ~0.5–1pp con poder razonable (alpha 5%, power 80%, two-sided).
TARGET_CIERRES_TRAT = 25
exp_start = pd.to_datetime(EXPERIMENT.start_date)
today = pd.Timestamp(datetime.now().date())
dias_corridos = max(1, (today - exp_start).days)

# Ritmo actual = cierres acumulados / días corridos. Si aún no hay cierres,
# asumimos un ritmo conservador en función del histórico de asignaciones.
ritmo_cierres_dia_t = n_cierre_t / dias_corridos if n_cierre_t > 0 else 0.0
faltantes_t = max(0, TARGET_CIERRES_TRAT - n_cierre_t)
if ritmo_cierres_dia_t > 0:
    dias_eta_t = faltantes_t / ritmo_cierres_dia_t
    eta_date = today + pd.Timedelta(days=int(dias_eta_t))
    eta_label = eta_date.strftime("%Y-%m-%d")
    eta_sub = f"~{int(dias_eta_t)} días al ritmo actual de {ritmo_cierres_dia_t:.2f} cierres/día"
else:
    dias_eta_t = None
    eta_label = "—"
    eta_sub = "Aún sin cierres; ETA pendiente"

# Construir serie de cierres acumulados proyectados.
# Real: hoy mismo, una marca con el n_cierre_t y n_cierre_c.
# Proyectada: linea recta hasta eta_date (target).
fig_pred = go.Figure()

# Punto actual real
fig_pred.add_trace(go.Scatter(
    x=[exp_start, today], y=[0, n_cierre_t],
    mode="lines+markers", line=dict(color=GREEN_DARK, width=3),
    marker=dict(size=8),
    name=f"Tratamiento (real · {n_cierre_t})",
    hovertemplate="%{x|%Y-%m-%d}: %{y} cierres<extra>Tratamiento</extra>",
))
fig_pred.add_trace(go.Scatter(
    x=[exp_start, today], y=[0, n_cierre_c],
    mode="lines+markers", line=dict(color=PRIMARY, width=3),
    marker=dict(size=8),
    name=f"Control (real · {n_cierre_c})",
    hovertemplate="%{x|%Y-%m-%d}: %{y} cierres<extra>Control</extra>",
))

# Proyección lineal
if ritmo_cierres_dia_t > 0 and dias_eta_t is not None:
    proj_x = pd.date_range(today, today + pd.Timedelta(days=int(dias_eta_t)), periods=8)
    proj_y = [n_cierre_t + ritmo_cierres_dia_t * (d - today).days for d in proj_x]
    fig_pred.add_trace(go.Scatter(
        x=proj_x, y=proj_y,
        mode="lines", line=dict(color=GREEN_DARK, width=2, dash="dot"),
        name="Proyección Tratamiento",
        hovertemplate="%{x|%Y-%m-%d}: %{y:.1f} proyectados<extra>Proyección T</extra>",
    ))

# Línea umbral
fig_pred.add_hline(
    y=TARGET_CIERRES_TRAT,
    line_dash="dash", line_color=MED,
    annotation_text=f"Umbral significancia · {TARGET_CIERRES_TRAT} cierres T",
    annotation_position="top right",
    annotation_font=dict(color=MED, size=10),
)

fig_pred.update_layout(
    paper_bgcolor=WHITE, plot_bgcolor=WHITE,
    font=dict(family="Inter, sans-serif", color=DEEP, size=11),
    height=340, margin=dict(l=10, r=10, t=10, b=10),
    xaxis=dict(title="Fecha", gridcolor="#ede8f5"),
    yaxis=dict(title="Cierres acumulados", gridcolor="#ede8f5",
               rangemode="nonnegative"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=10)),
)
st.plotly_chart(fig_pred, use_container_width=True)

# KPIs de predicción
p1, p2, p3, p4 = st.columns(4)
p1.markdown(kpi_card("Días corridos", f"{dias_corridos:,}",
                     f"desde {EXPERIMENT.start_date}"),
            unsafe_allow_html=True)
p2.markdown(kpi_card("Ritmo cierres T", f"{ritmo_cierres_dia_t:.2f}/día",
                     f"{n_cierre_t} cierres / {dias_corridos} días"),
            unsafe_allow_html=True)
p3.markdown(kpi_card("Target cierres T", f"{TARGET_CIERRES_TRAT:,}",
                     f"faltan {faltantes_t}"),
            unsafe_allow_html=True)
p4.markdown(kpi_card("ETA conclusión", eta_label, eta_sub),
            unsafe_allow_html=True)

with st.expander("Cómo se calcula la predicción"):
    st.markdown(
        f"""
- **Ritmo cierres/día** = `cierres acumulados / días desde el lanzamiento ({EXPERIMENT.start_date})`.
- **Target** = `{TARGET_CIERRES_TRAT}` cierres acumulados en el tratamiento. Heurística para
  CVR base ~1% con alpha 5%, power 80%; se ajusta a medida que llegan datos.
- **ETA** = `(target - cierres actuales) / ritmo cierres/día`. Asume ritmo constante; no
  modela estacionalidad ni curva de aprendizaje.
- **Cuándo ignorar la ETA**: si el ritmo es < 0.2 cierres/día durante las primeras 2 semanas,
  el experimento probablemente no llegará a significancia y conviene rediseñar el copy o
  aumentar el share del tratamiento.
"""
    )


st.divider()
st.caption(
    f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    "TTL cache: 120s (Sheets) · 24h (HS + BQ). "
    "Funnel etapas desde sellers-main-prod.bi_mx.seguimiento_funnel_mex · "
    "nid mapping vía sellers-main-prod.hubspot.deals."
)
