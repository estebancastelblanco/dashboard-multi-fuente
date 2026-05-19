------------------------------------------
-- TABLA MAESTRA EXPERIMENTOS MX
------------------------------------------

WITH

-------------------------------
-- ARMADO
-------------------------------

base_armado AS (
SELECT
  CAST(nid AS INT64) AS nid, 
  SAFE.PARSE_JSON(REPLACE(idm_hesh, "\'", "\"")) AS idm_hesh,
  SAFE.PARSE_JSON(REPLACE(diff_price, "\'", "\"")) AS diff_price, 
  DATETIME(fecha_ejecucion) - INTERVAL 5 HOUR AS fecha_ejecucion_armado,
  precio_final_prestamo AS precio_esperado_compra
FROM  `im-main-prod.habi_wh_analytics.price_building_mx_v2`
WHERE fecha_ejecucion >='2026-01-26'
QUALIFY ROW_NUMBER() OVER(PARTITION BY nid, DATE(fecha_ejecucion) ORDER BY DATETIME(fecha_ejecucion) DESC) = 1 -- ultima ejecución de armado

),

base_armado_v2 AS (
SELECT
  *, 
  JSON_VALUE(JSON_EXTRACT(JSON_EXTRACT(diff_price, '$.comision'),'$.ab_test')) AS ab_test,
  JSON_VALUE(JSON_EXTRACT(JSON_EXTRACT(diff_price, '$.comision'),'$.experiment_name')) AS experiment_name_raw,
  CAST(JSON_VALUE(JSON_EXTRACT(JSON_EXTRACT(diff_price, '$.comision'),'$.precio_base')) as FLOAT64) AS precio_base,
  CAST(JSON_VALUE(JSON_EXTRACT(JSON_EXTRACT(diff_price, '$.comision'),'$.precio_maximo')) as FLOAT64) AS precio_maximo,
  CAST(JSON_VALUE(JSON_EXTRACT(JSON_EXTRACT(diff_price, '$.comision'),'$.precio_minimo')) as FLOAT64) AS precio_minimo,
  CAST(JSON_VALUE(JSON_EXTRACT(JSON_EXTRACT(diff_price, '$.comision'),'$.precio_ancla')) as FLOAT64) AS precio_ancla,
  JSON_VALUE(idm_hesh.general_hesh_hs_response.db.hubspot.ask_price_comite_mx_hesh) AS precio_esperado_venta,
FROM base_armado
QUALIFY ROW_NUMBER() OVER(PARTITION BY nid, DATE(fecha_ejecucion_armado)) = 1 -- doble check porque aveces hay dos ejecuciones en el último minuto
),

-- -------------------------------
-- -- OFERTAS
-- -------------------------------
pre_detalle_ofertas as (
  SELECT
	dox.*,
	ig.fuente_id,
	ig.fuente,
FROM
`papyrus-data.habi_wh.detalle_ofertas_mx` AS dox 
LEFT JOIN `papyrus-data-mx.habi_wh_bi.tabla_inmuebles_general` AS ig ON dox.nid=ig.nid
LEFT JOIN `sellers-main-prod.hubspot_staging.deal` AS hd ON  dox.nid=hd.nid
LEFT JOIN (SELECT 
              da.deal_id,
              ig.nid,
              JSON_VALUE(meta, '$.apply_real_estate_first') AS inmo_first
            FROM 
                `papyrus-data-mx.habi_wh_sellers.deal_additional` AS da
                LEFT JOIN `papyrus-data-mx.habi_wh_bi.tabla_inmuebles_general` as ig on ig.id_negocio = da.deal_id
            WHERE 
                JSON_EXTRACT_SCALAR(meta, '$.apply_real_estate_first') = "true") AS inf ON dox.nid=inf.nid
WHERE 1=1
AND asignacion_descartes_top IS NULL  
AND inf.nid IS NULL
-- and flag_recurrecia_gestion ='Primer gestión'
),

base_ofertas as (
SELECT
  *,
  IF(fecha_aprobado IS NOT NULL, 1, 0) AS d_aprobado, 
  IF(fecha_cierre IS NOT NULL, 1, 0) AS d_cierre, 
  DATE_TRUNC(fecha_cierre, WEEK(MONDAY)) semana_cierre,
  DATE_TRUNC(fecha_aprobado, WEEK(MONDAY)) semana_aprobado,
  IFNULL(DATE_DIFF(fecha_cierre, fecha_aprobado, WEEK), 50) AS weeks_apro_cie,
  ROW_NUMBER() OVER(PARTITION BY nid ORDER BY fecha_aprobado) AS orden_aprobado
FROM pre_detalle_ofertas
),

-- -------------------------------
-- -- HUSBPOT
-- -------------------------------

base_hubspot AS (
SELECT
  nid, 
  SAFE_CAST(valor_subsidiado AS FLOAT64) AS valor_subsidiado, 
  SAFE_CAST(valor_subsidiado_extraordinario AS FLOAT64) AS valor_subsidiado_extraordinario,
  subsidio_aprobado_mx,
  quiere_solicitar_subsidio_,
  valor_solicitado_de_subsidio,
  final_final_aprobado_bo_prestamo_mx_calculo,
  final_prestamo_mx,
  flag_precio_comite_hesh,
  ask_price_comite_mx_hesh,
  ask_price_comite_mx,
  valor_pintura,
  valor_mejoras,
  valor_negociado,
  ask_price as customer_price,
  precio_ancla as precio_ancla_hs,
  oferta_final_prestamo_mx_calculada,
  equipo_sellers,
  deal_uuid,
  razon_de_venta_usuario_gabi_mx,
  ab_test_landing,
  abc_test_landing_co
FROM `sellers-main-prod.hubspot.deals`
),
-- -------------------------------
-- -- HUSBPOT historico
-- -------------------------------
pasaron_ofertados as(
select * from `sellers-main-prod.hubspot.historical` h 
where 
1=1
and propiedad = 'dealstage'
and valor = '1066441580'
QUALIFY ROW_NUMBER() OVER(PARTITION BY nid, DATE(fecha) ORDER BY DATETIME(fecha) asc) = 1 
),
-- -------------------------------
-- -- Informacion de las landings
-- -------------------------------

pages AS (
  SELECT
    REGEXP_EXTRACT(context_page_url, r'([0-9a-fA-F\-]{36})') AS uuid,
    COUNT(*) AS pages_count
  FROM `sellers-main-prod.javascript9.pages`
  WHERE context_page_url IS NOT NULL
  GROUP BY uuid
),

tracks AS (
  SELECT
    REGEXP_EXTRACT(context_page_url, r'([0-9a-fA-F\-]{36})') AS uuid,
    COUNT(*) AS tracks_count
  FROM `sellers-main-prod.javascript9.tracks`
  WHERE context_page_url IS NOT NULL
  GROUP BY uuid
),

base_landing as(
SELECT  
  COALESCE(p.uuid, t.uuid) AS uuid,
  COALESCE(p.pages_count, 0) AS pages_count,
  COALESCE(t.tracks_count, 0) AS tracks_count,
  razon_de_venta_usuario_gabi_mx
   
FROM pages p
FULL OUTER JOIN tracks t
    ON p.uuid = t.uuid
left join `sellers-main-prod.hubspot.deals` hd on hd.deal_uuid = p.uuid
WHERE COALESCE(p.uuid, t.uuid) IS NOT NULL 
-- and hd.deal_uuid is null 
-- and hd.razon_de_venta_usuario_gabi_mx = "Liquidez"
ORDER BY pages_count DESC, tracks_count DESC
),
-- -------------------------------
-- -- Informacion de las landings
-- -------------------------------
fecha_ofertas as (
  select h.nid, h.propiedad, h.fecha as fecha_ofertado,  hd.abc_test_landing_co from sellers-main-prod.hubspot.historical h
-- left join mas m on m.nid = h.nid 
left join papyrus-data-mx.habi_wh_bi.tabla_inmuebles_general ig on ig.nid= h.nid
left join sellers-main-prod.hubspot.deals hd on hd.nid = h.nid
where 
1=1 
and h.propiedad = 'dealstage' and valor = '1066441580'
-- and m.nid is null
-- and h.fecha >= '2026-02-18'
--and hd.abc_test_landing_co is not null
and ig.nid is not null
qualify row_number() over(partition by h.nid order by h.fecha asc) = 1
),

fecha_inmueble_aprobado as (

  select h.nid, h.propiedad, h.fecha as fecha_inmueble_aprobado,  from sellers-main-prod.hubspot.historical h

where 
1=1 
and h.propiedad = 'dealstage' and valor = '1066441579'
qualify row_number() over(partition by h.nid order by h.fecha asc) = 1
),

-- -------------------------------
-- -- CIERRES OCD
-- -------------------------------

base_cierres AS (
SELECT
  nid,
  v_fecha_cierre as v_fecha_promesa,
  v_precio_de_compra AS precio_real_compra
FROM `papyrus-master.operations_sellers_mx_dwh.int_sellers_cierres_y_precierres_mx_dwh`
),

-- -------------------------------
-- -- base para relacionar las aprobaciones con la ejecucion de armado inmediatamente anterior
-- -------------------------------

base_auxiliar_armado AS (
SELECT
  o.nid, 
  o.fecha_aprobado, 
  o.fecha_creacion,
  CAST(a.precio_esperado_compra AS FLOAT64) AS precio_esperado_compra,
  CAST(a.precio_esperado_venta AS FLOAT64) AS precio_esperado_venta,
  a.ab_test AS experiment_group, 
  a.diff_price, 
  a.fecha_ejecucion_armado, 
  a.precio_base, 
  a.precio_maximo, 
  a.precio_minimo,
  a.precio_ancla, 
  CASE 
    WHEN a.ab_test is not null and a.fecha_ejecucion_armado between '2025-11-10' and '2025-12-31' THEN 'mx-rangos-20251110'
    ELSE a.experiment_name_raw END AS experiment_id
FROM base_ofertas o
LEFT JOIN base_armado_v2 a ON o.nid=a.nid
WHERE a.fecha_ejecucion_armado <= o.fecha_aprobado -- ejecucion de armado debe ser anterior a aprobado
QUALIFY ROW_NUMBER() OVER(PARTITION BY o.nid, o.fecha_aprobado ORDER BY a.fecha_ejecucion_armado DESC) = 1 -- se toma la fecha de armado inmediatamente anterior a aprobado
),

-- -------------------------------
-- -- JOIN ofertas + armado
-- -------------------------------

base_final_mm AS (
SELECT
  IFNULL(o.nid, a.nid) AS nid,
  'Market Maker' AS business_line,
  --- ofertas
  o.fecha_aprobado,
  o.fecha_cierre,
  o.fecha_ejecucion_hesh,
  o.fecha_rechazo,
  o.fecha_creacion,
  o.d_aprobado, 
  o.d_cierre, 
  o.semana_cierre,
  o.semana_aprobado,
  o.weeks_apro_cie,
  o.orden_aprobado,
  o.estado_aprobado,
  CONCAT(o.nid, '-', o.orden_aprobado) AS offer_id,
  COUNT(o.d_aprobado) OVER (PARTITION BY o.semana_aprobado) AS tot_aprobados_semana,
  COUNT(o.d_cierre) OVER (PARTITION BY o.semana_aprobado ORDER BY o.weeks_apro_cie ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS tot_cerrados_semana_cum,
  DIV(EXTRACT(DAY FROM o.semana_aprobado) - 1, 7) + 1 AS semana_del_mes,
  DATE_DIFF(o.fecha_rechazo, o.fecha_aprobado, DAY) AS dias_para_rechazo,
  --- armado
  a.precio_esperado_compra,
  a.precio_esperado_venta,
  a.experiment_group, 
  a.diff_price, 
  a.fecha_ejecucion_armado, 
  a.precio_base, 
  a.precio_maximo, 
  a.precio_minimo, 
  a.precio_ancla,
  a.experiment_id,
FROM base_ofertas AS o 
left JOIN base_auxiliar_armado AS a ON o.nid=a.nid AND DATE(a.fecha_aprobado)=DATE(o.fecha_aprobado) -- se une a las ofertas la ejecucion de armado inmediatamente anterior
),

-- -------------------------------
-- -- base para relacionar las promesas con el aprobado inmediatamente anterior
-- -------------------------------

base_auxiliar_cierres AS (
SELECT
  bf.nid,
  bf.fecha_aprobado,
  bf.fecha_cierre,
  c.v_fecha_promesa, 
  c.precio_real_compra
FROM base_final_mm bf
LEFT JOIN base_cierres c ON bf.nid=c.nid
WHERE bf.fecha_aprobado <= c.v_fecha_promesa AND bf.fecha_cierre IS NOT NULL
QUALIFY ROW_NUMBER() OVER(PARTITION BY bf.nid, bf.fecha_aprobado ORDER BY c.v_fecha_promesa DESC) = 1 -- se toma la ultima promesa por cada aprobado
) ,

-- -------------------------------
-- -- FINAL
-- -------------------------------

base_final_mm_v2 AS (
SELECT
  bf.*,
  date_trunc(bf.fecha_aprobado,week) as fecha_aprobado_semana,
  date_trunc(bf.fecha_aprobado,WEEK(WEDNESDAY)) as fecha_aprobado_semana_comercial,
  date_trunc(bf.fecha_aprobado,month) as fecha_aprobado_mes,
  ac.v_fecha_promesa, 
  ac.precio_real_compra, 
  (1 - (bf.precio_esperado_compra / bf.precio_esperado_venta)) as margen_oferta,
  (precio_esperado_compra - precio_real_compra) / precio_esperado_venta as margin_variance, 
  hs.valor_subsidiado, 
  IF(hs.valor_subsidiado > 0, 1, 0) AS d_subsidio,
  hs.valor_subsidiado_extraordinario, 
  hs.valor_negociado,
  case when po.nid is not null then 'Ofertado' else 'No ofertado' end as fue_ofertado,
  IF(hs.valor_subsidiado_extraordinario > 0, 1, 0) AS d_subsidio_extraordinario, 
  CASE
    WHEN precio_esperado_compra BETWEEN 0 AND 100000000 THEN '1. 0-100MM'
    WHEN precio_esperado_compra BETWEEN 100000000 AND 150000000 THEN '2. 100MM-150MM'
    WHEN precio_esperado_compra BETWEEN 150000000 AND 200000000 THEN '3. 150MM-200MM'
    WHEN precio_esperado_compra BETWEEN 200000000 AND 250000000 THEN '4. 200MM-250MM'
    WHEN precio_esperado_compra BETWEEN 250000000 AND 300000000 THEN '5. 250MM-300MM'
    WHEN precio_esperado_compra >= 300000000 THEN '6. +300MM'
    ELSE NULL END AS precio_esperado_compra_cat,
  CASE
    WHEN ti.tipo_inmueble_id = 1 THEN '1. Apto en condiminio'
    WHEN ti.tipo_inmueble_id = 2 THEN '2. Casa sola'
    WHEN ti.tipo_inmueble_id = 3 THEN '3. Casa en condiminio'
    WHEN ti.tipo_inmueble_id = 4 THEN '4. Apto solo'
    ELSE NULL END AS tipo_inmueble_id,
    customer_price,

  -- Métricas para COMISIÓN
        SAFE_DIVIDE(valor_negociado - hs.precio_ancla_hs, hs.precio_ancla_hs) AS var_ancla_comision,
        ABS(SAFE_DIVIDE(valor_negociado - hs.precio_ancla_hs, hs.precio_ancla_hs)) AS abs_var_ancla_comision,
        
        -- Métrica Base vs Customer (COMISIÓN)
        SAFE_DIVIDE(oferta_final_prestamo_mx_calculada - customer_price, customer_price) AS var_base_comision_vs_customer,
        CASE 
        WHEN SAFE_DIVIDE(oferta_final_prestamo_mx_calculada - customer_price, customer_price)  IS NULL THEN NULL
        WHEN SAFE_DIVIDE(oferta_final_prestamo_mx_calculada - customer_price, customer_price)  > -0.16 THEN 'Baja diferencia (20%)'
        WHEN SAFE_DIVIDE(oferta_final_prestamo_mx_calculada - customer_price, customer_price)  >= -0.30 THEN 'Media diferencia (13%)'
        WHEN SAFE_DIVIDE(oferta_final_prestamo_mx_calculada - customer_price, customer_price)  < -0.30 THEN 'Alta diferencia (5%)'
        ELSE NULL
    END AS score_variacion,
        
  tam.name AS metropolitan_area,
  (hs.valor_subsidiado-500000)/bf.precio_base AS pct_valor_subsidiado, -- se resta 500mil por el discrecional
  hs.valor_subsidiado_extraordinario/bf.precio_base AS pct_valor_subsidiado_extraordinario,
  oferta_final_prestamo_mx_calculada,
  --- límites para monitoreo
  lim.mean_cvr, 
  lim.std_cvr, 
  lim.limit_1std_cvr, 
  lim.mean_margin_variance, 
  lim.std_margin_variance, 
  lim.limit_1std_margin_variance,
  di.valor_compra,
  IF(hs.flag_precio_comite_hesh = 1, SAFE_CAST(hs.ask_price_comite_mx_hesh AS FLOAT64) + hs.valor_pintura + hs.valor_mejoras*2,
    SAFE_CAST(hs.ask_price_comite_mx as FLOAT64)+ hs.valor_pintura + hs.valor_mejoras*2) as ask_comite_post_remo,
  hs.flag_precio_comite_hesh as hesh,
  di.utilidad_esperada_comite as utilidad_esperada,
  hs.final_prestamo_mx,
  hs.precio_ancla_hs,
  hs.equipo_sellers,
  hs.deal_uuid,
  hs.razon_de_venta_usuario_gabi_mx,
  bl.pages_count,
  bl.tracks_count,
  hs.ab_test_landing,
  hs.abc_test_landing_co,
  fecha_ofertado,
  fecha_inmueble_aprobado
FROM base_final_mm bf
LEFT JOIN base_auxiliar_cierres ac ON bf.nid=ac.nid AND bf.fecha_aprobado=ac.fecha_aprobado
LEFT JOIN base_hubspot hs ON bf.nid=hs.nid
LEFT JOIN pasaron_ofertados po ON po.nid = bf.nid
LEFT JOIN `papyrus-data.habi_db.tabla_negocio_inmueble` AS tni ON tni.nid=bf.nid
LEFT JOIN `papyrus-data.habi_db.tabla_inmueble_v2` AS ti ON ti.id = tni.inmueble_id
LEFT JOIN `papyrus-data.habi_db.tabla_localizacion_inmueble_v2` AS tli ON tli.id = ti.localizacion_new_id
LEFT JOIN `papyrus-data.habi_wh.tabla_zona_mediana` AS tzm ON tzm.id = tli.zona_mediana_id
LEFT JOIN `papyrus-data.habi_wh.tabla_zona_grande` AS tzg ON tzg.id = tzm.zona_grande_id
LEFT JOIN `papyrus-data.habi_wh.tabla_ciudad` AS tc ON tc.id = tzg.ciudad_id
LEFT JOIN `papyrus-data.habi_wh.tabla_area_metropolitana` AS tam ON tam.id = tc.area_metropolitana_id
LEFT JOIN `im-main-prod.diff_prices_co.limit_cvr_weekly_co` AS lim ON lim.weeks_apro_cie = bf.weeks_apro_cie AND lim.semana_del_mes= bf.semana_del_mes
left join (select* from papyrus-data-mx.habi_wh_hesh.production_hesh h qualify row_number() over(partition by h.nid,h.tipo_transaccion order by h.fecha_ejecucion_hesh desc) = 1) h on h.nid = bf.nid
LEFT JOIN papyrus-data-mx.habi_wh_hesh.IdM_adquisiciones_datos_inventario di on di.nid = bf.nid
LEFT JOIN base_landing bl on bl.uuid =  hs.deal_uuid
LEFT JOIN fecha_ofertas fo on fo.nid = bf.nid
left join fecha_inmueble_aprobado fia on fia.nid = bf.nid
)

SELECT
  *
FROM base_final_mm_v2
