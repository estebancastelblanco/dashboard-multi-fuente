"""EXP-003 Pre-Oferta Temprana (MX) — dashboard live (HubSpot + Sheets logs)."""
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
        "GOOGLE_SHEETS_CREDENTIALS",
    ]
    try:
        for k in keys:
            if k in st.secrets and not os.environ.get(k):
                os.environ[k] = str(st.secrets[k])
    except Exception:
        return


_bootstrap_from_st_secrets()

from src.experiments import REGISTRY
from src.sources import gsheets as gs_src
from src.sources import hubspot as hs_src
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
    f"Pre-Oferta · EXP-003 (MX)</h1>"
    f"<div style='color:{MED};font-size:0.8rem;margin-bottom:20px'>"
    f"Pre-oferta temprana vía WhatsApp · lanzado {EXPERIMENT.start_date} · split 95/5</div>",
    unsafe_allow_html=True,
)


DAY = 86400
SHORT = 120
START_DATE = EXPERIMENT.start_date
WA_DELIVERY = EXPERIMENT.funnel_baseline.get("wa_delivery_ratio", 0.80)
LANDING_SHEET_ID = EXPERIMENT.funnel_baseline.get("landing_sheet_id")


@st.cache_data(ttl=DAY, show_spinner="HubSpot · deals pre-oferta…", persist="disk")
def load_preoferta_deals() -> pd.DataFrame:
    return hs_src.fetch_preoferta_deals(since_iso=START_DATE)


UUID_RX = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
EXP_DOMAIN = "ofertadesdeasignado"


def _extract_uuid_from_row(row) -> str | None:
    """Saca el UUID del log: prioriza Deal_uuid; si no, lo busca en las URLs."""
    duuid = str(row.get("Deal_uuid", "") or "").strip()
    if UUID_RX.fullmatch(duuid):
        return duuid.lower()
    for col in ("base_url", "full_url", "url"):
        m = UUID_RX.search(str(row.get(col, "") or ""))
        if m:
            return m.group(1).lower()
    return None


def _is_exp_row(row) -> bool:
    """¿Este log es del dominio del experimento (ofertadesdeasignado)?"""
    for col in ("base_url", "full_url", "url"):
        if EXP_DOMAIN in str(row.get(col, "") or "").lower():
            return True
    return False


@st.cache_data(ttl=SHORT, show_spinner="Sheets · logs de aperturas…")
def load_landing_logs() -> pd.DataFrame:
    """Logs del sheet, filtrados al dominio del experimento. UUID normalizado."""
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


with st.sidebar:
    if st.button("Actualizar datos", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.markdown("---")
    st.markdown(
        f"<div style='color:{LIGHT};font-weight:700;font-size:0.9rem;margin-bottom:8px'>"
        f"Pre-Oferta Temprana</div>"
        f"<div style='font-size:0.75rem;color:{PALE};line-height:1.4'>"
        f"Filtros aplicados desde el origen:<br>"
        f"• createdate ≥ {START_DATE}<br>"
        f"• contacto_digital = Seller</div>",
        unsafe_allow_html=True,
    )


try:
    df_hs = load_preoferta_deals()
except Exception as exc:
    st.error(f"HubSpot falló: {type(exc).__name__}: {exc}")
    st.stop()

if df_hs.empty:
    st.warning(
        f"No hay deals con createdate ≥ {START_DATE} y contacto_digital=Seller."
    )
    st.stop()

try:
    df_logs = load_landing_logs()
except Exception as exc:
    df_logs = pd.DataFrame()
    st.warning(f"Sheets logs no disponibles: {type(exc).__name__}: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Cómputo del funnel
# ─────────────────────────────────────────────────────────────────────────────
n_universo = len(df_hs)
n_wa = int(round(n_universo * WA_DELIVERY))

opened_uuids: set[str] = set()
if not df_logs.empty and "uuid" in df_logs.columns:
    opened_uuids = set(df_logs["uuid"].dropna().astype(str).str.lower().unique())

deals_uuids = (
    set(df_hs["deal_uuid"].dropna().astype(str).str.lower())
    if "deal_uuid" in df_hs.columns else set()
)
n_opened = len(opened_uuids & deals_uuids) if deals_uuids else len(opened_uuids)

# Branches post-apertura
n_oferta = int((df_hs.get("quiero_recibir_oferta_formal", pd.Series(dtype=int)) > 0).sum())
n_preg = int((df_hs.get("tengo_preguntas", pd.Series(dtype=int)) > 0).sum())
n_err = int((df_hs.get("error_preoferta", pd.Series(dtype=int)) > 0).sum())


# ─────────────────────────────────────────────────────────────────────────────
# KPIs
# ─────────────────────────────────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
c1.markdown(kpi_card("Universo", f"{n_universo:,}", f"Seller MX · ≥ {START_DATE}"), unsafe_allow_html=True)
c2.markdown(kpi_card("Enviados WA", f"{n_wa:,}", f"{int(WA_DELIVERY*100)}% del universo"), unsafe_allow_html=True)
c3.markdown(kpi_card("Abrieron link", f"{n_opened:,}", f"{n_opened/max(1,n_wa):.0%} de enviados"), unsafe_allow_html=True)
c4.markdown(kpi_card("Quiero oferta", n_oferta, "interés primario"), unsafe_allow_html=True)
c5.markdown(kpi_card("Tengo preguntas", n_preg, "interés con dudas"), unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Funnel principal
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Embudo del experimento</h2>", unsafe_allow_html=True)

stages = [
    ("Universo (Seller MX)",   n_universo,  f"HubSpot · createdate ≥ {START_DATE} + contacto_digital=Seller"),
    ("Enviados WA",            n_wa,        f"Estimado · {int(WA_DELIVERY*100)}% × Universo"),
    ("Abrieron link",          n_opened,    "Sheets LOGS · dedup por Deal_uuid"),
]
f_labels = [s[0] for s in stages]
f_vals = [s[1] for s in stages]
f_sources = [s[2] for s in stages]
f_colors = [DEEP, PRIMARY, MED]
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
    height=240, margin=dict(l=10, r=230, t=10, b=10),
    xaxis=dict(type="log" if use_log else "linear",
               title="Clientes" + (" (log)" if use_log else ""),
               gridcolor="#ede8f5", tickformat=",d"),
    yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig_funnel, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Split post-apertura: 3 ramas (oferta / preguntas / error)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Interacción post-apertura</h2>", unsafe_allow_html=True)

st.caption(
    "Cada deal abierto se divide según los contadores de HubSpot: "
    "quiero_recibir_oferta_formal, tengo_preguntas, error_preoferta. "
    "Un mismo deal puede aparecer en varias ramas si el usuario clicó varios CTAs."
)

branches = [
    ("Quiero oferta formal", n_oferta, GREEN_DARK,    "quiero_recibir_oferta_formal > 0"),
    ("Tengo preguntas",      n_preg,   ACCENT,        "tengo_preguntas > 0"),
    ("Error pre-oferta",     n_err,    RED,           "error_preoferta > 0"),
]
b_labels = [b[0] for b in branches]
b_vals = [b[1] for b in branches]
b_colors = [b[2] for b in branches]
b_sources = [b[3] for b in branches]
b_text = [
    f"{v:,}  ({v/n_opened*100:.0f}% de abiertos)" if n_opened > 0 else f"{v:,}"
    for v in b_vals
]
fig_split = go.Figure(go.Bar(
    x=b_vals, y=b_labels, orientation="h",
    marker_color=b_colors, text=b_text,
    textposition="outside", textfont=dict(size=11, color=DEEP),
    customdata=b_sources,
    hovertemplate="<b>%{y}</b><br>%{x:,} · %{customdata}<extra></extra>",
))
fig_split.update_layout(
    paper_bgcolor=WHITE, plot_bgcolor=WHITE,
    font=dict(family="Inter, sans-serif", color=DEEP, size=11),
    height=240, margin=dict(l=10, r=230, t=10, b=10),
    xaxis=dict(title="Deals", gridcolor="#ede8f5", tickformat=",d"),
    yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig_split, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Desglose por deal
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(f"<h2>Desglose ({n_universo:,})</h2>", unsafe_allow_html=True)

cons = df_hs.copy()
cons["deal_uuid_str"] = cons.get("deal_uuid", pd.Series(dtype=str)).astype(str).str.lower()
cons["abrió_link"] = cons["deal_uuid_str"].isin(opened_uuids)

# Conteo de aperturas por deal desde LOGS
if not df_logs.empty and "uuid" in df_logs.columns:
    aperturas = df_logs.groupby("uuid").size().reset_index(name="n_aperturas")
    aperturas.columns = ["deal_uuid_str", "n_aperturas"]
    cons = cons.merge(aperturas, on="deal_uuid_str", how="left")
cons["n_aperturas"] = cons.get("n_aperturas", pd.Series(dtype=int)).fillna(0).astype(int)

show_cols = [
    ("dealname",                       "Nombre del negocio"),
    ("phone",                          "Teléfono"),
    ("createdate",                     "Fecha creación"),
    ("dealstage",                      "Etapa"),
    ("contacto_digital",               "Contacto Digital"),
    ("precio_maximo_prestamo",         "Precio máximo"),
    ("abrió_link",                     "Abrió link"),
    ("n_aperturas",                    "# aperturas"),
    ("quiero_recibir_oferta_formal",   "Quiero oferta"),
    ("tengo_preguntas",                "Tengo preguntas"),
    ("error_preoferta",                "Error pre-oferta"),
    ("deal_uuid",                      "deal_uuid"),
]
present = [(src, lab) for src, lab in show_cols if src in cons.columns]
df_display = cons[[s for s, _ in present]].rename(columns=dict(present))
st.dataframe(df_display, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Decisión · esperando datos
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Decisión · CVR asignado → cierre</h2>", unsafe_allow_html=True)

st.markdown(
    f"""
<div style="background:{DEEP};color:white;padding:18px 20px;border-radius:8px">
  <div style="font-size:0.75rem;opacity:0.85;letter-spacing:0.08em;text-transform:uppercase">Métrica primaria</div>
  <div style="font-size:1.5rem;font-weight:700;line-height:1.1;margin:4px 0">Esperando cierres</div>
  <div style="font-size:0.85rem;opacity:0.95">
    El experimento se lanzó el {START_DATE}. La CVR asignado → cierre se calcula
    cuando los deals lleguen a la etapa de cierre del pipeline. Por ahora, las
    señales tempranas son apertura del link y clics en CTA.
  </div>
</div>
""",
    unsafe_allow_html=True,
)

with st.expander("Criterios de éxito (del documento de diseño)"):
    st.markdown(
        """
| Resultado | Criterio | Acción |
|---|---|---|
| **Éxito** | CVR asignado→cierre tratamiento ≥ control · margin variance neutro/positivo · sin caída en cierres absolutos | Rollout |
| **Inconcluso** | CVR ≈ control · margin variance ≈ control | Revisar mecanismos de transmisión |
| **Fracaso** | CVR < control · o margin variance significativamente negativo | Revertir e iterar copy |

**Guardrails:** margin variance, tasa de rechazo por desviación de precio, volumen absoluto de cierres.
"""
    )


st.divider()
st.caption(
    f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    "TTL cache: 120s (Sheets) · 24h (HS). "
    "Logs de aperturas: dedupe por Deal_uuid."
)
