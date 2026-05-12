# Dashboard Habi Capital · Multi-experimento

App de Streamlit que aloja **dashboards en tiempo real** para los experimentos de Habi Capital. Una sola base de credenciales (BigQuery + HubSpot + Google Sheets) alimenta N dashboards. El primero (y referencia) es el Fake Door del Crédito de Libre Inversión con Garantía Hipotecaria.

> Deploy: https://share.streamlit.io/ apuntando a `estebancastelblanco/dashboard-multi-fuente` (público).
> Sheet de leads en vivo: https://docs.google.com/spreadsheets/d/1vN7wL8a_NvfLks2IvoIICgbV2GrIDKhPbymS3aLft2I/edit

---

## ¿Qué problema resuelve?

Antes los resultados del experimento se construían con un script que descargaba CSVs/XLSX manualmente (`process_leads.py`, `fetch_hubspot.py`, etc. en `/Users/usermac/Hc/resultadosfakedoor/`). Cada nuevo lead o cambio en CRM exigía re-bajar archivos. Eso no escala a *n* experimentos corriendo en paralelo.

Este proyecto reemplaza esa locura por:

- **Conectores en vivo** a Google Sheets (formulario + entrevistas), HubSpot (deals + propiedades del experimento) y BigQuery (eventos de Segment del front).
- **Persistencia en el mismo Sheet**: los scores ya consultados se escriben en columnas `Aplica` + `Metadata` y no se vuelven a pedir.
- **Selector** tipo biblioteca: la home lista los experimentos como cards y cada uno tiene su dashboard.

---

## Arquitectura

```
.
├── streamlit_app.py              # Home — biblioteca de experimentos (cards)
├── pages/
│   ├── 1_FakeDoor_Habicapital.py # Dashboard FakeDoor (KPIs, funnel, pipeline, insights)
│   └── 2_Demo_multi_fuente.py    # Dashboard demo (3 tablas live, 10×4)
├── src/
│   ├── experiments.py            # Registry de experimentos (metadata por card)
│   ├── styling.py                # Paleta morada + CSS
│   └── sources/
│       ├── bigquery.py           # BQ client + queries (production_hesh, landing events)
│       ├── hubspot.py            # Deals con flag_fakedoor, compute_fuente, FUENTES
│       ├── gsheets.py            # fetch_tab + ensure_columns + update_rows_by_key
│       └── risk_score.py         # Score API + cache en Sheet (Aplica/Metadata)
├── experiments/
│   └── fakedoor_habicapital/     # Docs adjuntos al experimento
├── .env / .env.example           # Credenciales locales
├── .streamlit/secrets.toml       # Credenciales para Streamlit Cloud (TOML)
└── requirements.txt
```

Multipágina nativo de Streamlit: cualquier `.py` en `pages/` aparece en el side-nav. La home (`streamlit_app.py`) usa `st.page_link` desde las cards.

---

## Stack

- **Streamlit** (UI y caching)
- **Plotly** (charts: funnel horizontal con escala log, pies, barras)
- **gspread** + `google-auth` (Sheets lectura/escritura)
- **google-cloud-bigquery** (queries a `sellers-main-prod`)
- **requests** (HubSpot CRM v3 + score API)
- **python-dotenv** (carga `.env` local; en cloud usa `st.secrets`)

---

## Fuentes de datos

| Fuente | Qué aporta | Cómo se accede |
|---|---|---|
| **HubSpot** · CRM Deals | Universo del experimento, propiedades custom (flag_fakedoor, ab_test_landing, comite_remodelaciones, oportunidad_del_negocio, nombre_del_conjunto, etc.) | API v3 `/crm/v3/objects/deals/search` con filtro `flag_fakedoor HAS_PROPERTY` + `createdate >= start_date`. Pagina hasta agotar. |
| **Google Sheets** · pestaña `Leads` | Leads que completaron T&C (formulario público). cedula, telefono, grupo (AH/BH), contesto?, y columnas persistidas `Aplica` + `Metadata`. | gspread con scope `spreadsheets` + `drive`. Service account `ctl-reader-service@try12-455405.iam.gserviceaccount.com` con permiso **Editor**. |
| **Google Sheets** · pestaña `Entrevista` | 12 entrevistas cualitativas (P1–P9, tiene hipoteca?). | gspread (mismo client). |
| **Google Sheets** · pestaña `Infobip` | Teléfonos a los que se envió WhatsApp. Headers vacíos → `fetch_tab` cae a `get_all_values()`. | gspread. |
| **BigQuery** · `sellers-main-prod.javascript9.{pages,tracks}` | Eventos Segment de la landing `habicapitalliquidez.vercel.app`. Permite contar abrieron link + tracks events. | OAuth user creds (authorized_user). Ver `src/sources/bigquery.py::fetch_fakedoor_landing_events()`. |
| **Score API (Habi Capital)** · `score-api.habicapital.co/v1/score/<cedula>` | Score ADVANCE 1.1 + Cuota Máxima + Ingresos + Razón. | GET con bearer token. Solo se llama si no hay cache; resultado se persiste al Sheet. Opcional: si `SCORE_API_URL`/`SCORE_API_TOKEN` no están seteados, todos los leads sin valor en `Aplica` aparecen como pendientes. |

---

## Cómo correr local

```bash
git clone git@github.com:estebancastelblanco/dashboard-multi-fuente.git
cd dashboard-multi-fuente
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# editar .env con valores reales (ver siguiente sección para formato)

streamlit run streamlit_app.py
```

Abre `http://localhost:8501`.

---

## Credenciales · dos formatos

| Donde | Formato | Archivo |
|---|---|---|
| Local | `KEY=value` (dotenv) | `.env` (gitignored) |
| Streamlit Cloud | TOML | Settings → Secrets (pega el contenido de `.streamlit/secrets.toml`) |

El código en `streamlit_app.py` y cada `pages/*.py` hace un **bridge** `st.secrets → os.environ` al inicio, así los conectores leen siempre de env vars sin importar el entorno.

### Variables requeridas

```
# BigQuery
BQ_PROJECT_ID=sellers-main-prod
BQ_DATASET_PROJECT=papyrus-data-mx
BQ_DATASET=habi_wh_hesh
BQ_TABLE=production_hesh
GOOGLE_APPLICATION_CREDENTIALS_JSON={"type":"authorized_user", ...}

# HubSpot
HUBSPOT_ACCESS_TOKEN=pat-na1-...

# Google Sheets
GOOGLE_SHEETS_ID=1vN7wL8a_NvfLks2IvoIICgbV2GrIDKhPbymS3aLft2I
GOOGLE_SHEETS_TAB=Leads
GOOGLE_SHEETS_CREDENTIALS={"type":"service_account","project_id":"try12-455405",...}

# Score API (opcional — sin esto los nuevos leads aparecen como "pendiente")
SCORE_API_URL=https://score-api.habicapital.co/v1/score
SCORE_API_TOKEN=<bearer>
```

### Formato TOML (para Streamlit Cloud)

Pegar el contenido de `.streamlit/secrets.toml` exactamente como está — los JSONs grandes (BQ y Sheets creds) van entre `'''...'''` (literal multi-línea) para que las secuencias `\n` del private_key se conserven sin que TOML las interprete.

---

## Deploy en Streamlit Cloud

1. https://share.streamlit.io/ → **Deploy an app**
2. Repository `estebancastelblanco/dashboard-multi-fuente`, branch `main`, main file `streamlit_app.py`.
3. **Advanced settings → Secrets**: pegar el contenido de `.streamlit/secrets.toml`.
4. Deploy. Cada push a `main` redespliega automáticamente.

---

## El experimento FakeDoor

Card en la home con: nombre, fecha inicio (20 abr 2026), link al diseño y a los resultados (ambos en Google Docs), expander con documentos adjuntos (`funnel.md`, `query_segment.sql`).

### Filtros (sidebar)

Los 4 del dashboard original (`/Users/usermac/Hc/resultadosfakedoor/dashboard.py`):

- **Variante** → `ab_test_landing` de HubSpot (AH = 84 meses, BH = 120 meses)
- **Fuente** → calculada con `hubspot.compute_fuente`:
  - `"Top"` si `flag_fakedoor == "Top"`
  - `"Rechazos Remo"` si `comite_remodelaciones` ∈ {4 valores fijos}
  - `"Rechazos Comite"` si `oportunidad_del_negocio == "Descartado por comité"`
  - `"MM + Inmo"` por defecto
- **Oportunidad del Negocio** → `oportunidad_del_negocio`
- **Estado del Negocio** → `estado` (custom de Habi, NO el `dealstage` estándar de HubSpot)

Cada filtro narrows down `df_hs` y propaga al funnel, distribuciones, pipeline y tabla raw.

### Funnel (7 etapas live, sin filtro de fecha)

| # | Etapa | Cómo se computa |
|---|---|---|
| 1 | Universo (flag fakedoor) | `len(df_hs)` con `flag_fakedoor HAS_PROPERTY` — sin filtro de fecha |
| 2 | Con nombre del conjunto | `df_hs` donde `nombre_del_conjunto` ≠ vacío |
| 3 | Enviados WA | `77% × Con conjunto` (constante histórica de Infobip — no hay API live) |
| 4 | Abrieron página | `len(BQ pages uuids ∩ HS allowed_uuids)` |
| 5 | T&C firmados | `len(Sheets/Leads ∩ HS allowed_uuids)` |
| 6 | Elegibles | `Leads where Aplica = "si"` |
| 7 | Aplican (sin hipoteca) | Elegibles − `tiene hipoteca? == "si"` |

Hover en cada barra muestra el porcentaje vs etapa previa y la fuente del dato.

Las métricas de **usabilidad de la landing** (tracks events, llegaron a consentimiento, total eventos) van en una sección aparte debajo del funnel, alimentadas por BigQuery.

### Pipeline de leads (la vista clave)

Tabla coloreada de los leads que firmaron T&C, ordenada por **prioridad de llamada**:

| Color | Significado |
|---|---|
| 🟢 Verde oscuro | Aplica + ya contactado |
| 🟩 Verde claro | Aplica + **NO contactado** → call list |
| 🟡 Amarillo | Pendiente de consultar score (sin valor en `Aplica`) |
| ⬜ Gris | No aplica (score <720 o con hipoteca) |
| 🔴 Rojo | Error en consulta |

Columnas: Nombre, Teléfono, Cédula, Grupo, Fuente, Contesto?, Hipoteca?, Score, Nivel, Aplica, **Cuota Máxima**, **Ingresos**, **Razón** — los tres últimos vienen del JSON de la columna `Metadata` del Sheet.

### Distribuciones

Cuatro gráficos sobre `df_hs` filtrado:
- Pie de **Fuente** (4 categorías)
- Pie de **Variante A/B**
- Barra horizontal **Top-10 Oportunidad del Negocio**
- Barra horizontal **Top-10 Estado del Negocio**

### Tabla raw de deals filtrados

Vista tabular de los HubSpot deals que pasaron los filtros, con columnas clave (dealname, phone, createdate, estado, fuente, flag, variante, oportunidad, conjunto, comite_remodelaciones, deal_uuid). Útil para inspeccionar el universo después de filtrar.

### Insights de entrevistas

Reemplaza la tabla raw por:
- Pie de **¿Tiene hipoteca?** (sí/no/sin dato)
- Expanders por pregunta con respuestas listadas:
  - P1 — Trigger de demanda
  - P5 — Plazo preferido
  - P8 — Objeciones / fricción
  - P9 — Urgencia

---

## Persistencia de scores · Cómo funciona

El problema: cada vez que se carga el dashboard, no queremos pedirle el score al API a 38+ cédulas (costo, latencia, rate limit). Solución: **el Sheet es el cache**.

Al `Leads` se le agregaron dos columnas vía `gsheets.ensure_columns()`:

| Columna | Contenido |
|---|---|
| `Aplica` | `si` / `no` / `error` — derivado del score (≥720 = sí) |
| `Metadata` | JSON con la respuesta cruda del API: Aplica, Score, Nivel Riesgo, Ingresos Mensuales, Fuente Ingresos, Deudas Vigentes, Disponible Mensual, Máx. Crédito, Cuota Máxima, Vigencia Aprobación, Notas, Score Mín 720, Razón, Estado |

### Flow por lead

```
si Aplica está lleno en el Sheet:
    leer el JSON de Metadata → usar el cache
si Aplica está vacío y SCORE_API_URL+SCORE_API_TOKEN están seteados:
    GET https://score-api.habicapital.co/v1/score/<cedula>
    parsear respuesta → score, nivel_riesgo, etc.
    escribir Aplica + Metadata al Sheet (gspread batch_update)
si no hay API configurada:
    aplica = "pending" → lead aparece amarillo en el pipeline
```

### Bootstrap histórico ya cargado

Los 26 scores históricos del `quienesaplican.xlsx` original (6 sí, 16 no, 4 errores) **ya están escritos** en el Sheet. Se hizo una vez desde un script que matched por cédula. Quedan 12 leads nuevos sin consultar — esos amanecen amarillos hasta que se configure el score API.

### Panel "Estado de consulta de scores"

Expander en el dashboard que muestra: API configurado (sí/no), hits de cache, consultados ahora, pendientes, escritos al Sheet, y errores de write si los hay. Útil para verificar que el flujo funciona después de configurar SCORE_API.

---

## Cómo agregar un experimento nuevo

1. Decidir el `slug` (kebab-case) y crear la entrada en `src/experiments.py`:
   ```python
   Experiment(
       slug="mi-experimento",
       title="Mi Experimento",
       start_date="2026-XX-XX",
       end_date=None,
       design_doc_url="https://docs.google.com/document/d/...",
       results_doc_url="https://docs.google.com/document/d/...",
       page="pages/3_Mi_Experimento.py",
       description="...",
       attachments=["experiments/mi_experimento/funnel.md"],
   )
   ```
2. Crear `pages/3_Mi_Experimento.py`. Copiar el patrón de `pages/1_FakeDoor_Habicapital.py`:
   - `_bootstrap_from_st_secrets()` al inicio
   - `inject_base_css()` para el styling
   - Loaders cacheados (`@st.cache_data(ttl=120)`)
   - Sidebar con filtros
   - Secciones (KPIs → funnel → distribuciones → pipeline → tabla raw → insights → A/B)
3. (Opcional) Adjuntos no-PII en `experiments/<slug>/`.
4. Si necesita propiedades de HubSpot distintas, agregar a `src/sources/hubspot.py::FAKEDOOR_PROPS` (renombrar al hacer abstracción multi-experimento).

---

## Permisos del service account

`ctl-reader-service@try12-455405.iam.gserviceaccount.com` debe tener acceso de **Editor** en el Google Sheet (no Viewer). Si no, el `ensure_columns()` y `update_rows_by_key()` fallan con 403. Verificar en Sheet → Share → buscar el email del service account → confirmar permiso.

---

## Troubleshooting

**`GSpreadException: get_all_records ... headers`**
La pestaña tiene headers vacíos o duplicados. `gsheets.fetch_tab` ya cae a `get_all_values()` y deduplica automáticamente.

**`ModuleNotFoundError: plotly`**
Streamlit Cloud no instaló plotly. Verifica que `plotly>=5.20` esté en `requirements.txt`.

**`Invalid format: please enter valid TOML` en Streamlit Secrets**
Pegaste formato `.env` (`KEY=value`). Streamlit quiere TOML (`KEY = "value"`). Para los JSONs grandes usa `'''...'''` (literal multi-línea) — no `"""..."""`, que reinterpreta los `\n`.

**HubSpot devuelve propiedades vacías en el dashboard**
Probablemente algún internal name de `FAKEDOOR_PROPS` no matchea el real. Abre el expander "Debug · propiedades vacías" en la sección HubSpot — propiedades al 100% NaN son las que tienen otro nombre. Compara con HubSpot → Settings → Properties → Deal.

**El funnel no tiene la etapa "Entregados WA" exacta**
Es una estimación al 77% sobre Enviados WA (constante histórica de Infobip). Para hacerla live habría que integrar la API de Infobip y leer delivery_status por mensaje.

**El score API devuelve 401**
`SCORE_API_TOKEN` no es válido o expiró. Confirma con el equipo de Riesgo de Habi Capital.

---

## Cron / refresh

No hay cron — cada usuario que abre el dashboard dispara el pull. Caching de Streamlit:
- Sheets: TTL 120s
- HubSpot: TTL 120s
- BigQuery: TTL 300s
- Score API: invocado solo para cédulas sin cache en el Sheet (no se cachea en memoria de Streamlit; el Sheet es la verdad).

Para invalidar el cache, refrescar la página del navegador o esperar el TTL.

---

## Roadmap visible

- [ ] Wire `SCORE_API_URL` + `SCORE_API_TOKEN` en producción (Streamlit Secrets) para que los 12 leads pendientes se autocompleten.
- [ ] Integración Infobip para que `Entregados WA` sea live, no constante × 0.77.
- [ ] Generalizar `hubspot.FAKEDOOR_PROPS` a un esquema por experimento.
- [ ] Test estadístico A/B integrado (z-test de proporciones, igual al original).
- [ ] Distribución de scores como histograma con línea vertical en 720.
