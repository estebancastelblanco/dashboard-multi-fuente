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
# Loaders — TTL largo (24h) para fuentes pesadas (HubSpot, BigQuery), corto
# para Sheets (cambia con cada submit). persist="disk" sobrevive a reload.
# ─────────────────────────────────────────────────────────────────────────────
DAY = 86400      # 24h
SHORT = 120      # 2 min


@st.cache_data(ttl=DAY, show_spinner="HubSpot · deals fakedoor…", persist="disk")
def load_hs_deals() -> pd.DataFrame:
    # Sin filtro de fecha — universo = todos los deals con flag_fakedoor.
    return hs_src.fetch_fakedoor_deals(since_iso=None)


@st.cache_data(ttl=DAY, show_spinner="HubSpot · catálogo de propiedades…", persist="disk")
def load_property_labels() -> dict[str, dict[str, str]]:
    """value→label por propiedad enum. Casi nunca cambia."""
    result: dict[str, dict[str, str]] = {}
    for prop in ("estado", "oportunidad_del_negocio"):
        try:
            result[prop] = hs_src.fetch_property_options(prop)
        except Exception:
            result[prop] = {}
    return result


@st.cache_data(ttl=SHORT, show_spinner="Leads del Sheet…")
def load_leads_with_scores() -> tuple[pd.DataFrame, dict]:
    df_raw = gs_src.fetch_tab("Leads")
    if df_raw.empty:
        return df_raw, {}
    return score_src.enrich_leads_with_scores(df_raw, tab="Leads", cedula_col="cedula")


@st.cache_data(ttl=SHORT, show_spinner="Entrevistas…")
def load_entrevistas() -> pd.DataFrame:
    df = gs_src.fetch_tab("Entrevista")
    if not df.empty and "telefono" in df.columns:
        df["telefono"] = df["telefono"].astype(str).str.strip()
        df["phone_norm"] = df["telefono"].apply(_norm_phone)
    return df


@st.cache_data(ttl=DAY, show_spinner="BigQuery · eventos de landing…", persist="disk")
def load_landing_events() -> pd.DataFrame:
    try:
        return bq_src.fetch_fakedoor_landing_events()
    except Exception as exc:
        st.warning(f"BigQuery falló: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=[
            "uuid", "total_events", "had_pages", "had_tracks",
            "visited_home", "visited_solicitud", "visited_consent", "reached_consent",
        ])


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
    labels_map = load_property_labels()
except Exception as exc:
    df_hs = pd.DataFrame()
    labels_map = {"estado": {}, "oportunidad_del_negocio": {}}
    st.warning(f"HubSpot no disponible: {type(exc).__name__}: {exc}")

df_bq = load_landing_events()


# ─────────────────────────────────────────────────────────────────────────────
# Decode internal IDs → labels en HubSpot
# ─────────────────────────────────────────────────────────────────────────────
if not df_hs.empty:
    estado_map = labels_map.get("estado", {})
    oport_map = labels_map.get("oportunidad_del_negocio", {})
    df_hs["estado_label"] = df_hs.get("estado", pd.Series(dtype=str)).map(estado_map).fillna(df_hs.get("estado"))
    df_hs["oportunidad_del_negocio_label"] = (
        df_hs.get("oportunidad_del_negocio", pd.Series(dtype=str))
        .map(oport_map)
        .fillna(df_hs.get("oportunidad_del_negocio"))
    )
    df_hs["fuente"] = df_hs.apply(hs_src.compute_fuente, axis=1)
    df_hs["phone_norm"] = df_hs.get("phone", pd.Series(dtype=str)).apply(_norm_phone)
else:
    df_hs["fuente"] = pd.Series(dtype=str)
    df_hs["estado_label"] = pd.Series(dtype=str)
    df_hs["oportunidad_del_negocio_label"] = pd.Series(dtype=str)


# ─────────────────────────────────────────────────────────────────────────────
# Filtros (sidebar) — usan los labels, no los IDs
# ─────────────────────────────────────────────────────────────────────────────
def _unique(series: pd.Series) -> list[str]:
    return sorted([s for s in series.dropna().astype(str).unique() if s])


variantes_all = _unique(df_hs.get("ab_test_landing", pd.Series(dtype=str)))
estados_all = _unique(df_hs.get("estado_label", pd.Series(dtype=str)))
oport_all = _unique(df_hs.get("oportunidad_del_negocio_label", pd.Series(dtype=str)))

with st.sidebar:
    # Botón de refresh manual — clear cache + rerun
    if st.button("🔄 Actualizar datos", use_container_width=True,
                  help="Limpia el cache de HubSpot/BigQuery/Sheets y vuelve a pegar los APIs"):
        st.cache_data.clear()
        st.rerun()
    st.caption(
        "Cache: HubSpot/BigQuery se refrescan cada 24h; Sheets cada 2 min. "
        "Usa el botón si necesitas datos al instante."
    )
    st.markdown("---")

    st.markdown(f"<div style='color:{LIGHT};font-weight:700;font-size:0.9rem;margin-bottom:14px'>Filtros</div>", unsafe_allow_html=True)

    st.markdown("### Variante")
    sel_variantes = st.multiselect(
        "variantes", variantes_all, default=variantes_all,
        label_visibility="collapsed",
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
    st.caption("Filtros aplican sobre el universo HubSpot y propagan al funnel, distribuciones y pipeline.")


# Aplicar filtros
df_hs_f = df_hs.copy()
if not df_hs_f.empty:
    if len(sel_variantes) < len(variantes_all):
        df_hs_f = df_hs_f[df_hs_f["ab_test_landing"].isin(sel_variantes) | df_hs_f["ab_test_landing"].isna()]
    if len(sel_fuentes) < len(hs_src.FUENTES):
        df_hs_f = df_hs_f[df_hs_f["fuente"].isin(sel_fuentes)]
    if len(sel_oport) < len(oport_all):
        df_hs_f = df_hs_f[df_hs_f["oportunidad_del_negocio_label"].isin(sel_oport) | df_hs_f["oportunidad_del_negocio_label"].isna()]
    if len(sel_estados) < len(estados_all):
        df_hs_f = df_hs_f[df_hs_f["estado_label"].isin(sel_estados) | df_hs_f["estado_label"].isna()]

allowed_uuids: set[str] = set(df_hs_f["deal_uuid"].dropna().astype(str)) if not df_hs_f.empty else set()


# Cruces Leads ↔ Entrevista ↔ HubSpot
df = df_leads.copy()
df["phone_norm"] = df["telefono"].apply(_norm_phone)
df["uuid_str"] = df["uuid"].astype(str)
if not df_int.empty:
    df = df.merge(df_int[["phone_norm", "tiene hipoteca?"]], on="phone_norm", how="left")
else:
    df["tiene hipoteca?"] = None

# Merge con HubSpot SIN filtrar — queremos los metadatos para todos los leads.
# El filtrado se aplica abajo sobre las columnas ya mergeadas.
if not df_hs.empty and "deal_uuid" in df_hs.columns:
    df = df.merge(
        df_hs[["deal_uuid", "fuente", "ab_test_landing",
               "estado_label", "oportunidad_del_negocio_label"]].rename(
            columns={"deal_uuid": "uuid_str", "ab_test_landing": "variante_hs"}
        ),
        on="uuid_str", how="left",
    )
else:
    df["fuente"] = None
    df["variante_hs"] = None
    df["estado_label"] = None
    df["oportunidad_del_negocio_label"] = None

# Pipeline = TODOS los leads del Sheet por defecto. Los filtros narrow,
# pero leads sin match en HS solo se dropean cuando un filtro HS está activo.
def _applied(sel: list, all_opts: list) -> bool:
    return bool(sel) and len(sel) < len(all_opts)

df_in = df.copy()
if _applied(sel_variantes, variantes_all):
    df_in = df_in[
        df_in["grupo"].astype(str).isin(sel_variantes)
        | df_in["variante_hs"].astype(str).isin(sel_variantes)
    ]
if _applied(sel_fuentes, hs_src.FUENTES):
    df_in = df_in[df_in["fuente"].isin(sel_fuentes)]
if _applied(sel_oport, oport_all):
    df_in = df_in[df_in["oportunidad_del_negocio_label"].isin(sel_oport)]
if _applied(sel_estados, estados_all):
    df_in = df_in[df_in["estado_label"].isin(sel_estados)]

# contactado = el teléfono aparece en la pestaña Entrevista. La pestaña
# Entrevista es la fuente de verdad de a quiénes ya se llamó. Los que aplican
# y NO están ahí son la call list activa ("Aplica + LLAMAR", verde claro).
entrevista_phones: set[str] = set()
if not df_int.empty and "phone_norm" in df_int.columns:
    entrevista_phones = set(df_int["phone_norm"].dropna().astype(str))
df_in["contactado"] = df_in["phone_norm"].astype(str).isin(entrevista_phones)
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
c1.markdown(kpi_card("Universo HS", f"{n_universe:,}", "todos con flag_fakedoor"), unsafe_allow_html=True)
c2.markdown(kpi_card("Leads T&C", n_leads, "firmaron formulario"), unsafe_allow_html=True)
c3.markdown(kpi_card("Contactados", n_contactados, f"{n_contactados/max(1,n_leads):.0%}"), unsafe_allow_html=True)
c4.markdown(kpi_card("Interés activo", n_interes, f"{n_interes/max(1,n_leads):.0%}"), unsafe_allow_html=True)
c5.markdown(kpi_card("Elegibles", n_aplica, "score≥720"), unsafe_allow_html=True)
c6.markdown(kpi_card("Por llamar", n_call_list, "call list activa"), unsafe_allow_html=True)


with st.expander("Estado de los scores", expanded=False):
    c1, c2 = st.columns(2)
    c1.metric("Leads con score (Aplica lleno)", score_stats.get("filled", 0))
    c2.metric("Leads sin score (Aplica vacío)", score_stats.get("empty", 0))
    st.caption(
        "El scoring lo dispara el fakedoor frontend al firmar T&C — escribe "
        "directamente las columnas `Aplica` y `Metadata` del Sheet. El "
        "dashboard solo lee, no consulta el API."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Funnel (7 etapas, todo live, sin filtro de fecha)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Embudo del experimento</h2>", unsafe_allow_html=True)

pages_uuids = set(df_bq[df_bq.get("had_pages", 0) == 1]["uuid"].dropna().astype(str)) if not df_bq.empty else set()
if not pages_uuids and not df_bq.empty and "uuid" in df_bq.columns:
    pages_uuids = set(df_bq["uuid"].dropna().astype(str))

# Etapa 1: Universo (HS flag_fakedoor)
n_e1 = n_universe
# Etapa 2: Con nombre del conjunto
if not df_hs_f.empty and "nombre_del_conjunto" in df_hs_f.columns:
    n_e2 = int(df_hs_f["nombre_del_conjunto"].fillna("").astype(str).str.strip().ne("").sum())
else:
    n_e2 = 0
# Etapa 3: Enviados WA = 77% × Con conjunto (Infobip delivery historico)
delivery_ratio = EXPERIMENT.funnel_baseline.get("wa_delivery_ratio", 0.77)
n_e3 = int(round(n_e2 * delivery_ratio))
# Etapa 4: Abrieron pagina (BQ pages ∩ HS allowed)
if allowed_uuids and pages_uuids:
    n_e4 = len(allowed_uuids & pages_uuids)
else:
    n_e4 = len(pages_uuids) if not allowed_uuids else 0
# Etapa 5: T&C firmados (Sheet ∩ HS)
n_e5 = n_leads
# Etapa 6: Elegibles (aplica = "si")
n_e6 = n_aplica
# Etapa 7: Aplican (elegibles sin hipoteca)
n_e7 = n_aplica - int(df_in["con_hipoteca"].sum())

stages = [
    ("Universo (flag fakedoor)",   n_e1, "HubSpot · sin filtro de fecha"),
    ("Con nombre del conjunto",    n_e2, "HubSpot · nombre_del_conjunto ≠ vacío"),
    (f"Enviados WA",                n_e3, f"Estimado · {int(delivery_ratio*100)}% × Con conjunto"),
    ("Abrieron página",             n_e4, "BigQuery · pages ∩ HS"),
    ("T&C firmados",                n_e5, "Sheets/Leads ∩ HS"),
    ("Elegibles",                   n_e6, "Aplica=si"),
    ("Aplican (sin hipoteca)",      n_e7, "Aplica=si + tiene hipoteca≠si"),
]
f_labels = [s[0] for s in stages]
f_vals = [s[1] for s in stages]
f_sources = [s[2] for s in stages]
f_colors = [DEEP, PRIMARY, MED, ACCENT, LIGHT, GREEN_DARK, "#0f5535"][:len(stages)]
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
    hovertemplate="<b>%{y}</b><br>%{x:,} · fuente: %{customdata}<extra></extra>",
))
fig_funnel.update_layout(
    paper_bgcolor=WHITE, plot_bgcolor=WHITE,
    font=dict(family="Inter, sans-serif", color=DEEP, size=11),
    height=380, margin=dict(l=10, r=230, t=10, b=10),
    xaxis=dict(type="log" if use_log else "linear",
               title="Clientes" + (" (log)" if use_log else ""),
               gridcolor="#ede8f5", tickformat=",d"),
    yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig_funnel, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Distribución de leads (side-by-side, todas las categorias)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Distribución de leads</h2>", unsafe_allow_html=True)

if df_hs_f.empty:
    st.info("No hay deals con los filtros actuales.")
else:
    def _hbar(series: pd.Series, title: str, palette: tuple[str, str]):
        c = series.fillna("(sin valor)").astype(str).replace("", "(sin valor)").value_counts().reset_index()
        c.columns = ["cat", "N"]
        c = c.sort_values("N", ascending=True)
        fig = go.Figure(go.Bar(
            x=c["N"], y=c["cat"], orientation="h",
            marker=dict(
                color=c["N"],
                colorscale=[[0, palette[0]], [1, palette[1]]],
                showscale=False,
            ),
            text=c["N"], textposition="outside", textfont_size=10,
        ))
        fig.update_layout(
            paper_bgcolor=WHITE, plot_bgcolor=WHITE,
            font=dict(family="Inter, sans-serif", color=DEEP, size=11),
            title=dict(text=title, font=dict(size=13, color=DEEP, family="Inter")),
            height=max(280, len(c) * 24 + 80),
            margin=dict(l=10, r=50, t=44, b=10),
            yaxis=dict(gridcolor="#ede8f5"),
            xaxis=dict(gridcolor="#ede8f5"),
        )
        return fig

    col_op, col_es = st.columns(2)
    with col_op:
        st.plotly_chart(_hbar(df_hs_f["oportunidad_del_negocio_label"],
                              "Oportunidad del negocio (CO)", (PALE, PRIMARY)),
                        use_container_width=True)
    with col_es:
        st.plotly_chart(_hbar(df_hs_f["estado_label"],
                              "Estado del negocio", (PALE, MED)),
                        use_container_width=True)

    # Tercera fila: Fuente + Variante como pies pequeños
    col_f, col_v = st.columns(2)
    with col_f:
        f_c = df_hs_f["fuente"].value_counts().reindex(hs_src.FUENTES).dropna().reset_index()
        f_c.columns = ["Fuente", "N"]
        if not f_c.empty:
            fig_f = go.Figure(go.Pie(
                labels=f_c["Fuente"], values=f_c["N"],
                hole=0.42, marker_colors=[DEEP, PRIMARY, MED, ACCENT],
                textinfo="label+percent+value", textfont_size=10,
            ))
            fig_f.update_layout(
                paper_bgcolor=WHITE, showlegend=False,
                title=dict(text="Fuente", font=dict(size=13, color=DEEP)),
                height=260, margin=dict(l=5, r=5, t=44, b=5),
            )
            st.plotly_chart(fig_f, use_container_width=True)
    with col_v:
        # Solo AH y BH — el experimento solo tiene esas dos variantes
        v_c = (
            df_hs_f[df_hs_f["ab_test_landing"].isin(["AH", "BH"])]["ab_test_landing"]
            .value_counts().reset_index()
        )
        v_c.columns = ["Variante", "N"]
        if not v_c.empty:
            fig_v = go.Figure(go.Pie(
                labels=v_c["Variante"], values=v_c["N"],
                hole=0.42, marker_colors=[PRIMARY, ACCENT],
                textinfo="label+percent+value", textfont_size=10,
            ))
            fig_v.update_layout(
                paper_bgcolor=WHITE, showlegend=False,
                title=dict(text="Variante A/B (solo AH/BH)", font=dict(size=13, color=DEEP)),
                height=260, margin=dict(l=5, r=5, t=44, b=5),
            )
            st.plotly_chart(fig_v, use_container_width=True)
        else:
            st.info("No hay deals con ab_test_landing ∈ {AH, BH} en el filtro actual.")


# ─────────────────────────────────────────────────────────────────────────────
# Funnel de usabilidad de la landing (4 etapas BQ)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Funnel de usabilidad de la landing</h2>", unsafe_allow_html=True)

if df_bq.empty:
    st.info("BigQuery no devolvió eventos de la landing.")
else:
    df_bq_in = df_bq.copy()
    if allowed_uuids:
        df_bq_in = df_bq_in[df_bq_in["uuid"].astype(str).isin(allowed_uuids)]

    # Etapa 1: Enviados WA — replicamos del funnel principal
    n_u1 = n_e3  # Enviados WA (77% × con conjunto), del funnel principal
    # Etapa 2: Abrió primer link (/<uuid>)
    n_u2 = int(df_bq_in.get("visited_home", pd.Series(dtype=int)).fillna(0).astype(int).sum())
    # Etapa 3: Llegó a /solicitud
    n_u3 = int(df_bq_in.get("visited_solicitud", pd.Series(dtype=int)).fillna(0).astype(int).sum())
    # Etapa 4: T&C firmados (= leads en Sheet, mismo del funnel principal)
    n_u4 = n_leads

    usab_stages = [
        ("Enviados WA",       n_u1, "Estimado (funnel principal)"),
        ("Abrió primer link", n_u2, "BQ · page view en /<uuid>"),
        ("Llegó a /solicitud", n_u3, "BQ · page view en /solicitud"),
        ("Firmó T&C",         n_u4, "Sheets/Leads"),
    ]
    u_labels = [s[0] for s in usab_stages]
    u_vals = [s[1] for s in usab_stages]
    u_sources = [s[2] for s in usab_stages]
    u_colors = [DEEP, PRIMARY, MED, GREEN_DARK]
    u_text = [
        f"{v:,}  ({v/u_vals[i-1]*100:.0f}%)" if i > 0 and u_vals[i-1] > 0 else f"{v:,}"
        for i, v in enumerate(u_vals)
    ]
    use_log_u = (max(u_vals) if u_vals else 0) > 100 and all(v > 0 for v in u_vals)

    fig_u = go.Figure(go.Bar(
        x=u_vals, y=u_labels, orientation="h",
        marker_color=u_colors, text=u_text,
        textposition="outside", textfont=dict(size=11, color=DEEP),
        customdata=u_sources,
        hovertemplate="<b>%{y}</b><br>%{x:,} · %{customdata}<extra></extra>",
    ))
    fig_u.update_layout(
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=11),
        height=260, margin=dict(l=10, r=200, t=10, b=10),
        xaxis=dict(type="log" if use_log_u else "linear",
                   title="Clientes" + (" (log)" if use_log_u else ""),
                   gridcolor="#ede8f5", tickformat=",d"),
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig_u, use_container_width=True)


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

n_total_sheet = len(df_leads)
n_with_hs = int(df["fuente"].notna().sum())
n_shown = len(disp_sorted)
caption_parts = [f"{n_shown} leads mostrados · ordenados por prioridad de llamada"]
if n_with_hs < n_total_sheet:
    caption_parts.append(
        f"⚠️ {n_total_sheet - n_with_hs} de {n_total_sheet} leads del Sheet "
        f"no tienen deal con flag_fakedoor en HubSpot (no salen al filtrar por fuente/estado/oportunidad)"
    )
st.caption(" · ".join(caption_parts))


# ─────────────────────────────────────────────────────────────────────────────
# Tabla raw de HubSpot deals (filtrados)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(f"<h2>HubSpot · deals filtrados ({len(df_hs_f):,})</h2>", unsafe_allow_html=True)
if df_hs_f.empty:
    st.info("Sin deals con los filtros aplicados.")
else:
    show_cols = [
        "dealname", "phone", "createdate", "estado_label",
        "fuente", "flag_fakedoor", "ab_test_landing",
        "oportunidad_del_negocio_label", "nombre_del_conjunto",
        "comite_remodelaciones", "deal_uuid",
    ]
    show_cols = [c for c in show_cols if c in df_hs_f.columns]
    df_hs_display = df_hs_f[show_cols].rename(columns={
        "dealname": "Nombre del negocio",
        "phone": "Teléfono",
        "createdate": "Fecha de creación",
        "estado_label": "Estado del Negocio",
        "fuente": "Fuente",
        "flag_fakedoor": "flag fakedoor",
        "ab_test_landing": "Variante",
        "oportunidad_del_negocio_label": "Oportunidad del Negocio",
        "nombre_del_conjunto": "Nombre del conjunto",
        "comite_remodelaciones": "Comité remodelaciones",
        "deal_uuid": "deal_uuid",
    })
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
# Insights de entrevistas
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Insights · entrevistas cualitativas</h2>", unsafe_allow_html=True)

if df_int.empty:
    st.info("La pestaña Entrevista está vacía.")
else:
    # Solo entrevistas de leads que APLICAN (aplica=si)
    aplica_si_phones = set(df_in[df_in["aplica"] == "si"]["phone_norm"].dropna().astype(str))
    df_int_f = df_int[df_int["phone_norm"].isin(aplica_si_phones)].copy()

    if df_int_f.empty:
        st.info("Ninguna entrevista corresponde a leads que aplican (aplica=si).")
    else:
        st.caption(f"Insights solo sobre los {len(df_int_f)} leads que aplican (de {len(df_int)} entrevistas totales)")
        col_pie, col_quote = st.columns([1, 2])
        with col_pie:
            hip_vals = df_int_f["tiene hipoteca?"].fillna("(sin dato)").astype(str).str.strip().str.lower()
            hip_vals = hip_vals.replace({"si": "Sí", "sí": "Sí", "no": "No", "": "(sin dato)"})
            hip_counts = hip_vals.value_counts()
            fig_hip = go.Figure(go.Pie(
                labels=hip_counts.index, values=hip_counts.values,
                hole=0.42, marker_colors=[GREEN_DARK, ACCENT, "#bbb"],
                textinfo="label+percent+value", textfont_size=10,
            ))
            fig_hip.update_layout(
                paper_bgcolor=WHITE, showlegend=False,
                title=dict(text="¿Tiene hipoteca?", font=dict(size=13, color=DEEP)),
                height=280, margin=dict(l=5, r=5, t=44, b=5),
            )
            st.plotly_chart(fig_hip, use_container_width=True)
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


st.divider()
st.caption(
    f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    "TTL cache: 120s (Sheets/HS) · 300s (BQ) · 3600s (HS property labels). "
    "Scores persisten en Aplica + Metadata del Sheet."
)
