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


def fetch_leads() -> pd.DataFrame:
    sheet_id = os.environ["GOOGLE_SHEETS_ID"]
    tab = os.environ.get("GOOGLE_SHEETS_TAB", "Leads")
    gc = gspread.authorize(_credentials())
    ws = gc.open_by_key(sheet_id).worksheet(tab)
    return pd.DataFrame(ws.get_all_records())
