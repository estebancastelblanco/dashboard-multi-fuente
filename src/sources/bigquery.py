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


def fetch_nid_for_uuids(deal_uuids: list[str]) -> pd.DataFrame:
    """Cruza deal_uuid (HubSpot API) → nid (BQ) usando sellers-main-prod.hubspot.deals."""
    if not deal_uuids:
        return pd.DataFrame(columns=["deal_uuid", "nid"])
    uuids_str = ",".join(f'"{u}"' for u in deal_uuids[:5000])
    sql = f"""
        SELECT deal_uuid, nid
        FROM `sellers-main-prod.hubspot.deals`
        WHERE deal_uuid IN ({uuids_str})
    """
    return _client().query(sql).to_dataframe()


def fetch_funnel_mex(
    nids: list[int],
    date_from: str | None = None,
    date_to: str | None = None,
) -> pd.DataFrame:
    """Trae todas las filas del funnel MX para los nids dados.

    `seguimiento_funnel_mex` es una tabla long: una fila por (nid, valor, fecha).
    Devolvemos todas las filas con dedupe por (nid, valor) tomando la fecha más
    temprana — así contar `valor='Cita Agendada (hubspot)'` da el set de leads
    que llegaron al menos una vez a esa etapa.
    """
    if not nids:
        return pd.DataFrame(columns=[
            "nid", "valor", "fecha", "hubspot_owner_id", "categoria_comercial",
            "prioridad_gestion_market_maker",
        ])
    # BQ array literal — limitar a 5000 nids por query para estar seguros
    nid_list = ",".join(str(int(n)) for n in nids[:5000])
    date_clauses = []
    if date_from:
        date_clauses.append(f"AND DATE(fecha) >= '{date_from}'")
    if date_to:
        date_clauses.append(f"AND DATE(fecha) <= '{date_to}'")
    date_filter = "\n        ".join(date_clauses)
    sql = f"""
        SELECT
          nid,
          valor,
          MIN(fecha) AS fecha,
          ANY_VALUE(hubspot_owner_id) AS hubspot_owner_id,
          ANY_VALUE(categoria_comercial) AS categoria_comercial,
          ANY_VALUE(prioridad_gestion_market_maker) AS prioridad_gestion_market_maker
        FROM `sellers-main-prod.bi_mx.seguimiento_funnel_mex`
        WHERE nid IN ({nid_list})
        {date_filter}
        GROUP BY nid, valor
    """
    return _client().query(sql).to_dataframe()


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


def fetch_fakedoor_client_categories(nids: list[int]) -> pd.DataFrame:
    """Trae la categoría comercial declarada para los nids del FakeDoor."""
    if not nids:
        return pd.DataFrame(columns=["nid", "motivo_venta_string"])
    nid_list = ",".join(str(int(n)) for n in nids[:5000])
    sql = f"""
        SELECT
          nid,
          motivo_venta_string
        FROM `sellers-main-prod.mid_funnel_ibuyer.seller_digital_co_recepcionista_mm`
        WHERE motivo_venta_string IS NOT NULL
          AND nid IN ({nid_list})
    """
    return _client().query(sql).to_dataframe()


def fetch_sellers_credit_breakdown(nids: list[int] | None = None) -> pd.DataFrame:
    """Trae nid, línea de negocio y cédula para sellers."""
    base_sql = """
        SELECT DISTINCT
          d.nid,
          bl.name AS linea_negocio,
          p.document_id AS cedula_cliente
        FROM `sellers-main-prod.commercial_legal_co.deal` d
        LEFT JOIN `sellers-main-prod.commercial_legal_co.deal_person` dp
          ON dp.deal_id = d.id
        LEFT JOIN `sellers-main-prod.commercial_legal_co.person` p
          ON p.id = dp.person_id
        LEFT JOIN `sellers-main-prod.commercial_legal_co.business_line` bl
          ON bl.id = d.business_line_id
        WHERE d.nid IS NOT NULL
          AND p.document_id IS NOT NULL
          AND bl.name IS NOT NULL
    """
    if not nids:
        return _client().query(base_sql).to_dataframe()

    frames: list[pd.DataFrame] = []
    for i in range(0, len(nids), 5000):
        chunk = nids[i:i + 5000]
        nid_list = ",".join(str(int(n)) for n in chunk)
        sql = base_sql + f"\n  AND d.nid IN ({nid_list})"
        frames.append(_client().query(sql).to_dataframe())
    if not frames:
        return pd.DataFrame(columns=["nid", "linea_negocio", "cedula_cliente"])
    return pd.concat(frames, ignore_index=True).drop_duplicates()


# ─────────────────────────────────────────────────────────────────────────────
# ABC Test Landing CO · query simplificada para el dashboard
# Omite las tablas im-main-prod (sin permisos) y usa la versión CO de cierres.
# ─────────────────────────────────────────────────────────────────────────────
def fetch_abc_test_landing_co() -> pd.DataFrame:
    """Tabla maestra del experimento ABC test landing CO.

    Columnas finales:
      nid, abc_test_landing_co, ab_test_landing, estado_aprobado,
      fecha_aprobado, fecha_aprobado_semana, fecha_cierre, v_fecha_promesa,
      fecha_cierre_efectiva, fecha_ofertado, fue_ofertado, categoria_ancla
    """
    sql = """
    WITH
    base_ofertas AS (
      SELECT
        CAST(nid AS INT64) AS nid,
        DATE(fecha_aprobado) AS fecha_aprobado,
        DATE(fecha_cierre)   AS fecha_cierre,
        estado_aprobado
      FROM `papyrus-data.habi_wh.detalle_ofertas_col`
      WHERE fecha_aprobado IS NOT NULL
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY nid, DATE(fecha_aprobado) ORDER BY fecha_aprobado DESC
      ) = 1
    ),
    base_hubspot AS (
      SELECT
        CAST(nid AS INT64) AS nid,
        abc_test_landing_co,
        ab_test_landing,
        SAFE_CAST(valor_negociado AS FLOAT64) AS valor_negociado,
        SAFE_CAST(ask_price AS FLOAT64)       AS customer_price,
        SAFE_CAST(precio_ancla AS FLOAT64)    AS precio_ancla_hs,
        SAFE_CAST(oferta_final_prestamo_mx_calculada AS FLOAT64) AS oferta_final_calculada
      FROM `sellers-main-prod.hubspot.deals`
      WHERE abc_test_landing_co IS NOT NULL
    ),
    pasaron_ofertados AS (
      SELECT
        CAST(nid AS INT64) AS nid,
        MIN(fecha) AS fecha_ofertado
      FROM `sellers-main-prod.hubspot.historical`
      WHERE propiedad = 'dealstage' AND valor = '1066441580'
      GROUP BY 1
    ),
    base_cierres_co AS (
      SELECT
        CAST(nid AS INT64) AS nid,
        MIN(DATE(fecha_de_firma_promesa_compra_venta_docusign)) AS v_fecha_promesa
      FROM `papyrus-master.operations_sellers_co_dwh.sellers_promesa_compraventa_co_dwh`
      WHERE fecha_de_firma_promesa_compra_venta_docusign IS NOT NULL
      GROUP BY 1
    )
    SELECT
      o.nid,
      hs.abc_test_landing_co,
      hs.ab_test_landing,
      o.estado_aprobado,
      o.fecha_aprobado,
      DATE_TRUNC(o.fecha_aprobado, WEEK(MONDAY)) AS fecha_aprobado_semana,
      o.fecha_cierre,
      c.v_fecha_promesa,
      IFNULL(o.fecha_cierre, c.v_fecha_promesa) AS fecha_cierre_efectiva,
      DATE(po.fecha_ofertado) AS fecha_ofertado,
      IF(po.nid IS NOT NULL, 'Ofertado', 'No ofertado') AS fue_ofertado,
      CASE
        WHEN SAFE_DIVIDE(hs.oferta_final_calculada - hs.customer_price, hs.customer_price) IS NULL THEN NULL
        WHEN SAFE_DIVIDE(hs.oferta_final_calculada - hs.customer_price, hs.customer_price) > -0.16 THEN 'Baja diferencia (20%)'
        WHEN SAFE_DIVIDE(hs.oferta_final_calculada - hs.customer_price, hs.customer_price) >= -0.30 THEN 'Media diferencia (13%)'
        WHEN SAFE_DIVIDE(hs.oferta_final_calculada - hs.customer_price, hs.customer_price) < -0.30 THEN 'Alta diferencia (5%)'
        ELSE NULL
      END AS categoria_ancla
    FROM base_ofertas o
    INNER JOIN base_hubspot hs ON hs.nid = o.nid
    LEFT JOIN pasaron_ofertados po ON po.nid = o.nid
    LEFT JOIN base_cierres_co c ON c.nid = o.nid
    """
    df = _client().query(sql).to_dataframe()
    for col in ("fecha_aprobado", "fecha_aprobado_semana", "fecha_cierre",
                "v_fecha_promesa", "fecha_cierre_efectiva", "fecha_ofertado"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    return df
