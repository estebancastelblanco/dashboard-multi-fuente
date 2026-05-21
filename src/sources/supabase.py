"""Conector Supabase (PostgREST) para EXP-003 Pre-Oferta Temprana.

Lee las tablas pobladas por la landing `ofertadesdeasignado`:
  - whatsapp_sends     : log inmutable de cada envio WA (1..4)
  - deal_interactions  : un row por click en CTA (con send_number)
  - deal_assignments   : un row por asignacion automatica al market maker

Usa el service_role key para saltarse RLS. No requiere instalar el SDK
de Supabase: la REST API de PostgREST acepta requests crudos.
"""
from __future__ import annotations

import os
import time
from typing import Iterable

import pandas as pd
import requests


def _config() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        raise RuntimeError(
            "Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY en env / st.secrets."
        )
    return url, key


def _headers() -> dict:
    _, key = _config()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }


def _request_with_retry(
    url: str, *, max_retries: int = 4, timeout: int = 30, **kwargs,
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=timeout, **kwargs)
            if resp.status_code in (429, 502, 503, 504):
                time.sleep(min(2.0 ** attempt, 15.0))
                last_exc = requests.HTTPError(
                    f"{resp.status_code} (retry {attempt + 1}/{max_retries})"
                )
                continue
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            time.sleep(min(2.0 ** attempt, 15.0))
    if last_exc:
        raise last_exc
    raise RuntimeError(f"Supabase GET {url} fallo tras {max_retries} intentos")


def fetch_table(
    table: str,
    *,
    select: str = "*",
    since_iso: str | None = None,
    until_iso: str | None = None,
    timestamp_col: str = "created_at",
    order_desc: bool = True,
    limit: int = 50000,
) -> pd.DataFrame:
    """Lee una tabla de Supabase via PostgREST.

    `since_iso` y `until_iso` (YYYY-MM-DD) filtran por `timestamp_col`.
    Pagina con Range header solo si el response viene truncado.
    """
    url, _ = _config()
    full = f"{url}/rest/v1/{table}"
    params: dict[str, str] = {
        "select": select,
        "order": f"{timestamp_col}.{'desc' if order_desc else 'asc'}",
        "limit": str(limit),
    }
    if since_iso:
        params[timestamp_col] = f"gte.{since_iso}T00:00:00"
    if until_iso:
        # Incluir el dia final completo
        params[timestamp_col] = f"lte.{until_iso}T23:59:59"
    if since_iso and until_iso:
        # PostgREST soporta multiples filtros con `and=`
        params.pop(timestamp_col, None)
        params["and"] = (
            f"({timestamp_col}.gte.{since_iso}T00:00:00,"
            f"{timestamp_col}.lte.{until_iso}T23:59:59)"
        )

    resp = _request_with_retry(full, headers=_headers(), params=params)
    rows = resp.json()
    if not isinstance(rows, list):
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if timestamp_col in df.columns:
        df[timestamp_col] = pd.to_datetime(df[timestamp_col], errors="coerce", utc=True)
    return df


def fetch_whatsapp_sends(
    since_iso: str | None = None, until_iso: str | None = None
) -> pd.DataFrame:
    """Log de envios WA. timestamp_col = sent_at."""
    return fetch_table(
        "whatsapp_sends",
        since_iso=since_iso,
        until_iso=until_iso,
        timestamp_col="sent_at",
    )


def fetch_deal_interactions(
    since_iso: str | None = None, until_iso: str | None = None
) -> pd.DataFrame:
    """Cada click del cliente en un CTA. timestamp_col = created_at."""
    return fetch_table(
        "deal_interactions",
        since_iso=since_iso,
        until_iso=until_iso,
        timestamp_col="created_at",
    )


def fetch_deal_assignments(
    since_iso: str | None = None, until_iso: str | None = None
) -> pd.DataFrame:
    """Una fila por deal asignado al market maker. timestamp_col = assigned_at."""
    return fetch_table(
        "deal_assignments",
        since_iso=since_iso,
        until_iso=until_iso,
        timestamp_col="assigned_at",
    )


def aggregate_sends_per_deal(df_sends: pd.DataFrame) -> pd.DataFrame:
    """Resumen por deal: cuantos envios reales se hicieron y cuando fue el ultimo.

    Util para cruzar contra HubSpot y detectar deals donde HubSpot ya marca
    preofertaflag1=N pero solo hay M<N filas en whatsapp_sends (gap).
    """
    if df_sends.empty:
        return pd.DataFrame(columns=["deal_id", "n_sends", "last_send_at", "max_send_number"])
    g = (
        df_sends.groupby("deal_id")
        .agg(
            n_sends=("id", "count"),
            last_send_at=("sent_at", "max"),
            max_send_number=("send_number", "max"),
        )
        .reset_index()
    )
    return g


def last_interaction_per_deal(df_interactions: pd.DataFrame) -> pd.DataFrame:
    """Para cada deal, la ultima interaccion (cualquier CTA) con su send_number."""
    if df_interactions.empty:
        return pd.DataFrame(columns=["deal_id", "property", "send_number", "created_at"])
    df = df_interactions.sort_values("created_at", ascending=False)
    return df.drop_duplicates(subset=["deal_id"], keep="first")[
        ["deal_id", "property", "send_number", "created_at"]
    ].reset_index(drop=True)


def filter_deals(df: pd.DataFrame, deal_ids: Iterable[str]) -> pd.DataFrame:
    """Restringe el df a un subconjunto de deal_ids (strings)."""
    if df.empty:
        return df
    s = set(str(d) for d in deal_ids)
    return df[df["deal_id"].astype(str).isin(s)].copy()
