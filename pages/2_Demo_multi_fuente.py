"""Demo · multi-fuente — tres tablas en vivo, max 10x4 por fuente."""
from __future__ import annotations

import os

import pandas as pd
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

from src.sources import bigquery as bq_src
from src.sources import gsheets as gs_src
from src.sources import hubspot as hs_src
from src.styling import inject_base_css

st.set_page_config(page_title="Demo multi-fuente", layout="wide")
inject_base_css()

st.title("Demo · multi-fuente")
st.caption("Datos en vivo · BigQuery + HubSpot + Google Sheets · max 10×4 por tabla")


def _trim(df: pd.DataFrame, max_rows: int = 10, max_cols: int = 4) -> pd.DataFrame:
    return df.iloc[:max_rows, :max_cols]


def _section(title: str, loader, *, refresh_key: str):
    st.subheader(title)
    cols = st.columns([1, 6])
    if cols[0].button("Refrescar", key=refresh_key):
        st.cache_data.clear()
    try:
        df = _trim(loader())
        if df.empty:
            st.info("Sin datos.")
        else:
            st.dataframe(df, hide_index=True, use_container_width=True)
            st.caption(f"{len(df)} filas · {len(df.columns)} columnas")
    except Exception as exc:
        st.error(f"Error al cargar: {type(exc).__name__}: {exc}")


@st.cache_data(ttl=300, show_spinner="Cargando BigQuery…")
def load_bq() -> pd.DataFrame:
    return bq_src.fetch_top_inmuebles(limit=10)


@st.cache_data(ttl=300, show_spinner="Cargando HubSpot…")
def load_hs() -> pd.DataFrame:
    return hs_src.fetch_recent_deals(limit=10)


@st.cache_data(ttl=300, show_spinner="Cargando Google Sheets…")
def load_gs() -> pd.DataFrame:
    return gs_src.fetch_leads()


_section("BigQuery · top 10 inmuebles", load_bq, refresh_key="r_bq")
st.divider()
_section("HubSpot · últimos 10 deals", load_hs, refresh_key="r_hs")
st.divider()
_section(
    f"Google Sheets · hoja {os.environ.get('GOOGLE_SHEETS_TAB', 'Leads')}",
    load_gs,
    refresh_key="r_gs",
)
