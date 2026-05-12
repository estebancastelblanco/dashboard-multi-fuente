"""Conector Google Sheets - credenciales desde variables de entorno."""
from __future__ import annotations

import json
import os

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _credentials() -> Credentials:
    inline = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if inline:
        info = json.loads(inline)
        return Credentials.from_service_account_info(info, scopes=_SCOPES)
    path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_FILE")
    if path and os.path.exists(path):
        return Credentials.from_service_account_file(path, scopes=_SCOPES)
    raise RuntimeError(
        "Define GOOGLE_SHEETS_CREDENTIALS (JSON inline) "
        "o GOOGLE_SHEETS_CREDENTIALS_FILE (ruta)."
    )


def _open_spreadsheet(sheet_id: str | None = None):
    sid = sheet_id or os.environ["GOOGLE_SHEETS_ID"]
    return gspread.authorize(_credentials()).open_by_key(sid)


def fetch_tab(tab: str, sheet_id: str | None = None) -> pd.DataFrame:
    """Lee cualquier pestaña como DataFrame. Si no existe devuelve DataFrame vacío."""
    sh = _open_spreadsheet(sheet_id)
    try:
        ws = sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        return pd.DataFrame()
    return pd.DataFrame(ws.get_all_records())


def list_tabs(sheet_id: str | None = None) -> list[str]:
    return [ws.title for ws in _open_spreadsheet(sheet_id).worksheets()]


def fetch_leads() -> pd.DataFrame:
    return fetch_tab(os.environ.get("GOOGLE_SHEETS_TAB", "Leads"))
