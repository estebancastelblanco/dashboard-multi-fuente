WITH urls AS (
  SELECT
    context_page_url,
    REGEXP_EXTRACT(context_page_url, r'([0-9a-fA-F\-]{36})') AS uuid,
    'pages' AS source
  FROM `sellers-main-prod.javascript9.pages`
  WHERE context_page_url IS NOT NULL
    AND context_page_url LIKE 'https://habicapitalliquidez.vercel.app/%'

  UNION ALL

  SELECT
    context_page_url,
    REGEXP_EXTRACT(context_page_url, r'([0-9a-fA-F\-]{36})') AS uuid,
    'tracks' AS source
  FROM `sellers-main-prod.javascript9.tracks`
  WHERE context_page_url IS NOT NULL
    AND context_page_url LIKE 'https://habicapitalliquidez.vercel.app/%'
)

SELECT
  context_page_url,
  uuid,
  source,
  COUNT(*) AS total_events
FROM urls
WHERE uuid IS NOT NULL
GROUP BY context_page_url, uuid, source
ORDER BY total_events DESC;