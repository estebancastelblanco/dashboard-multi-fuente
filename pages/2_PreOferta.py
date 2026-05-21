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

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.markdown(kpi_card("Universo", f"{n_universo:,}", f"Seller MX · {since_iso} → {until_iso}"), unsafe_allow_html=True)
c2.markdown(kpi_card("Enviados WA", f"{n_wa:,}", f"{int(WA_DELIVERY*100)}% del universo"), unsafe_allow_html=True)
c3.markdown(kpi_card("Abrieron link", f"{n_opened:,}", f"{n_opened/max(1,n_wa):.0%} de enviados"), unsafe_allow_html=True)
c4.markdown(kpi_card("Asignados", f"{n_asig:,}", f"{n_asig/max(1,n_universo):.0%}"), unsafe_allow_html=True)
c5.markdown(kpi_card("Quiero oferta", f"{n_oferta:,}", "CTA primario"), unsafe_allow_html=True)
c6.markdown(kpi_card("Tengo preguntas", f"{n_preg:,}", "CTA secundario"), unsafe_allow_html=True)


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


n_cita_agendada = _count_stage("Cita Agendada (hubspot)")
n_visita = _count_stage("Visita Efectuada (hubspot)")
n_pre_comite = _count_stage("Pre-comite validado")
n_aprobado = _count_stage("Aprobado General")
n_pendiente_oferta = _count_stage("Pendiente respuesta oferta")
n_aceptada = _count_stage("Acepto Oferta - Pendiente firma")
n_cierre = _count_stage("Cierre - Comprado") + _count_stage("Cierre  OCD")

stages = [
    ("Universo (Seller MX)",          n_universo,         f"HubSpot · contacto_digital=seller"),
    ("Enviados WA",                   n_wa,               f"Estimado · {int(WA_DELIVERY*100)}% × Universo"),
    ("Abrieron link",                 n_opened,           "Sheets LOGS · dedup por uuid"),
    ("Quiero oferta",                 n_oferta,           "HS quiero_recibir_oferta_formal > 0"),
    ("Tengo preguntas",               n_preg,             "HS tengo_preguntas > 0 (sin oferta)"),
    ("Asignados",                     n_asig,             "owner≠JuanQ + prioridad_gestion_mm"),
    ("Cita Agendada",                 n_cita_agendada,    "BQ · Cita Agendada (hubspot)"),
    ("Visita Efectuada",              n_visita,           "BQ · Visita Efectuada (hubspot)"),
    ("Pre-comité validado",           n_pre_comite,       "BQ · Pre-comite validado"),
    ("Aprobado General",              n_aprobado,         "BQ · Aprobado General"),
    ("Pendiente respuesta oferta",    n_pendiente_oferta, "BQ · Pendiente respuesta oferta"),
    ("Acepto oferta · pendiente firma", n_aceptada,       "BQ · Acepto Oferta - Pendiente firma"),
    ("Cierre",                        n_cierre,           "BQ · Cierre - Comprado + Cierre OCD"),
]

f_labels = [s[0] for s in stages]
f_vals = [s[1] for s in stages]
f_sources = [s[2] for s in stages]
# Gradiente morado → verde
n = len(stages)
palette = [DEEP, "#3a1956", PRIMARY, MED, "#9050c0", ACCENT, LIGHT, "#bd8be3", PALE,
           GREEN_LIGHT, "#7eb37e", GREEN_DARK, "#0f5535"]
f_colors = palette[:n]
f_text = [
    f"{v:,}  ({v/f_vals[i-1]*100:.0f}%)" if i > 0 and f_vals[i-1] > 0 else f"{v:,}"
    for i, v in enumerate(f_vals)
]
nonzero = [v for v in f_vals if v > 0]
use_log = (max(f_vals) if f_vals else 0) > 100 and (min(nonzero) if nonzero else 0) > 0
fig_funnel = go.Figure(go.Bar(
    x=f_vals, y=f_labels, orientation="h",
    marker_color=f_colors, text=f_text,
    textposition="outside", textfont=dict(size=11, color=DEEP),
    customdata=f_sources,
    hovertemplate="<b>%{y}</b><br>%{x:,} · %{customdata}<extra></extra>",
))
fig_funnel.update_layout(
    paper_bgcolor=WHITE, plot_bgcolor=WHITE,
    font=dict(family="Inter, sans-serif", color=DEEP, size=11),
    height=520, margin=dict(l=10, r=240, t=10, b=10),
    xaxis=dict(type="log" if use_log else "linear",
               title="Clientes" + (" (log)" if use_log else ""),
               gridcolor="#ede8f5", tickformat=",d"),
    yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig_funnel, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Funnel 2 · comparativo A/B (control vs tratamiento)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Comparativa A/B · Control vs Tratamiento</h2>", unsafe_allow_html=True)
st.caption(
    "Control = leads MX con contacto_digital ≠ seller (gabi, chatbot, null), "
    "filtrados a categoría B para comparabilidad. Tratamiento = seller (este experimento). "
    "Las etapas se calculan sobre `seguimiento_funnel_mex` con los mismos filtros de fecha."
)

# Construir el conjunto de nids del control (categoría B equivalente).
# Filtramos en cliente porque la API HS no expone categoria_comercial directo
# con consistencia → usamos prioridad_gestion_mm == 'B' como proxy del doc.
df_c_b = pd.DataFrame()
if not df_c.empty:
    df_c_b = df_c[df_c["prioridad_gestion_market_maker"].astype(str).str.upper() == "B"].copy()
    # nid mapping para control
    uuids_c = tuple(sorted(df_c_b["deal_uuid"].dropna().astype(str).str.lower().unique().tolist()))
    if uuids_c:
        try:
            df_nid_c = load_nid_mapping(uuids_c)
            df_c_b["deal_uuid_lc"] = df_c_b["deal_uuid"].astype(str).str.lower()
            df_nid_c["deal_uuid_lc"] = df_nid_c["deal_uuid"].astype(str).str.lower()
            df_c_b = df_c_b.merge(df_nid_c[["deal_uuid_lc", "nid"]], on="deal_uuid_lc", how="left", suffixes=("_hs", ""))
        except Exception:
            df_c_b["nid"] = None

nids_c = set(df_c_b["nid"].dropna().astype(int).tolist()) if "nid" in df_c_b.columns else set()

try:
    df_funnel_c = load_funnel_mex(tuple(nids_c), since_iso, until_iso) if nids_c else pd.DataFrame()
except Exception:
    df_funnel_c = pd.DataFrame()


def _count_stage_in(df_fun: pd.DataFrame, stage: str, nids: set) -> int:
    if df_fun.empty:
        return 0
    sub = df_fun[df_fun["valor"] == stage]
    if not nids:
        return int(sub["nid"].nunique())
    return int(sub[sub["nid"].isin(nids)]["nid"].nunique())


# Etapas comparables (mismas para ambos)
COMPARE_STAGES = [
    ("Lead llega",        "_universe"),
    ("Asignación",        "Primer asignacion"),
    ("Cita Agendada",     "Cita Agendada (hubspot)"),
    ("Visita Efectuada",  "Visita Efectuada (hubspot)"),
    ("Pre-comité",        "Pre-comite validado"),
    ("Aprobado",          "Aprobado General"),
    ("Oferta",            "Pendiente respuesta oferta"),
    ("Cierre",            "Cierre - Comprado"),
]


def _build_funnel_counts(df_fun: pd.DataFrame, nids: set, universe: int) -> list[int]:
    counts = []
    for label, stage in COMPARE_STAGES:
        if stage == "_universe":
            counts.append(universe)
        else:
            counts.append(_count_stage_in(df_fun, stage, nids))
    return counts


vals_t = _build_funnel_counts(df_funnel, nids_f, n_universo)
vals_c = _build_funnel_counts(df_funnel_c, nids_c, len(df_c_b))

col_c, col_t = st.columns(2)
labels = [s[0] for s in COMPARE_STAGES]


def _vfunnel(values: list[int], title: str, color_start: str, color_end: str):
    n = len(values)
    palette_v = []
    for i in range(n):
        ratio = i / max(1, n - 1)
        # Interpolación lineal entre dos hex colors
        c1 = tuple(int(color_start.lstrip("#")[j:j+2], 16) for j in (0, 2, 4))
        c2 = tuple(int(color_end.lstrip("#")[j:j+2], 16) for j in (0, 2, 4))
        mix = tuple(int(c1[k] + (c2[k] - c1[k]) * ratio) for k in range(3))
        palette_v.append(f"rgb({mix[0]},{mix[1]},{mix[2]})")
    text = [
        f"{v:,}" + (f"  ({v/values[i-1]*100:.0f}%)" if i > 0 and values[i-1] > 0 else "")
        for i, v in enumerate(values)
    ]
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker_color=palette_v, text=text,
        textposition="outside", textfont=dict(size=10, color=DEEP),
    ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color=DEEP, family="Inter")),
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=10),
        height=360, margin=dict(l=10, r=180, t=44, b=10),
        xaxis=dict(title="Leads", gridcolor="#ede8f5", tickformat=",d"),
        yaxis=dict(autorange="reversed"),
    )
    return fig


with col_c:
    st.plotly_chart(
        _vfunnel(vals_c, "Funnel actual · control (B, sin pre-oferta)", PRIMARY, DEEP),
        use_container_width=True,
    )
with col_t:
    st.plotly_chart(
        _vfunnel(vals_t, "Funnel con pre-oferta · tratamiento", LIGHT, GREEN_DARK),
        use_container_width=True,
    )

cvr_c = vals_c[-1] / vals_c[1] if len(vals_c) > 1 and vals_c[1] > 0 else 0.0
cvr_t = vals_t[-1] / vals_t[1] if len(vals_t) > 1 and vals_t[1] > 0 else 0.0
st.caption(
    f"CVR asignación → cierre · Control: {cvr_c*100:.2f}% ({vals_c[-1]}/{vals_c[1]}) · "
    f"Tratamiento: {cvr_t*100:.2f}% ({vals_t[-1]}/{vals_t[1]}). "
    "Aún muy temprano — los cierres tardan semanas en aparecer."
)


# ─────────────────────────────────────────────────────────────────────────────
# Distribuciones
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Distribuciones</h2>", unsafe_allow_html=True)

col_int, col_estado, col_owner = st.columns(3)


def _hbar(series: pd.Series, title: str, palette_start: str, palette_end: str, top: int = 10):
    c = series.fillna("(sin valor)").astype(str).replace("", "(sin valor)").value_counts().reset_index()
    c.columns = ["cat", "N"]
    c = c.head(top).sort_values("N", ascending=True)
    fig = go.Figure(go.Bar(
        x=c["N"], y=c["cat"], orientation="h",
        marker=dict(
            color=c["N"],
            colorscale=[[0, palette_start], [1, palette_end]],
            showscale=False,
        ),
        text=c["N"], textposition="outside", textfont_size=10,
    ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color=DEEP, family="Inter")),
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=10),
        height=max(260, len(c) * 24 + 80),
        margin=dict(l=10, r=50, t=44, b=10),
        yaxis=dict(gridcolor="#ede8f5"),
        xaxis=dict(gridcolor="#ede8f5"),
    )
    return fig


with col_int:
    st.plotly_chart(
        _hbar(df_t_f["interaccion"], "Interacción post-apertura (jerárquica)", PALE, PRIMARY, top=4),
        use_container_width=True,
    )

with col_estado:
    if "dealstage" in df_t_f.columns:
        st.plotly_chart(
            _hbar(df_t_f["dealstage"], "Estado del negocio (top 10)", PALE, MED),
            use_container_width=True,
        )

with col_owner:
    # owner_label ya viene cacheado desde enrich_deals_df
    st.plotly_chart(
        _hbar(df_t_f["owner_label"], "Comerciales (top 10)", PALE, ACCENT),
        use_container_width=True,
    )


# Pie control vs tratamiento (95/5)
col_ab, col_cat = st.columns(2)
with col_ab:
    n_t_total = len(df_t)
    n_c_total = len(df_c)
    total = n_t_total + n_c_total
    if total > 0:
        share_t = n_t_total / total * 100
        share_c = n_c_total / total * 100
        fig_ab = go.Figure(go.Pie(
            labels=["Tratamiento (Seller)", "Control (no-Seller)"],
            values=[n_t_total, n_c_total],
            hole=0.42,
            marker_colors=[GREEN_DARK, PRIMARY],
            textinfo="label+percent+value", textfont_size=11,
        ))
        target_line = f"Target 95/5 · actual {share_c:.1f}/{share_t:.1f}"
        fig_ab.update_layout(
            paper_bgcolor=WHITE, showlegend=False,
            title=dict(text=f"Split A/B · {target_line}", font=dict(size=13, color=DEEP)),
            height=320, margin=dict(l=5, r=5, t=44, b=5),
        )
        st.plotly_chart(fig_ab, use_container_width=True)

with col_cat:
    if "prioridad_gestion_market_maker" in df_t_f.columns:
        cat_counts = (
            df_t_f["prioridad_gestion_market_maker"].fillna("(sin asignar)").astype(str)
            .value_counts().reset_index()
        )
        cat_counts.columns = ["Categoría", "N"]
        fig_cat = go.Figure(go.Pie(
            labels=cat_counts["Categoría"], values=cat_counts["N"],
            hole=0.42, marker_colors=[ACCENT, PRIMARY, MED, GREY],
            textinfo="label+percent+value", textfont_size=11,
        ))
        fig_cat.update_layout(
            paper_bgcolor=WHITE, showlegend=False,
            title=dict(text="Categoría comercial (tratamiento)", font=dict(size=13, color=DEEP)),
            height=320, margin=dict(l=5, r=5, t=44, b=5),
        )
        st.plotly_chart(fig_cat, use_container_width=True)


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
st.caption(
    "Cuántos leads están esperando o ya recibieron cada uno de los 4 envíos. "
    "La flag `preofertaflag1` en HubSpot se incrementa después de cada envío "
    "(workflow Infobip → /api/whatsapp/sent). 0 = aún sin enviar el primero."
)

flag_series = (
    df_t_f["preofertaflag1"].fillna(0).astype(int).clip(lower=0, upper=MAX_ENVIOS)
)
flag_counts = flag_series.value_counts().reindex(range(0, MAX_ENVIOS + 1), fill_value=0)

LABELS_FLAG = {
    0: "Sin enviar",
    1: "Recibió 1 · esperando 2",
    2: "Recibió 2 · esperando 3",
    3: "Recibió 3 · esperando 4",
    4: "Recibió los 4",
}

env_cols = st.columns(MAX_ENVIOS + 1)
for i, col in enumerate(env_cols):
    col.markdown(
        kpi_card(LABELS_FLAG[i], f"{int(flag_counts.get(i, 0)):,}",
                 "preofertaflag1 = " + str(i)),
        unsafe_allow_html=True,
    )

# Bar chart horizontal con gradiente
flag_labels = [LABELS_FLAG[i] for i in range(0, MAX_ENVIOS + 1)]
flag_vals = [int(flag_counts.get(i, 0)) for i in range(0, MAX_ENVIOS + 1)]
flag_colors = [LIGHT, PALE, MED, PRIMARY, DEEP]
fig_envios = go.Figure(go.Bar(
    x=flag_vals, y=flag_labels, orientation="h",
    marker_color=flag_colors,
    text=[f"{v:,}" for v in flag_vals], textposition="outside",
    textfont=dict(size=11, color=DEEP),
))
fig_envios.update_layout(
    paper_bgcolor=WHITE, plot_bgcolor=WHITE,
    font=dict(family="Inter, sans-serif", color=DEEP, size=11),
    height=260, margin=dict(l=10, r=80, t=10, b=10),
    xaxis=dict(title="Leads", gridcolor="#ede8f5", tickformat=",d"),
    yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig_envios, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline de no-asignados · kanban estilo CRM
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(
    f"<h2>Pipeline de no-asignados · alerta &gt; {MAX_DIAS_SIN_ASIGNAR} días</h2>",
    unsafe_allow_html=True,
)
st.caption(
    f"Deals seller en pipeline ORIGEN ({PIPELINE_ORIGEN}) que aún no fueron "
    f"movidos al pipeline destino ({PIPELINE_DESTINO} · Market Maker MX). "
    "Solo son **asignables** los que están en estado: "
    + ", ".join(f"<em>{lab}</em>" for lab in ESTADOS_ASIGNABLES.values())
    + ". El resto debe permanecer en su flujo (pricing, descarte, etc.)",
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
    dias = float(row.get("dias_desde_creacion") or 0)
    urgent = dias >= MAX_DIAS_SIN_ASIGNAR
    accent = RED if urgent else PRIMARY
    bg = "#fff5f5" if urgent else WHITE
    name = str(row.get("dealname") or "(sin nombre)")[:42]
    flag = int(row.get("preofertaflag1") or 0)
    inter = str(row.get("interaccion") or "")
    inter_badge = ""
    if inter == "Quiero oferta":
        inter_badge = f"<span style='background:{GREEN_DARK};color:#fff;padding:2px 6px;border-radius:8px;font-size:0.65rem;margin-left:4px'>oferta</span>"
    elif inter == "Tengo preguntas":
        inter_badge = f"<span style='background:{ACCENT};color:#fff;padding:2px 6px;border-radius:8px;font-size:0.65rem;margin-left:4px'>preguntas</span>"
    elif inter == "Error":
        inter_badge = f"<span style='background:{RED};color:#fff;padding:2px 6px;border-radius:8px;font-size:0.65rem;margin-left:4px'>error</span>"
    urgent_badge = (
        f"<span style='background:{RED};color:#fff;padding:2px 6px;border-radius:8px;font-size:0.65rem;font-weight:700;margin-left:4px'>URG</span>"
        if urgent else ""
    )
    phone = str(row.get("phone") or "").strip() or "—"
    return (
        f"<div style='background:{bg};border-left:3px solid {accent};"
        f"border-radius:6px;padding:8px 10px;margin-bottom:8px;"
        f"box-shadow:0 1px 3px rgba(46,17,71,0.07);font-size:0.78rem;color:{DEEP}'>"
        f"<div style='font-weight:600;line-height:1.3;margin-bottom:4px'>"
        f"{name}{urgent_badge}{inter_badge}</div>"
        f"<div style='color:#666;font-size:0.7rem'>"
        f"📅 {dias:.1f}d &nbsp;·&nbsp; ✉️ {flag}/{MAX_ENVIOS} envíos &nbsp;·&nbsp; 📞 {phone}"
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
            sub = df_kanban[df_kanban["estado_code"] == code]
            n_col = len(sub)
            n_urg_col = int((sub["dias_desde_creacion"] >= MAX_DIAS_SIN_ASIGNAR).sum())
            with col:
                # Header columna
                accent = RED if n_urg_col > 0 else PRIMARY
                st.markdown(
                    f"<div style='background:{DEEP};color:#fff;"
                    f"padding:8px 10px;border-radius:6px;margin-bottom:10px;"
                    f"border-top:3px solid {accent}'>"
                    f"<div style='font-size:0.8rem;font-weight:700;line-height:1.2'>{label}</div>"
                    f"<div style='font-size:0.7rem;opacity:0.85;margin-top:2px'>"
                    f"{n_col} deals · {n_urg_col} urgentes</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                # Cards
                if n_col == 0:
                    st.markdown(
                        f"<div style='color:{MED};font-size:0.72rem;font-style:italic;"
                        f"padding:6px 4px'>Sin deals</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    cards_html = "".join(
                        _kanban_card(row) for _, row in sub.head(40).iterrows()
                    )
                    st.markdown(cards_html, unsafe_allow_html=True)
                    if n_col > 40:
                        st.caption(f"+{n_col - 40} más…")


# ─────────────────────────────────────────────────────────────────────────────
# Tracking Supabase · envíos, interacciones, asignaciones automáticas
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Tracking Supabase · envíos, interacciones y asignaciones</h2>", unsafe_allow_html=True)
st.caption(
    "Tablas pobladas en tiempo real por la landing (`whatsapp_sends`, "
    "`deal_interactions`, `deal_assignments`). Sirven como log inmutable y "
    "para responder ‘después de cuál envío fue que interactuó el cliente’."
)

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


# Distribución: en qué envío interactuó cada cliente (1..4)
def _send_number_dist(df: pd.DataFrame, title: str, color: str):
    if df.empty or "send_number" not in df.columns:
        st.caption(f"{title}: sin datos en el rango.")
        return
    s = pd.to_numeric(df["send_number"], errors="coerce").fillna(0).astype(int).clip(0, MAX_ENVIOS)
    counts = s.value_counts().reindex(range(1, MAX_ENVIOS + 1), fill_value=0)
    labels = [f"Después del envío {i}" for i in range(1, MAX_ENVIOS + 1)]
    vals = [int(counts.get(i, 0)) for i in range(1, MAX_ENVIOS + 1)]
    fig = go.Figure(go.Bar(
        x=vals, y=labels, orientation="h",
        marker_color=color,
        text=[f"{v:,}" for v in vals], textposition="outside",
        textfont=dict(size=10, color=DEEP),
    ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color=DEEP, family="Inter")),
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=10),
        height=240, margin=dict(l=10, r=60, t=44, b=10),
        xaxis=dict(title="Clientes", gridcolor="#ede8f5", tickformat=",d"),
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig, use_container_width=True)


dist_c1, dist_c2 = st.columns(2)
with dist_c1:
    # Filtrar interacciones positivas (oferta + preguntas, sin error)
    if not df_inter_t.empty and "property" in df_inter_t.columns:
        df_inter_pos = df_inter_t[df_inter_t["property"].isin(
            ["quiero_recibir_oferta_formal", "tengo_preguntas"]
        )]
    else:
        df_inter_pos = df_inter_t
    _send_number_dist(
        df_inter_pos,
        "¿En qué envío clickeó? (oferta + preguntas)",
        PRIMARY,
    )

with dist_c2:
    _send_number_dist(
        df_asig_t.rename(columns={"send_number_at_assignment": "send_number"})
        if not df_asig_t.empty else df_asig_t,
        "¿En qué envío se asignó al MM?",
        ACCENT,
    )


# Desglose de razones de asignación (interaction_* vs day4_*)
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
# Decisión · ÉXITO / INCONCLUSO / FRACASO
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

card_colors = {
    "ÉXITO":      (DEEP,  "Rollout"),
    "INCONCLUSO": (MED,   "Revisar mecanismos"),
    "FRACASO":    (LIGHT, "Revertir"),
}
hl_color, hl_sub = card_colors[decision]

st.markdown(
    f"""
<div style="display:flex;gap:14px;margin-bottom:14px">
  <div style="flex:1;background:{DEEP if decision=='ÉXITO' else PALE};color:{WHITE if decision=='ÉXITO' else DEEP};
              padding:18px 20px;border-radius:8px;border:{('3px solid '+DEEP) if decision=='ÉXITO' else '1px solid '+PALE}">
    <div style="font-size:1.4rem;font-weight:700;letter-spacing:0.05em">ÉXITO</div>
    <div style="font-size:0.85rem;font-style:italic;opacity:0.9;margin:6px 0 10px 0">Rollout</div>
    <ul style="font-size:0.78rem;padding-left:18px;margin:0;line-height:1.6">
      <li>CVR asignado→cierre del tratamiento ≥ control</li>
      <li>Margin variance neutro o positivo</li>
      <li>Sin caída en cierres absolutos del segmento</li>
    </ul>
  </div>
  <div style="flex:1;background:{PRIMARY if decision=='INCONCLUSO' else PALE};color:{WHITE if decision=='INCONCLUSO' else DEEP};
              padding:18px 20px;border-radius:8px;border:{('3px solid '+PRIMARY) if decision=='INCONCLUSO' else '1px solid '+PALE}">
    <div style="font-size:1.4rem;font-weight:700;letter-spacing:0.05em">INCONCLUSO</div>
    <div style="font-size:0.85rem;font-style:italic;opacity:0.9;margin:6px 0 10px 0">Revisar mecanismos</div>
    <ul style="font-size:0.78rem;padding-left:18px;margin:0;line-height:1.6">
      <li>CVR ≈ control y margin variance ≈ control</li>
      <li>Revisar entrega del mensaje, anclaje real en negociación y composición de muestra</li>
    </ul>
  </div>
  <div style="flex:1;background:{LIGHT if decision=='FRACASO' else PALE};color:{WHITE if decision=='FRACASO' else DEEP};
              padding:18px 20px;border-radius:8px;border:{('3px solid '+LIGHT) if decision=='FRACASO' else '1px solid '+PALE}">
    <div style="font-size:1.4rem;font-weight:700;letter-spacing:0.05em">FRACASO</div>
    <div style="font-size:0.85rem;font-style:italic;opacity:0.9;margin:6px 0 10px 0">Revertir</div>
    <ul style="font-size:0.78rem;padding-left:18px;margin:0;line-height:1.6">
      <li>CVR &lt; control, o margin variance significativamente negativo</li>
      <li>Conservar evidencia para iterar copy y fórmula del precio</li>
    </ul>
  </div>
</div>
<div style="background:{hl_color};color:white;padding:14px 18px;border-radius:8px;margin-top:6px">
  <div style="font-size:0.75rem;opacity:0.85;letter-spacing:0.08em;text-transform:uppercase">Lectura actual</div>
  <div style="font-size:1.6rem;font-weight:700;margin:4px 0">{decision}</div>
  <div style="font-size:0.85rem;opacity:0.95">{decision_reason}</div>
</div>
""",
    unsafe_allow_html=True,
)


st.divider()
st.caption(
    f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    "TTL cache: 120s (Sheets) · 24h (HS + BQ). "
    "Funnel etapas desde sellers-main-prod.bi_mx.seguimiento_funnel_mex · "
    "nid mapping vía sellers-main-prod.hubspot.deals."
)
