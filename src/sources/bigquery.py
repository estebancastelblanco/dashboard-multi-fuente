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


def fetch_ab_funnel_mex(since_iso: str, until_iso: str | None = None) -> pd.DataFrame:
    """Funnel A/B del experimento Pre-Oferta MX, calculado en BQ.

    Devuelve un row por (nid, grupo, valor) donde:
      - grupo = 'B' si el deal HubSpot tiene contacto_digital='seller' creado
                dentro de la ventana del experimento (universo del tratamiento)
      - grupo = 'A' = TODO el funnel MX que pasó por alguna etapa en el rango
                y NO es del tratamiento. Incluye deals viejos cuyo cierre
                cayó en mayo aunque hayan sido creados meses antes.

    Esto hace que el funnel A muestre los 55 cierres OCD reales del producto
    (matchea con el funnel mensual MX), no solo la cohorte del experimento.

    Tanto el universo del control como el funnel (fecha de etapa) respetan
    el rango [since_iso, until_iso].
    """
    if not until_iso:
        until_iso = "9999-12-31"
    sql = f"""
    WITH seller_nids AS (
      SELECT DISTINCT CAST(nid AS INT64) AS nid
      FROM `sellers-main-prod.hubspot.deals`
      WHERE LOWER(contacto_digital) = 'seller'
        AND DATE(createdate) BETWEEN '{since_iso}' AND '{until_iso}'
        AND nid IS NOT NULL
    ),
    funnel AS (
      SELECT
        nid,
        valor,
        MIN(fecha) AS fecha
      FROM `sellers-main-prod.bi_mx.seguimiento_funnel_mex`
      WHERE DATE(fecha) BETWEEN '{since_iso}' AND '{until_iso}'
      GROUP BY nid, valor
    ),
    universe_a AS (
      -- Todos los nids del funnel MX que NO son del experimento (tratamiento)
      SELECT DISTINCT f.nid, 'A' AS grupo
      FROM funnel f
      LEFT JOIN seller_nids s USING(nid)
      WHERE s.nid IS NULL
    ),
    universe_b AS (
      -- Universo del experimento: deals seller creados en la ventana,
      -- aunque todavía no hayan pasado por ninguna etapa
      SELECT DISTINCT nid, 'B' AS grupo FROM seller_nids
    ),
    universos AS (
      SELECT * FROM universe_a
      UNION ALL
      SELECT * FROM universe_b
    )
    SELECT
      u.nid,
      u.grupo,
      f.valor,
      f.fecha
    FROM universos u
    LEFT JOIN funnel f USING(nid)
    """
    df = _client().query(sql).to_dataframe()
    if not df.empty and "fecha" in df.columns:
        df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    return df


def fetch_funnel_monthly_mex(
    since_iso: str, until_iso: str | None = None,
) -> pd.DataFrame:
    """Funnel MX completo agregado por mes con flag de A/B.

    A diferencia de fetch_ab_funnel_mex (que restringe el universo a deals
    creados en la ventana), esta query cuenta TODOS los nids que pasaron por
    cada etapa de `seguimiento_funnel_mex` dentro del rango. Eso replica el
    dashboard oficial del producto (donde un Cierre OCD de mayo cuenta aunque
    el deal se creó en marzo).

    El grupo se asigna así:
      - 'B' (tratamiento) si el nid pertenece a un deal con
            contacto_digital='seller' creado dentro del rango del experimento
      - 'A' (control) en cualquier otro caso

    Devuelve `mes, valor, grupo, leads`.
    """
    if not until_iso:
        until_iso = "9999-12-31"
    sql = f"""
    WITH seller_nids AS (
      SELECT DISTINCT CAST(nid AS INT64) AS nid
      FROM `sellers-main-prod.hubspot.deals`
      WHERE LOWER(contacto_digital) = 'seller'
        AND DATE(createdate) BETWEEN '{since_iso}' AND '{until_iso}'
        AND nid IS NOT NULL
    )
    SELECT
      DATE_TRUNC(DATE(f.fecha), MONTH) AS mes,
      f.valor,
      CASE WHEN s.nid IS NOT NULL THEN 'B' ELSE 'A' END AS grupo,
      COUNT(DISTINCT f.nid) AS leads
    FROM `sellers-main-prod.bi_mx.seguimiento_funnel_mex` f
    LEFT JOIN seller_nids s USING(nid)
    WHERE DATE(f.fecha) BETWEEN '{since_iso}' AND '{until_iso}'
    GROUP BY mes, f.valor, grupo
    ORDER BY mes, leads DESC
    """
    df = _client().query(sql).to_dataframe()
    if not df.empty and "mes" in df.columns:
        df["mes"] = pd.to_datetime(df["mes"], errors="coerce")
    return df


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

    Replica la lógica de la query maestra de Looker (data/query.sql) para que
    los conteos por variante cuadren 1:1 con el reporte de Looker Studio.
    Diferencias clave vs. versiones previas de esta función:
      - Universo restringido igual que Looker: excluye deals con
        ``asignacion_descartes_top`` asignado (hubspot_staging.deal) y deals
        inmobiliaria-first (``apply_real_estate_first = true``).
      - ``fecha_ofertado`` solo para nids presentes en tabla_inmuebles_general
        (filtro ``ig.nid IS NOT NULL`` de Looker).
      - ``fecha_cierre_efectiva`` = ``fecha_cierre`` de detalle_ofertas_mx;
        ``v_fecha_promesa`` (DWH) solo se adjunta cuando ya existe fecha_cierre,
        igual que Looker. NO se cuentan precierres sin cierre como cierre.
      - HubSpot se une por LEFT JOIN sin filtrar country ni variante: los deals
        sin variante caen a "(sin variante)" aguas arriba en la página.

    Columnas finales:
      nid, abc_test_landing_co, ab_test_landing, deal_uuid, pipeline,
      hubspot_owner_id, owner_name, equipo_sellers, estado_aprobado,
      fecha_aprobado, fecha_aprobado_semana, fecha_cierre, v_fecha_promesa,
      fecha_cierre_efectiva, fecha_ofertado, fue_ofertado, categoria_ancla
    """
    sql = """
    WITH
    inmo_first AS (
      SELECT DISTINCT ig.nid
      FROM `papyrus-data-mx.habi_wh_sellers.deal_additional` AS da
      LEFT JOIN `papyrus-data-mx.habi_wh_bi.tabla_inmuebles_general` AS ig
        ON ig.id_negocio = da.deal_id
      WHERE JSON_EXTRACT_SCALAR(da.meta, '$.apply_real_estate_first') = "true"
        AND ig.nid IS NOT NULL
    ),
    base_ofertas AS (
      SELECT
        CAST(dox.nid AS INT64) AS nid,
        DATE(dox.fecha_aprobado) AS fecha_aprobado,
        DATE(dox.fecha_cierre)   AS fecha_cierre,
        dox.estado_aprobado,
        dox.equipo_sellers
      FROM `papyrus-data.habi_wh.detalle_ofertas_mx` AS dox
      LEFT JOIN `sellers-main-prod.hubspot_staging.deal` AS hd
        ON CAST(hd.nid AS INT64) = CAST(dox.nid AS INT64)
      LEFT JOIN inmo_first inf ON inf.nid = CAST(dox.nid AS INT64)
      WHERE hd.asignacion_descartes_top IS NULL
        AND inf.nid IS NULL
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY dox.nid, DATE(dox.fecha_aprobado) ORDER BY dox.fecha_aprobado DESC
      ) = 1
    ),
    base_hubspot AS (
      SELECT
        CAST(d.nid AS INT64) AS nid,
        d.abc_test_landing_co,
        d.ab_test_landing,
        LOWER(d.deal_uuid) AS deal_uuid,
        d.pipeline,
        d.hubspot_owner_id,
        TRIM(CONCAT(IFNULL(o.first_name,''), ' ', IFNULL(o.last_name,''))) AS owner_name,
        -- Segmentación del experimento (filtros de análisis e insights):
        --   razon_de_venta_usuario_gabi_mx → motivo declarado de venta (Liquidez,
        --     Cambio de Casa, Otros). ~44% de cobertura.
        --   ciudad_mx → municipio MX (100% cobertura; `ciudad`/`area_metropolitana`
        --     vienen vacías para MX, son columnas CO).
        --   final_prestamo_mx → monto final del préstamo MXN (94% cobertura).
        NULLIF(TRIM(d.razon_de_venta_usuario_gabi_mx), '') AS razon_venta,
        NULLIF(TRIM(d.ciudad_mx), '') AS ciudad_mx,
        SAFE_CAST(d.final_prestamo_mx AS FLOAT64) AS final_prestamo_mx,
        SAFE_CAST(d.valor_negociado AS FLOAT64) AS valor_negociado,
        SAFE_CAST(d.ask_price AS FLOAT64)       AS customer_price,
        SAFE_CAST(d.precio_ancla AS FLOAT64)    AS precio_ancla_hs,
        SAFE_CAST(d.oferta_final_prestamo_mx_calculada AS FLOAT64) AS oferta_final_calculada
      FROM `sellers-main-prod.hubspot.deals` d
      LEFT JOIN `sellers-main-prod.hubspot.owners` o
        ON LOWER(o.email) = LOWER(d.hubspot_owner_id)
        OR CAST(o.id AS STRING) = CAST(d.hubspot_owner_id AS STRING)
    ),
    pasaron_ofertados AS (
      SELECT
        CAST(nid AS INT64) AS nid,
        MIN(fecha) AS fecha_ofertado
      FROM `sellers-main-prod.hubspot.historical`
      WHERE propiedad = 'dealstage' AND valor = '1066441580'
      GROUP BY 1
    ),
    fecha_ofertas AS (
      SELECT
        CAST(h.nid AS INT64) AS nid,
        MIN(h.fecha) AS fecha_ofertado
      FROM `sellers-main-prod.hubspot.historical` h
      WHERE h.propiedad = 'dealstage' AND h.valor = '1066441580'
        AND EXISTS (
          SELECT 1 FROM `papyrus-data-mx.habi_wh_bi.tabla_inmuebles_general` ig
          WHERE ig.nid = CAST(h.nid AS INT64)
        )
      GROUP BY 1
    ),
    base_cierres_mx AS (
      SELECT
        CAST(nid AS INT64) AS nid,
        MIN(DATE(v_fecha_cierre)) AS v_fecha_promesa
      FROM `papyrus-master.operations_sellers_mx_dwh.int_sellers_cierres_y_precierres_mx_dwh`
      WHERE v_fecha_cierre IS NOT NULL
      GROUP BY 1
    )
    SELECT
      o.nid,
      hs.abc_test_landing_co,
      hs.ab_test_landing,
      hs.deal_uuid,
      hs.pipeline,
      hs.hubspot_owner_id,
      hs.owner_name,
      hs.razon_venta,
      hs.ciudad_mx,
      hs.final_prestamo_mx,
      o.equipo_sellers,
      o.estado_aprobado,
      o.fecha_aprobado,
      DATE_TRUNC(o.fecha_aprobado, WEEK(MONDAY)) AS fecha_aprobado_semana,
      o.fecha_cierre,
      IF(o.fecha_cierre IS NOT NULL, c.v_fecha_promesa, NULL) AS v_fecha_promesa,
      o.fecha_cierre AS fecha_cierre_efectiva,
      DATE(fo.fecha_ofertado) AS fecha_ofertado,
      IF(po.nid IS NOT NULL, 'Ofertado', 'No ofertado') AS fue_ofertado,
      CASE
        WHEN SAFE_DIVIDE(hs.oferta_final_calculada - hs.customer_price, hs.customer_price) IS NULL THEN NULL
        WHEN SAFE_DIVIDE(hs.oferta_final_calculada - hs.customer_price, hs.customer_price) > -0.16 THEN 'Baja diferencia (20%)'
        WHEN SAFE_DIVIDE(hs.oferta_final_calculada - hs.customer_price, hs.customer_price) >= -0.30 THEN 'Media diferencia (13%)'
        WHEN SAFE_DIVIDE(hs.oferta_final_calculada - hs.customer_price, hs.customer_price) < -0.30 THEN 'Alta diferencia (5%)'
        ELSE NULL
      END AS categoria_ancla
    FROM base_ofertas o
    LEFT JOIN base_hubspot hs ON hs.nid = o.nid
    LEFT JOIN pasaron_ofertados po ON po.nid = o.nid
    LEFT JOIN fecha_ofertas fo ON fo.nid = o.nid
    LEFT JOIN base_cierres_mx c ON c.nid = o.nid
    """
    df = _client().query(sql).to_dataframe()
    for col in ("fecha_aprobado", "fecha_aprobado_semana", "fecha_cierre",
                "v_fecha_promesa", "fecha_cierre_efectiva", "fecha_ofertado"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    return df


def fetch_oferta_formal_envios_wa() -> pd.DataFrame:
    """Envíos de WhatsApp de Oferta formal MX que efectivamente llegaron.

    Filtra por el template del experimento Y por message_status IN
    ('read','delivered') — solo cuenta mensajes que el cliente recibió
    (excluye undelivered y rejected).
    """
    sql = """
    SELECT nid, message_status, created_at
    FROM `sellers-main-prod.mx_rds_staging.habi_notifications_whatsapp_messages`
    WHERE template_id = "envio_oferta_liquidez_mx_estebancastelblanco_60326"
      AND message_status IN ('read', 'delivered')
    """
    df = _client().query(sql).to_dataframe()
    if not df.empty:
        df["nid"] = pd.to_numeric(df["nid"], errors="coerce").astype("Int64")
        df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    return df


def fetch_oferta_formal_landing_tracks() -> pd.DataFrame:
    """Eventos individuales (Segment tracks) en ofertas.tuhabi.mx.

    Devuelve un row por evento disparado con uuid, event_name y timestamp.
    """
    sql = r"""
    SELECT
      REGEXP_EXTRACT(context_page_url, r'([0-9a-fA-F\-]{36})') AS uuid,
      event AS event_name,
      timestamp
    FROM `sellers-main-prod.javascript9.tracks`
    WHERE context_page_url LIKE 'https://ofertas.tuhabi.mx/%'
      AND event IS NOT NULL
    """
    df = _client().query(sql).to_dataframe()
    if not df.empty:
        df["uuid"] = df["uuid"].astype(str).str.lower()
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def fetch_oferta_formal_landing_events() -> pd.DataFrame:
    """Eventos de página en https://ofertas.tuhabi.mx/<uuid>.

    Devuelve un row por UUID con conteo total de vistas (events) y la primera
    vez que apareció en BQ. Filtra el dominio MX del experimento Oferta formal.
    """
    sql = r"""
    SELECT
      REGEXP_EXTRACT(context_page_url, r'([0-9a-fA-F\-]{36})') AS uuid,
      COUNT(*) AS events,
      MIN(timestamp) AS first_seen,
      MAX(timestamp) AS last_seen
    FROM `sellers-main-prod.javascript9.pages`
    WHERE context_page_url LIKE 'https://ofertas.tuhabi.mx/%'
      AND REGEXP_CONTAINS(context_page_url, r'tuhabi\.mx/[0-9a-fA-F\-]{36}')
    GROUP BY 1
    """
    df = _client().query(sql).to_dataframe()
    if not df.empty:
        df["first_seen"] = pd.to_datetime(df["first_seen"], errors="coerce")
        df["last_seen"] = pd.to_datetime(df["last_seen"], errors="coerce")
        df["uuid"] = df["uuid"].astype(str).str.lower()
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Oferta formal COL · dashboard (simplificado vs query maestra completa)
# ─────────────────────────────────────────────────────────────────────────────
OFERTA_FORMAL_COL_VARIANT_START = "2026-02-16"
OFERTA_FORMAL_COL_DEALSTAGE_OFERTADO = "1172812204"
OFERTA_FORMAL_COL_LANDING_HOST = "ofertas.habi.co"


def fetch_oferta_formal_col_master() -> pd.DataFrame:
    """Tabla maestra del experimento Oferta formal COL para el dashboard.

    Versión simplificada de la query maestra COL: omite armado (im-main-prod),
    subsidios y joins analíticos pesados. Conserva la lógica clave del
    experimento:
      - ofertas desde detalle_ofertas_col
      - variante abc_test_landing_co solo si fecha_ofertado >= 2026-02-16
      - cierres desde int_sellers_cierres_desistidos_co_dwh
      - fecha_ofertado vía dealstage 1172812204 en hubspot.historical
    """
    variant_start = OFERTA_FORMAL_COL_VARIANT_START
    dealstage_ofertado = OFERTA_FORMAL_COL_DEALSTAGE_OFERTADO
    sql = f"""
    WITH
    base_ofertas AS (
      SELECT
        CAST(nid AS INT64) AS nid,
        DATE(fecha_aprobado) AS fecha_aprobado,
        DATE(fecha_cierre)   AS fecha_cierre,
        estado_aprobado,
        equipo_sellers
      FROM `papyrus-data.habi_wh.detalle_ofertas_col`
      WHERE fecha_aprobado IS NOT NULL
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY nid, DATE(fecha_aprobado) ORDER BY fecha_aprobado DESC
      ) = 1
    ),
    base_hubspot AS (
      SELECT
        CAST(d.nid AS INT64) AS nid,
        d.abc_test_landing_co,
        d.ab_test_landing,
        LOWER(NULLIF(TRIM(d.deal_uuid), '')) AS deal_uuid,
        d.pipeline,
        d.hubspot_owner_id,
        d.area_metropolitana,
        TRIM(CONCAT(IFNULL(o.first_name, ''), ' ', IFNULL(o.last_name, ''))) AS owner_name,
        SAFE_CAST(d.precio_comite AS FLOAT64) AS precio_comite,
        SAFE_CAST(d.ask_price_despues__de_remodelacion AS FLOAT64) AS customer_price,
        SAFE_CAST(d.precio_comite_final_final_final__el_unico____clonada_ AS FLOAT64) AS oferta_final_calculada,
        CASE
          WHEN LOWER(TRIM(COALESCE(d.negocio_aplica_para_bnpl_, '')))
               IN ('sí', 'si', 'true', 'yes', '1') THEN 'Sí'
          ELSE 'No'
        END AS negocio_aplica_para_bnpl
      FROM `sellers-main-prod.hubspot.deals` d
      LEFT JOIN `sellers-main-prod.hubspot.owners` o
        ON LOWER(o.email) = LOWER(d.hubspot_owner_id)
        OR CAST(o.id AS STRING) = CAST(d.hubspot_owner_id AS STRING)
      WHERE LOWER(TRIM(COALESCE(d.country, ''))) IN ('colombia', 'co')
    ),
    pasaron_ofertados AS (
      SELECT
        CAST(nid AS INT64) AS nid,
        MIN(fecha) AS fecha_ofertado
      FROM `sellers-main-prod.hubspot.historical`
      WHERE propiedad = 'dealstage' AND valor = '{dealstage_ofertado}'
      GROUP BY 1
    ),
    base_cierres_raw AS (
      SELECT
        CAST(nid AS INT64) AS nid,
        DATE(v_fecha_promesa) AS v_fecha_promesa
      FROM `papyrus-master.operations_sellers_co_dwh.int_sellers_cierres_desistidos_co_dwh`
      WHERE v_fecha_promesa IS NOT NULL
    ),
    -- Cierre estilo query maestra COL: la promesa debe ser >= fecha_aprobado y se
    -- toma la última (MAX) ligada a ese aprobado. (Antes se usaba MIN sin la
    -- restricción, lo que inflaba el cierre ~+9 deals vs Looker.)
    base_cierres_col AS (
      SELECT o.nid, o.fecha_aprobado, c.v_fecha_promesa
      FROM base_ofertas o
      LEFT JOIN base_cierres_raw c ON c.nid = o.nid
      WHERE o.fecha_aprobado <= c.v_fecha_promesa
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY o.nid, o.fecha_aprobado ORDER BY c.v_fecha_promesa DESC
      ) = 1
    ),
    -- Dealstage "inmueble aprobado" (1172812203). El reporte de Looker filtra el
    -- universo a deals que pasaron por esta etapa ("Fecha inmueble aprobado").
    fecha_inmueble_aprobado AS (
      SELECT
        CAST(nid AS INT64) AS nid,
        MIN(DATE(fecha)) AS fecha_inmueble_aprobado
      FROM `sellers-main-prod.hubspot.historical`
      WHERE propiedad = 'dealstage' AND valor = '1172812203'
      GROUP BY 1
    )
    SELECT
      o.nid,
      CASE
        WHEN DATE(po.fecha_ofertado) >= '{variant_start}' THEN hs.abc_test_landing_co
        ELSE NULL
      END AS abc_test_landing_co,
      hs.ab_test_landing,
      hs.deal_uuid,
      hs.pipeline,
      hs.hubspot_owner_id,
      hs.area_metropolitana,
      hs.owner_name,
      hs.negocio_aplica_para_bnpl,
      o.equipo_sellers,
      o.estado_aprobado,
      o.fecha_aprobado,
      DATE_TRUNC(o.fecha_aprobado, WEEK(MONDAY)) AS fecha_aprobado_semana,
      o.fecha_cierre,
      c.v_fecha_promesa,
      IFNULL(o.fecha_cierre, c.v_fecha_promesa) AS fecha_cierre_efectiva,
      DATE(po.fecha_ofertado) AS fecha_ofertado,
      fia.fecha_inmueble_aprobado,
      IF(po.nid IS NOT NULL, 'Ofertado', 'No ofertado') AS fue_ofertado,
      CASE
        WHEN SAFE_DIVIDE(hs.oferta_final_calculada - hs.customer_price, hs.customer_price) IS NULL THEN NULL
        WHEN SAFE_DIVIDE(hs.oferta_final_calculada - hs.customer_price, hs.customer_price) > -0.16 THEN 'Baja diferencia (20%)'
        WHEN SAFE_DIVIDE(hs.oferta_final_calculada - hs.customer_price, hs.customer_price) >= -0.30 THEN 'Media diferencia (13%)'
        WHEN SAFE_DIVIDE(hs.oferta_final_calculada - hs.customer_price, hs.customer_price) < -0.30 THEN 'Alta diferencia (5%)'
        ELSE NULL
      END AS categoria_ancla
    FROM base_ofertas o
    LEFT JOIN base_hubspot hs ON hs.nid = o.nid
    LEFT JOIN pasaron_ofertados po ON po.nid = o.nid
    LEFT JOIN base_cierres_col c ON c.nid = o.nid AND c.fecha_aprobado = o.fecha_aprobado
    LEFT JOIN fecha_inmueble_aprobado fia ON fia.nid = o.nid
    """
    df = _client().query(sql).to_dataframe()
    for col in ("fecha_aprobado", "fecha_aprobado_semana", "fecha_cierre",
                "v_fecha_promesa", "fecha_cierre_efectiva", "fecha_ofertado",
                "fecha_inmueble_aprobado"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    return df


def fetch_oferta_formal_col_envios_wa() -> pd.DataFrame:
    """Envíos WA Oferta formal COL (read/delivered).

    Sin filtro de template_id hasta confirmar el ID del workflow CO; acota
    por país vía deals Colombia en HubSpot.
    """
    sql = """
    WITH co_nids AS (
      SELECT DISTINCT CAST(nid AS INT64) AS nid
      FROM `sellers-main-prod.hubspot.deals`
      WHERE country = 'Colombia' AND nid IS NOT NULL
    )
    SELECT w.nid, w.message_status, w.created_at
    FROM `sellers-main-prod.co_rds_staging.habi_notifications_whatsapp_messages` w
    INNER JOIN co_nids n ON CAST(w.nid AS INT64) = n.nid
    WHERE w.message_status IN ('read', 'delivered')
    """
    df = _client().query(sql).to_dataframe()
    if not df.empty:
        df["nid"] = pd.to_numeric(df["nid"], errors="coerce").astype("Int64")
        df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    return df


def fetch_oferta_formal_col_landing_tracks() -> pd.DataFrame:
    """Eventos Segment (tracks) en https://ofertas.habi.co/<uuid>.

    Misma tabla y patrón que MX; solo cambia el host de la landing.
    """
    sql = r"""
    SELECT
      REGEXP_EXTRACT(context_page_url, r'([0-9a-fA-F\-]{36})') AS uuid,
      event AS event_name,
      timestamp
    FROM `sellers-main-prod.javascript9.tracks`
    WHERE context_page_url LIKE 'https://ofertas.habi.co/%'
      AND event IS NOT NULL
    """
    df = _client().query(sql).to_dataframe()
    if not df.empty:
        df = df[df["uuid"].notna()].copy()
        df["uuid"] = df["uuid"].astype(str).str.lower()
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def fetch_oferta_formal_col_landing_events() -> pd.DataFrame:
    """Page views agregadas por UUID en https://ofertas.habi.co/<uuid>.

    Misma tabla y patrón que MX; solo cambia el host de la landing.
    """
    sql = r"""
    SELECT
      REGEXP_EXTRACT(context_page_url, r'([0-9a-fA-F\-]{36})') AS uuid,
      COUNT(*) AS events,
      MIN(timestamp) AS first_seen,
      MAX(timestamp) AS last_seen
    FROM `sellers-main-prod.javascript9.pages`
    WHERE context_page_url LIKE 'https://ofertas.habi.co/%'
      AND REGEXP_CONTAINS(context_page_url, r'habi\.co/[0-9a-fA-F\-]{36}')
    GROUP BY 1
    HAVING uuid IS NOT NULL
    """
    df = _client().query(sql).to_dataframe()
    if not df.empty:
        df["first_seen"] = pd.to_datetime(df["first_seen"], errors="coerce")
        df["last_seen"] = pd.to_datetime(df["last_seen"], errors="coerce")
        df["uuid"] = df["uuid"].astype(str).str.lower()
    return df
