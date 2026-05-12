"""Paleta y CSS compartidos entre páginas."""
from __future__ import annotations

import streamlit as st

DEEP    = "#2E1147"
PRIMARY = "#4A148C"
MED     = "#7B2CBF"
ACCENT  = "#9D4EDD"
LIGHT   = "#C77DFF"
PALE    = "#E0AAFF"
BG      = "#F4F1F9"
WHITE   = "#FFFFFF"

GREEN_DARK  = "#1B5E20"   # aplica + contactado
GREEN_LIGHT = "#A5D6A7"   # aplica + no contactado (CALL LIST)
YELLOW      = "#FFE082"   # pendiente de consultar score
GREY        = "#CFD8DC"   # no aplica
RED         = "#EF5350"   # error


def inject_base_css() -> None:
    st.markdown(f"""
    <style>
      html, body, [data-testid="stAppViewContainer"] {{ background:{BG}; }}
      [data-testid="stSidebar"] {{ background:{DEEP}; border-right: 1px solid {MED}; }}
      [data-testid="stSidebar"] *, [data-testid="stSidebar"] label {{ color:{PALE} !important; }}
      [data-testid="stSidebar"] .stMultiSelect [data-baseweb="tag"] {{
        background:{MED} !important;
        color:#fff !important;
      }}
      [data-testid="stSidebar"] .stMultiSelect [data-baseweb="tag"] span {{
        color:#fff !important;
      }}
      [data-testid="stSidebar"] .stMultiSelect [data-baseweb="select"] {{
        background:#fff;
      }}
      [data-testid="stSidebar"] .stMarkdown h3 {{
        color:{LIGHT} !important;
        font-size:0.78rem !important;
        text-transform:uppercase;
        letter-spacing:.08em;
        margin-top:18px !important;
        margin-bottom:4px !important;
      }}
      h1, h2, h3 {{ color:{DEEP}; }}
      h2 {{
        font-size:1rem; font-weight:700; margin:28px 0 10px 0;
        border-left:4px solid {PRIMARY}; padding-left:10px;
      }}
      .kcard {{
        background:{WHITE}; border-radius:10px; padding:14px 18px;
        border-left:4px solid {PRIMARY};
        box-shadow:0 1px 6px rgba(46,17,71,0.09);
      }}
      .kval  {{ font-size:1.75rem; font-weight:700; color:{PRIMARY}; line-height:1.1; }}
      .klbl  {{ font-size:0.7rem; color:#666; text-transform:uppercase; letter-spacing:.06em; margin-top:2px; }}
      .ksub  {{ font-size:0.75rem; color:{MED}; margin-top:1px; }}
      .exp-card {{
        background:{WHITE}; border-radius:12px; padding:20px 22px;
        border-top:6px solid {PRIMARY};
        box-shadow:0 2px 10px rgba(46,17,71,0.10);
        margin-bottom:18px;
      }}
      .exp-title {{ font-size:1.2rem; font-weight:700; color:{DEEP}; margin-bottom:4px; }}
      .exp-dates {{ font-size:0.78rem; color:{MED}; margin-bottom:12px; }}
      .legend-box {{
        display:inline-block; width:14px; height:14px; border-radius:3px;
        vertical-align:middle; margin-right:6px;
      }}
    </style>
    """, unsafe_allow_html=True)


def kpi_card(label: str, value: str | int, sub: str = "") -> str:
    return (
        f"<div class='kcard'>"
        f"<div class='kval'>{value}</div>"
        f"<div class='klbl'>{label}</div>"
        f"<div class='ksub'>{sub}</div>"
        f"</div>"
    )
