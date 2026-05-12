"""Conector Google Sheets - lectura + escritura.

Para escribir, el service account debe tener permiso Editor sobre la hoja
(en el share dialog de Google Sheets). El scope spreadsheets (no .readonly)
permite el write a nivel API.
"""
from __future__ import annotations

import json
import os

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _credentials() -> Credentials:
    inline = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if inline:
        return Credentials.from_service_account_info(json.loads(inline), scopes=_SCOPES)
    path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_FILE")
    if path and os.path.exists(path):
        return Credentials.from_service_account_file(path, scopes=_SCOPES)
    raise RuntimeError("Define GOOGLE_SHEETS_CREDENTIALS o GOOGLE_SHEETS_CREDENTIALS_FILE.")


def _open(sheet_id: str | None = None):
    sid = sheet_id or os.environ["GOOGLE_SHEETS_ID"]
    return gspread.authorize(_credentials()).open_by_key(sid)


def fetch_tab(tab: str, sheet_id: str | None = None) -> pd.DataFrame:
    sh = _open(sheet_id)
    try:
        ws = sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        return pd.DataFrame()
    return pd.DataFrame(ws.get_all_records())


def list_tabs(sheet_id: str | None = None) -> list[str]:
    return [ws.title for ws in _open(sheet_id).worksheets()]


def fetch_leads() -> pd.DataFrame:
    return fetch_tab(os.environ.get("GOOGLE_SHEETS_TAB", "Leads"))


def ensure_columns(tab: str, columns: list[str], sheet_id: str | None = None) -> dict[str, int]:
    """Asegura que las columnas existan en la primera fila. Devuelve mapping nombre→indice (1-based)."""
    sh = _open(sheet_id)
    ws = sh.worksheet(tab)
    headers = ws.row_values(1)
    existing = {h: i + 1 for i, h in enumerate(headers)}
    next_col = len(headers) + 1
    new_headers = list(headers)
    to_append: list[tuple[int, str]] = []
    for col in columns:
        if col in existing:
            continue
        existing[col] = next_col
        new_headers.append(col)
        to_append.append((next_col, col))
        next_col += 1
    if to_append:
        end_col = to_append[-1][0]
        ws.update(range_name=f"A1:{_a1_col(end_col)}1", values=[new_headers])
    return {c: existing[c] for c in columns}


def _a1_col(idx_1based: int) -> str:
    """1 -> A, 27 -> AA, etc."""
    s = ""
    n = idx_1based
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def update_rows_by_key(
    tab: str,
    key_column: str,
    updates: list[dict],
    sheet_id: str | None = None,
) -> int:
    """Para cada dict en updates, busca la fila donde key_column == updates[key_column]
    y escribe los otros campos en las columnas que correspondan.

    Si la fila no existe, no hace nada (no insertamos filas — el form lo hace).
    Devuelve cuántas filas actualizó.
    """
    if not updates:
        return 0
    sh = _open(sheet_id)
    ws = sh.worksheet(tab)
    headers = ws.row_values(1)
    if key_column not in headers:
        raise ValueError(f"Columna clave '{key_column}' no esta en {tab}")
    key_col_idx = headers.index(key_column)  # 0-based
    all_values = ws.get_all_values()
    # mapa key_value -> row index (1-based)
    key_map: dict[str, int] = {}
    for i, row in enumerate(all_values[1:], start=2):
        if key_col_idx < len(row):
            key_map[str(row[key_col_idx]).strip()] = i

    batch: list[dict] = []
    n = 0
    for upd in updates:
        key_val = str(upd.get(key_column, "")).strip()
        row_idx = key_map.get(key_val)
        if row_idx is None:
            continue
        for field, value in upd.items():
            if field == key_column or field not in headers:
                continue
            col_idx = headers.index(field) + 1
            a1 = f"{_a1_col(col_idx)}{row_idx}"
            batch.append({"range": a1, "values": [[str(value) if value is not None else ""]]})
        n += 1
    if batch:
        ws.batch_update(batch, value_input_option="RAW")
    return n
