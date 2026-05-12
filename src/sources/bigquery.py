"""Conector BigQuery - credenciales desde variables de entorno."""
from __future__ import annotations

import json
import os

import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials


def _client() -> bigquery.Client:
    project = os.environ["BQ_PROJECT_ID"]
    creds_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

    info: dict | None = None
    if creds_json:
        info = json.loads(creds_json)
    elif creds_path and os.path.exists(creds_path):
        with open(creds_path) as fh:
            info = json.load(fh)

    if info is None:
        raise RuntimeError(
            "Define GOOGLE_APPLICATION_CREDENTIALS_JSON (JSON inline) "
            "o GOOGLE_APPLICATION_CREDENTIALS (ruta a archivo)."
        )

    creds_type = info.get("type")
    if creds_type == "service_account":
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/bigquery"]
        )
    elif creds_type == "authorized_user":
        creds = UserCredentials(
            token=None,
            refresh_token=info["refresh_token"],
            client_id=info["client_id"],
            client_secret=info["client_secret"],
            token_uri="https://oauth2.googleapis.com/token",
        )
    else:
        raise RuntimeError(f"Tipo de credencial no soportado: {creds_type!r}")

    return bigquery.Client(project=project, credentials=creds)


def fetch_top_inmuebles(limit: int = 10) -> pd.DataFrame:
    dataset_project = os.environ.get("BQ_DATASET_PROJECT", "papyrus-data-mx")
    dataset = os.environ.get("BQ_DATASET", "habi_wh_hesh")
    table = os.environ.get("BQ_TABLE", "production_hesh")
    fqtn = f"`{dataset_project}.{dataset}.{table}`"
    sql = f"""
        SELECT
          nid,
          tipo_transaccion,
          precio_original,
          ask_price_final
        FROM {fqtn}
        WHERE ask_price_final IS NOT NULL
        ORDER BY nid DESC
        LIMIT {int(limit)}
    """
    return _client().query(sql).to_dataframe()


def fetch_fakedoor_landing_events() -> pd.DataFrame:
    """UUIDs que visitaron la landing del fake door y si llegaron a consentimiento.

    Cruza `sellers-main-prod.javascript9.pages` y `tracks` filtrando por la URL
    `https://habicapitalliquidez.vercel.app/%`. Devuelve un DataFrame con:
      - uuid
      - total_events
      - reached_consent (bool, 1 si visito /consentimiento/<uuid>)
    """
    sql = r"""
    WITH urls AS (
      SELECT context_page_url,
             REGEXP_EXTRACT(context_page_url, r'([0-9a-fA-F\-]{36})') AS uuid
      FROM `sellers-main-prod.javascript9.pages`
      WHERE context_page_url IS NOT NULL
        AND context_page_url LIKE 'https://habicapitalliquidez.vercel.app/%'
      UNION ALL
      SELECT context_page_url,
             REGEXP_EXTRACT(context_page_url, r'([0-9a-fA-F\-]{36})') AS uuid
      FROM `sellers-main-prod.javascript9.tracks`
      WHERE context_page_url IS NOT NULL
        AND context_page_url LIKE 'https://habicapitalliquidez.vercel.app/%'
    )
    SELECT
      uuid,
      COUNT(*) AS total_events,
      COUNTIF(context_page_url LIKE '%/consentimiento/%') AS consent_events,
      MAX(IF(context_page_url LIKE '%/consentimiento/%', 1, 0)) AS reached_consent
    FROM urls
    WHERE uuid IS NOT NULL
    GROUP BY uuid
    """
    return _client().query(sql).to_dataframe()
