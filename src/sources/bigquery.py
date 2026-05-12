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
    """UUIDs que visitaron la landing, desglosado por URL pattern.

    Tres etapas posibles dentro del flujo:
      - /<uuid>                          -> primer landing (home_uuid)
      - /solicitud?deal_uuid=<uuid>      -> pagina de solicitud
      - /consentimiento/<uuid>           -> pantalla T&C

    Devuelve un DataFrame con un row por uuid, con flags:
      - visited_home        page view en /<uuid>
      - visited_solicitud   page view en /solicitud
      - visited_consent     page view en /consentimiento/<uuid>
      - had_pages           cualquier page view (legacy)
      - had_tracks          cualquier track event (legacy)
      - total_events        total de eventos
    """
    sql = r"""
    WITH urls AS (
      SELECT context_page_url,
             REGEXP_EXTRACT(context_page_url, r'([0-9a-fA-F\-]{36})') AS uuid,
             'pages' AS source
      FROM `sellers-main-prod.javascript9.pages`
      WHERE context_page_url IS NOT NULL
        AND context_page_url LIKE 'https://habicapitalliquidez.vercel.app/%'
      UNION ALL
      SELECT context_page_url,
             REGEXP_EXTRACT(context_page_url, r'([0-9a-fA-F\-]{36})') AS uuid,
             'tracks' AS source
      FROM `sellers-main-prod.javascript9.tracks`
      WHERE context_page_url IS NOT NULL
        AND context_page_url LIKE 'https://habicapitalliquidez.vercel.app/%'
    )
    SELECT
      uuid,
      COUNT(*) AS total_events,
      MAX(IF(source = 'pages', 1, 0)) AS had_pages,
      MAX(IF(source = 'tracks', 1, 0)) AS had_tracks,
      MAX(IF(REGEXP_CONTAINS(context_page_url,
              r'vercel\.app/[0-9a-fA-F\-]{36}(\?|$)') AND source = 'pages', 1, 0)) AS visited_home,
      MAX(IF(context_page_url LIKE '%/solicitud%' AND source = 'pages', 1, 0)) AS visited_solicitud,
      MAX(IF(context_page_url LIKE '%/consentimiento/%' AND source = 'pages', 1, 0)) AS visited_consent,
      MAX(IF(context_page_url LIKE '%/consentimiento/%', 1, 0)) AS reached_consent
    FROM urls
    WHERE uuid IS NOT NULL
    GROUP BY uuid
    """
    return _client().query(sql).to_dataframe()
