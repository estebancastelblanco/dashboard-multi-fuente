"""BNPL Comerciales · CO — negocios con ¿aplica BNPL?=Sí que NO han cerrado.

Muestra, por comercial (hubspot_owner_id), cuántos negocios BNPL=Sí tiene que
aún no avanzaron al proceso de cierre con Habi (se excluyen las etapas de
oferta aceptada / legalizado / firmado / etc.). Debajo, una tabla con nid,
comercial, Link Habi Capital y si el cliente aplica (cruce con el Sheet de
Habi Capital: score ≥ 720 en la columna Metadata).
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def _bootstrap_from_st_secrets() -> None:
    keys = [
        "HUBSPOT_ACCESS_TOKEN",
        "BQ_PROJECT_ID", "GOOGLE_APPLICATION_CREDENTIALS_JSON",
        "GOOGLE_SHEETS_ID", "GOOGLE_SHEETS_CREDENTIALS",
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
from src.sources import risk_score as score_src

hs_src = importlib.reload(hs_src)
bq_src = importlib.reload(bq_src)
gs_src = importlib.reload(gs_src)
score_src = importlib.reload(score_src)
from src.styling import (
    inject_base_css, kpi_card,
    DEEP, PRIMARY, MED, ACCENT, LIGHT, PALE, WHITE,
)

st.set_page_config(page_title="BNPL Comerciales · CO", layout="wide")
inject_base_css()

EXPERIMENT = next(e for e in REGISTRY if e.slug == "bnpl-comerciales-co")

st.markdown(
    f"<h1 style='color:{DEEP};font-size:1.5rem;font-weight:700;margin-bottom:0'>"
    f"{EXPERIMENT.title}</h1>"
    f"<div style='color:{MED};font-size:0.8rem;margin-bottom:20px'>"
    f"Negocios CO · ¿aplica BNPL?=Sí · sin cerrar · por comercial (hubspot_owner_id)</div>",
    unsafe_allow_html=True,
)

DAY = 86400
SHORT = 120
HABICAPITAL_SHEET_ID = os.environ.get(
    "GOOGLE_SHEETS_ID", "1vN7wL8a_NvfLks2IvoIICgbV2GrIDKhPbymS3aLft2I"
)
UUID_RX = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I
)


# ─────────────────────────────────────────────────────────────────────────────
# Loaders
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=DAY, show_spinner="HubSpot · deals BNPL=Sí CO…", persist="disk")
def load_bnpl_deals() -> pd.DataFrame:
    return hs_src.fetch_bnpl_co_deals()


@st.cache_data(ttl=DAY, show_spinner="HubSpot · etapas de pipeline…", persist="disk")
def load_stages() -> list[dict]:
    return hs_src.fetch_deal_stages()


@st.cache_data(ttl=DAY, show_spinner="BigQuery · nid + equipo_sellers…", persist="disk")
def load_enrichment(deal_uuids: tuple[str, ...]) -> pd.DataFrame:
    return bq_src.fetch_bnpl_co_enrichment(list(deal_uuids))


@st.cache_data(ttl=SHORT, show_spinner="Sheets · aplica (Habi Capital)…")
def load_aplica_map() -> dict[str, str]:
    """uuid (deal_uuid) → aplica (si/no/pending/error) desde el Sheet de leads.

    Reglas iguales a Habi Capital: el frontend escribe Aplica + Metadata (JSON
    con score) al firmar T&C; aplica='si' si score ≥ 720.
    """
    try:
        df = gs_src.fetch_tab("Leads", sheet_id=HABICAPITAL_SHEET_ID)
    except Exception as exc:
        st.warning(f"Sheets Habi Capital: {type(exc).__name__}: {exc}")
        return {}
    if df.empty or "uuid" not in df.columns:
        return {}
    df, _ = score_src.enrich_leads_with_scores(df)
    df["uuid_lc"] = df["uuid"].astype(str).str.strip().str.lower()
    df = df[df["uuid_lc"].str.contains(UUID_RX, na=False)]
    return dict(zip(df["uuid_lc"], df["aplica"]))


def _extract_uuid(row) -> str | None:
    for col in ("link_habi_capital", "deal_uuid"):
        m = UUID_RX.search(str(row.get(col, "") or ""))
        if m:
            return m.group(1).lower()
    return None


APLICA_LABELS = {
    "si": "Sí", "no": "No", "pending": "Pendiente",
    "error": "Error", "(no enviado)": "(no enviado)",
}


# ─────────────────────────────────────────────────────────────────────────────
# Carga + procesamiento
# ─────────────────────────────────────────────────────────────────────────────
try:
    df = load_bnpl_deals()
except Exception as exc:
    st.error(f"Error cargando HubSpot: {type(exc).__name__}: {exc}")
    st.stop()

if df.empty:
    st.warning("No hay negocios CO con ¿aplica BNPL?=Sí.")
    st.stop()

stages = load_stages()
stage_label = {s["id"]: s["label"] for s in stages}
excluded_ids = {
    s["id"] for s in stages
    if s["is_won"]
    or any(e in (s["label"] or "").strip().lower()
           for e in hs_src.BNPL_EXCLUDED_STAGE_LABELS)
}

df["uuid"] = df.apply(_extract_uuid, axis=1)
df["dealstage"] = df["dealstage"].astype(str)
df["etapa"] = df["dealstage"].map(stage_label).fillna(df["dealstage"])

# Excluir negocios que ya avanzaron / cerraron con Habi.
n_total = len(df)
df = df[~df["dealstage"].isin(excluded_ids)].copy()
n_excluidos = n_total - len(df)

# Enriquecer con nid + equipo_sellers (BigQuery).
uuids = tuple(sorted({u for u in df["uuid"].dropna().astype(str) if u}))
try:
    df_enr = load_enrichment(uuids)
except Exception as exc:
    df_enr = pd.DataFrame(columns=["deal_uuid", "nid", "equipo_sellers"])
    st.warning(f"BigQuery enriquecimiento: {type(exc).__name__}: {exc}")
if not df_enr.empty:
    df_enr["deal_uuid"] = df_enr["deal_uuid"].astype(str).str.lower()
    df = df.merge(df_enr, left_on="uuid", right_on="deal_uuid", how="left")
for col in ("nid", "equipo_sellers"):
    if col not in df.columns:
        df[col] = pd.NA
df["equipo_sellers"] = df["equipo_sellers"].fillna("(sin equipo)").astype(str).replace("", "(sin equipo)")
df["hubspot_owner_id"] = df["hubspot_owner_id"].fillna("(sin owner)").astype(str)

# Aplica? (cruce con el Sheet de Habi Capital por uuid).
aplica_map = load_aplica_map()
df["aplica_raw"] = df["uuid"].map(aplica_map).fillna("(no enviado)")
df["aplica"] = df["aplica_raw"].map(lambda v: APLICA_LABELS.get(v, v))


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar · filtros
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    if st.button("Actualizar datos", use_container_width=True,
                 help="Refresca HubSpot, BigQuery y el Sheet de Habi Capital."):
        for loader in (load_bnpl_deals, load_stages, load_enrichment, load_aplica_map):
            try:
                loader.clear()
            except Exception:
                pass
        st.rerun()
    st.markdown("---")
    st.markdown(
        f"<div style='color:{LIGHT};font-weight:700;font-size:0.9rem;margin-bottom:14px'>"
        f"Filtros</div>", unsafe_allow_html=True,
    )

    st.markdown("### Comercial (hubspot_owner_id)")
    owners_all = sorted(df["hubspot_owner_id"].dropna().astype(str).unique().tolist())
    sel_owners = st.multiselect(
        "owners", owners_all, default=owners_all, label_visibility="collapsed",
        help="hubspot_owner_id del negocio.",
    )

    st.markdown("### NID")
    sel_nid = st.text_input(
        "nid", value="", placeholder="ej. 59030823233",
        label_visibility="collapsed", help="Filtra a un nid específico. Vacío = sin filtro.",
    ).strip()

    st.markdown("### Equipo sellers")
    equipos_all = sorted(df["equipo_sellers"].dropna().astype(str).unique().tolist())
    sel_equipos = st.multiselect(
        "equipos", equipos_all, default=equipos_all, label_visibility="collapsed",
        help="equipo_sellers de detalle_ofertas_col.",
    )

    st.markdown("### Aplica?")
    APLICA_OPTS = ["Sí", "No", "Pendiente", "Error", "(no enviado)"]
    sel_aplica = st.multiselect(
        "aplica", APLICA_OPTS, default=APLICA_OPTS, label_visibility="collapsed",
        help="Cruce con el Sheet de Habi Capital (score ≥ 720).",
    )


# Aplicar filtros
dff = df.copy()
if sel_owners and len(sel_owners) < len(owners_all):
    dff = dff[dff["hubspot_owner_id"].isin(sel_owners)]
if sel_nid:
    dff = dff[dff["nid"].astype(str) == sel_nid]
if sel_equipos and len(sel_equipos) < len(equipos_all):
    dff = dff[dff["equipo_sellers"].isin(sel_equipos)]
if sel_aplica and len(sel_aplica) < len(APLICA_OPTS):
    dff = dff[dff["aplica"].isin(sel_aplica)]


# ─────────────────────────────────────────────────────────────────────────────
# KPIs
# ─────────────────────────────────────────────────────────────────────────────
n_negocios = len(dff)
n_comerciales = int(dff["hubspot_owner_id"].nunique())
n_aplica_si = int((dff["aplica"] == "Sí").sum())
n_enviados = int((dff["aplica_raw"] != "(no enviado)").sum())

c1, c2, c3, c4 = st.columns(4)
c1.markdown(kpi_card("Negocios BNPL=Sí", f"{n_negocios:,}", "sin cerrar (filtrado)"), unsafe_allow_html=True)
c2.markdown(kpi_card("Comerciales", f"{n_comerciales:,}", "con ≥1 negocio"), unsafe_allow_html=True)
c3.markdown(kpi_card("Link enviado", f"{n_enviados:,}", "aparece en el Sheet"), unsafe_allow_html=True)
c4.markdown(kpi_card("Aplica (≥720)", f"{n_aplica_si:,}", "score ≥ 720"), unsafe_allow_html=True)

st.caption(
    f"Universo: {n_total:,} negocios CO con ¿aplica BNPL?=Sí · "
    f"{n_excluidos:,} excluidos por etapa avanzada/cerrada (oferta aceptada, "
    f"legalizado, firmado, etc.) · {len(df):,} quedan activos."
)


# ─────────────────────────────────────────────────────────────────────────────
# Distribución por comercial
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Negocios por comercial (hubspot_owner_id)</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

if dff.empty:
    st.info("Sin negocios para el filtro actual.")
else:
    by_owner = (
        dff.groupby("hubspot_owner_id")
        .agg(negocios=("uuid", "size"),
             aplica_si=("aplica", lambda s: int((s == "Sí").sum())))
        .reset_index()
        .sort_values("negocios", ascending=False)
    )
    top = by_owner.head(30).iloc[::-1]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=top["negocios"], y=top["hubspot_owner_id"].astype(str),
        orientation="h", marker_color=PRIMARY,
        text=top["negocios"], textposition="outside",
        name="Negocios", hovertemplate="owner %{y}<br>%{x} negocios<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=11),
        height=max(380, len(top) * 24 + 80),
        margin=dict(l=10, r=60, t=10, b=10),
        xaxis=dict(title="Negocios BNPL=Sí sin cerrar", gridcolor="#ede8f5"),
        yaxis=dict(title="hubspot_owner_id", type="category"),
    )
    st.plotly_chart(fig, use_container_width=True)
    if len(by_owner) > 30:
        st.caption(f"Mostrando top 30 de {len(by_owner)} comerciales.")


# ─────────────────────────────────────────────────────────────────────────────
# Tabla
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(f"<h2>Detalle ({len(dff):,})</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

show_cols = [
    ("nid", "nid"),
    ("hubspot_owner_id", "Comercial (owner_id)"),
    ("equipo_sellers", "Equipo sellers"),
    ("etapa", "Etapa"),
    ("aplica", "Aplica?"),
    ("link_habi_capital", "Link Habi Capital"),
]
table = dff[[c for c, _ in show_cols]].rename(columns=dict(show_cols))
table = table.sort_values(["Comercial (owner_id)", "Aplica?"]).reset_index(drop=True)


def _color_aplica(v):
    return {
        "Sí": "background-color:#16a34a;color:#fff;font-weight:600",
        "No": "background-color:#dc2626;color:#fff;font-weight:600",
        "Pendiente": "background-color:#f59e0b;color:#fff;font-weight:600",
        "Error": "background-color:#7f1d1d;color:#fff;font-weight:600",
    }.get(v, "color:#9aa0a6")


styled = table.style.map(_color_aplica, subset=["Aplica?"])
st.dataframe(
    styled, hide_index=True, use_container_width=True,
    column_config={
        "Link Habi Capital": st.column_config.LinkColumn("Link Habi Capital", display_text="abrir"),
    },
)

st.divider()
st.caption(
    f"Última actualización: {datetime.now():%Y-%m-%d %H:%M} · "
    f"TTL cache: HubSpot/BQ 24h, Sheet 2 min · "
    f"Excluye etapas: oferta aceptada, legalizado, declaración juramentada, "
    f"documentación cargada, regresados/aprobados cumplimiento y legal, "
    f"contrato enviado/regresado, firmado, regestión, y cierres ganados."
)
