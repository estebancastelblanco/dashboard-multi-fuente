"""Selector home — biblioteca de experimentos."""
from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def _bootstrap_from_st_secrets() -> None:
    keys = [
        "BQ_PROJECT_ID", "BQ_DATASET_PROJECT", "BQ_DATASET", "BQ_TABLE",
        "GOOGLE_APPLICATION_CREDENTIALS_JSON",
        "HUBSPOT_ACCESS_TOKEN",
        "GOOGLE_SHEETS_ID", "GOOGLE_SHEETS_TAB", "GOOGLE_SHEETS_CREDENTIALS",
        "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
    ]
    try:
        for k in keys:
            if k in st.secrets and not os.environ.get(k):
                os.environ[k] = str(st.secrets[k])
    except Exception:
        return


_bootstrap_from_st_secrets()

from src.experiments import REGISTRY
from src.styling import inject_base_css, DEEP, MED, PRIMARY

st.set_page_config(
    page_title="Habi · Biblioteca de experimentos",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_base_css()

st.markdown(
    f"<h1 style='color:{DEEP};font-size:1.7rem;font-weight:700;margin-bottom:0'>"
    f"Biblioteca de experimentos</h1>"
    f"<div style='color:{MED};font-size:0.85rem;margin-bottom:24px'>"
    f"Habi Capital · dashboards en tiempo real · {len(REGISTRY)} experimentos</div>",
    unsafe_allow_html=True,
)

ROOT = Path(__file__).parent


def _render_exp_card(exp: "Experiment") -> None:
    end = exp.end_date or "en curso"
    st.markdown(
        f"<div class='exp-card'>"
        f"<div class='exp-title'>{exp.title}</div>"
        f"<div class='exp-dates'>{exp.start_date} → {end}</div>"
        f"<div style='font-size:0.85rem;color:#333;margin-bottom:12px'>"
        f"{exp.description}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    link_cols = st.columns(2)
    with link_cols[0]:
        if exp.design_doc_url:
            st.link_button("Diseño", exp.design_doc_url, use_container_width=True)
        else:
            st.button("Diseño", disabled=True, use_container_width=True, key=f"d_{exp.slug}")
    with link_cols[1]:
        if exp.results_doc_url:
            st.link_button("Resultados", exp.results_doc_url, use_container_width=True)
        else:
            st.button("Resultados", disabled=True, use_container_width=True, key=f"r_{exp.slug}")

    st.page_link(exp.page, label="Abrir dashboard →", use_container_width=True)

    if exp.attachments or exp.external_links:
        with st.expander("Documentos adjuntos", expanded=False):
            for label, url in exp.external_links:
                st.markdown(f"- [{label}]({url})")
            for rel in exp.attachments:
                path = ROOT / rel
                if not path.exists():
                    st.caption(f"(falta) {rel}")
                    continue
                st.caption(rel)
                body = path.read_text()
                if path.suffix == ".sql":
                    st.code(body, language="sql")
                elif path.suffix in (".md", ".txt"):
                    st.markdown(f"```\n{body}\n```")
                else:
                    st.code(body)
    st.markdown("<div style='margin-bottom:12px'></div>", unsafe_allow_html=True)


# Filas de 2 cards — evita que la 3ª/4ª queden ocultas abajo de cards altas.
for row_start in range(0, len(REGISTRY), 2):
    row = REGISTRY[row_start : row_start + 2]
    cols = st.columns(len(row))
    for col, exp in zip(cols, row):
        with col:
            _render_exp_card(exp)
