# Dashboard Habi Capital · Multi-experimento

App de Streamlit que aloja **dashboards en tiempo real** para los experimentos de Habi Capital. Una sola base de credenciales (BigQuery + HubSpot + Google Sheets) alimenta N dashboards. El primero (y referencia) es el Fake Door del Crédito de Libre Inversión con Garantía Hipotecaria.

> Deploy: https://share.streamlit.io/ apuntando a `estebancastelblanco/dashboard-multi-fuente`.
> Sheet de leads en vivo: https://docs.google.com/spreadsheets/d/1vN7wL8a_NvfLks2IvoIICgbV2GrIDKhPbymS3aLft2I/edit

---

## Cómo trabajo · principios del dashboard

Estos son los principios que rigen TODO el código y la UX del tablero. Si algo nuevo se mete y rompe estos principios, hay que revisarlo antes de mergear.

1. **Datos en vivo, no exports**. Nada de descargar CSV/XLSX a mano. Cada fuente se consume por API o por gspread; el tablero abre y ya está cargado.
2. **El Sheet es la fuente de verdad de los leads**. Lo que el usuario escribe ahí (manualmente o vía el frontend del fakedoor) manda. El dashboard solo lee — nunca consulta el motor de riesgo en línea ni reescribe leads.
3. **Persistencia de scoring en columnas `Aplica` + `Metadata`**. Cada lead que firma T&C dispara una llamada al credit application desde el frontend; el resultado se persiste como JSON en `Metadata` y un literal `si/no/error/pending` en `Aplica`. El dashboard parsea esas dos columnas; nunca re-consulta.
4. **Refresco manual con botón, no polling**. El tablero cachea HubSpot y BigQuery 24h y Sheets 2 min. Hay un botón "Actualizar datos" en el sidebar que limpia el cache y vuelve a pedir todo. No quiero re-pulls automáticos que generen costos innecesarios.
5. **Sin emojis ni captions explicativos en la UI**. El tablero es para tomar decisiones, no para enseñar a usar Streamlit. Las captions tipo "Filtros aplican a…" o "Cruces: HubSpot ↔ Sheets" sobran.
6. **Top-10 en distribuciones**. Las distribuciones de Oportunidad y Estado del Negocio se ordenan por frecuencia y se cortan en 10. Más allá es ruido.
7. **Solo AH y BH como variantes**. A y B son residuales de un test viejo; el A/B real del experimento es AH (84 meses) vs BH (120 meses). Cualquier pie/barra de variantes excluye A y B.
8. **Pipeline coloreado por estado de la llamada, no por score**. El que aplica y está sin contactar es la call list activa (verde claro). El que aplica y ya tiene entrevista es verde oscuro. Los demás colores describen el estado, no el riesgo.
9. **Insights solo sobre elegibles**. Las entrevistas cualitativas pesan solo si quien las da puede tomar el producto. Las respuestas de leads `Aplica=no` no aportan a la decisión.

---

## Arquitectura

```
.
├── streamlit_app.py              # Home — biblioteca de experimentos (cards)
├── pages/
│   ├── 1_FakeDoor_Habicapital.py # Dashboard FakeDoor
│   └── 2_Demo_multi_fuente.py    # Dashboard demo (3 tablas live, 10×4)
├── src/
│   ├── experiments.py            # Registry de experimentos
│   ├── styling.py                # Paleta morada + CSS
│   └── sources/
│       ├── bigquery.py           # BQ client + landing events
│       ├── hubspot.py            # Deals + compute_fuente + property labels
│       ├── gsheets.py            # fetch_tab + ensure_columns + update_rows_by_key
│       └── risk_score.py         # Parser de Aplica + Metadata del Sheet
├── experiments/
│   └── fakedoor_habicapital/     # Docs adjuntos al experimento
├── .streamlit/secrets.toml       # Credenciales para Streamlit Cloud (TOML, gitignored)
└── requirements.txt
```

---

## Stack

- **Streamlit** (UI y caching con `persist="disk"`)
- **Plotly** (funnel horizontal con escala log, pies y barras horizontales)
- **gspread** + `google-auth` (Sheets lectura/escritura, scope `spreadsheets` + `drive`)
- **google-cloud-bigquery** (queries a `sellers-main-prod.javascript9.{pages,tracks}`)
- **requests** (HubSpot CRM v3)
- **python-dotenv** (carga `.env` local; en cloud usa `st.secrets`)

---

## Fuentes de datos

| Fuente | Qué aporta | Cómo se accede |
|---|---|---|
| **HubSpot** · Deals | Universo (flag_fakedoor), propiedades custom (ab_test_landing, estado, oportunidad_del_negocio, nombre_del_conjunto, comite_remodelaciones) | API v3 `/crm/v3/objects/deals/search` con filtro `flag_fakedoor HAS_PROPERTY`. Pagina hasta agotar. |
| **HubSpot** · Property catalog | Mapeo de valores internos → labels para enums | `/crm/v3/properties/deals/{name}` (cacheado 24h). |
| **Google Sheets** · `Leads` | Leads que firmaron T&C, columnas persistidas `Aplica` + `Metadata` | gspread con service account `ctl-reader-service@try12-455405.iam.gserviceaccount.com` con permiso **Editor**. |
| **Google Sheets** · `Entrevista` | Entrevistas cualitativas D+1 (P1, P5, P8, P9, tiene hipoteca?) | gspread (mismo client). |
| **Google Sheets** · `Infobip` | Teléfonos a los que se envió WhatsApp (constante de delivery se aproxima en 77%) | gspread; tab con headers vacíos cae a `get_all_values()`. |
| **BigQuery** · Segment events | Eventos de la landing `habicapitalliquidez.vercel.app` (pages + tracks). Flags por UUID: visited_home, visited_solicitud, visited_consent. | OAuth user creds. |
| **Credit application** · `api.habicapital.com` (PROD) | Score, Cuota Máxima, Ingresos, Razón. **El dashboard NO lo consulta** — lo dispara el [fakedoor frontend](https://github.com/EstebanCastel/habicapitalliquidez) al firmar T&C y persiste en el Sheet. |

---

## Filtros (sidebar)

Cada filtro narrows down `df_hs` y propaga al funnel, distribuciones, pipeline y desglose.

- **Variante** → `ab_test_landing`. Solo se ofrece AH (84m) y BH (120m). A y B no aparecen.
- **Fuente** → calculada con `hubspot.compute_fuente`:
  - `"Top"` si `flag_fakedoor == "Top"`
  - `"Rechazos Remo"` si `comite_remodelaciones` ∈ {4 valores fijos}
  - `"Rechazos Comite"` si `oportunidad_del_negocio == "Descartado por comité"`
  - `"MM + Inmo"` por defecto
- **Oportunidad del Negocio** → `oportunidad_del_negocio` (decoded a label).
- **Estado del Negocio** → `estado` (custom de Habi, NO el `dealstage` estándar).

Defaults: todos seleccionados. El universo entero queda intacto a menos que el usuario deseleccione algo explícitamente.

Botón **Actualizar datos** en el tope del sidebar limpia el cache y rerun.

---

## Secciones del dashboard

### 1. KPIs (6 cards)

Universo HS · Leads T&C · Contactados · Interés activo · Elegibles · Por llamar.

### 2. Embudo del experimento (7 etapas, live, sin filtro de fecha)

| # | Etapa | Cómo se computa |
|---|---|---|
| 1 | Universo (flag fakedoor) | `len(df_hs)` con `flag_fakedoor HAS_PROPERTY` |
| 2 | Con nombre del conjunto | `df_hs` donde `nombre_del_conjunto` ≠ vacío |
| 3 | Enviados WA | `77% × Con conjunto` (constante histórica de Infobip) |
| 4 | Abrieron página | `BQ pages ∩ HS allowed_uuids` |
| 5 | T&C firmados | `Sheets/Leads ∩ HS` |
| 6 | Elegibles | `Aplica = si` |
| 7 | Aplican (sin hipoteca) | Elegibles − `tiene hipoteca? = si` |

Barras horizontales con escala log si el rango es ancho. Hover muestra %vs etapa previa y fuente del dato.

### 3. Distribución de leads (top-10 + 2 pies)

- **Barra horizontal · Top-10 Oportunidad del Negocio** (paleta PALE→PRIMARY).
- **Barra horizontal · Top-10 Estado del Negocio** (paleta PALE→MED).
- **Pie · Fuente** (4 categorías).
- **Pie · Variante A/B** (solo AH y BH).

### 4. Funnel de usabilidad de la landing (4 etapas, BigQuery)

Enviados WA → Abrió primer link (`/<uuid>`) → Llegó a `/solicitud` → Firmó T&C.

Si una etapa supera el 100% de la previa (típico cuando el flujo viejo usaba `/<uuid>` y el nuevo `/solicitud`), no se muestra el %.

### 5. Pipeline de leads (la vista clave)

Tabla coloreada por estado de la llamada. Ordenada por **prioridad**: primero los que aplican y NO han sido contactados.

| Color | Estado | Significado |
|---|---|---|
| Verde oscuro | `aplica_contactado` | Aplica + ya tiene entrevista |
| Verde claro | `aplica_pendiente_llamar` | Aplica + sin contactar (call list activa) |
| Amarillo | `pendiente_score` | Engine no respondió todavía |
| Gris | `no_aplica` | Score <720 o con hipoteca |
| Rojo | `error` | Engine devolvió error |

Columnas: Nombre, Teléfono, Cédula, Grupo, Fuente, Contesto?, Hipoteca?, Score, Nivel, Aplica, **Cuota Máxima**, **Ingresos**, **Razón**.

`contactado` = el teléfono aparece en la pestaña Entrevista (NO la columna `contesto?`).

### 6. Desglose (consolidación HS + Sheets + BQ + Entrevista)

Tabla unificada con base en `df_hs_f`, joineada con Leads (por uuid), Entrevista (por teléfono normalizado) y BigQuery (por uuid). 26 columnas: identificación, fuente/variante, eventos BQ, estado de scoring, hipoteca. Se titula `Desglose (N)`.

### 7. Insights · entrevistas cualitativas

Solo sobre elegibles (`aplica == "si"`). Muestra:
- Línea de resumen: `N elegibles totales · N ya con entrevista · N aún sin contactar`.
- Tabla **Elegibles aún sin contactar (N)** con Nombre, Teléfono, Cédula, Grupo, Score (entero), Nivel, Cuota Máxima, Ingresos, Razón. Esta es la call list activa.
- Pie **¿Tiene hipoteca?** sobre los entrevistados elegibles.
- Expanders por pregunta (P1 trigger, P5 plazo, P8 objeciones, P9 urgencia) con todas las respuestas listadas.

#### Snapshot actual de elegibles (9)

Los 9 elegibles del experimento, con scores reales del motor de riesgo de Habi Capital (sin decimales, threshold ≥ 720, salvo casos manuales aprobados por income_validation_pending con score limítrofe):

| Score | Notas |
|---|---|
| 909 | Elegible · alto |
| 872 | Elegible · alto |
| 846 | Elegible (manual: income_validation_pending) |
| 826 | Elegible |
| 803 | Elegible |
| 776 | Elegible |
| 776 | Elegible (manual) |
| 724 | Elegible · limítrofe |
| 712 | Elegible (manual: engine flag, score limítrofe) |

Lead adicional con score 500: **NO elegible** (queda en `Aplica=no`, fila gris en el pipeline).

Los **contactados** ya tienen entrevista cualitativa con respuestas a P1/P5/P8/P9 y campo `tiene hipoteca?` lleno, agregadas en los expanders del bloque Insights. Los **sin contactar** salen en la tabla "Elegibles aún sin contactar" como call list activa para llamar D+1.

### 8. Decisión · GO / ITERATE / KILL

Sección final del dashboard que aplica los criterios del documento de diseño del experimento sobre los datos en vivo. Calcula dos decisiones en orden estricto:

#### 8.1 Tracción agregada (pooled AH + BH)

```
% interés activo = elegibles / leads_con_entrevista_cualitativa
```

| % interés activo | Decisión |
|---|---|
| ≥ 40% | **GO** — demanda real sólida, construir el producto |
| 20–40% | **ITERATE** — señal ambigua, iterar copy o extender el experimento |
| < 20% | **KILL** — sin demanda, no construir |

#### 8.2 Elasticidad de plazo (AH vs BH)

Condicional a GO. Se compara la conversión a T&C (o el interés activo en la entrevista) entre AH y BH:

```
diff_pp = abs(conv_BH - conv_AH)
```

| Diferencia | Plazo recomendado |
|---|---|
| BH − AH ≥ 20 pp | Construir con **BH (120 meses)** |
| AH − BH ≥ 20 pp | Construir con **AH (84 meses)** |
| diff < 20 pp | Default **AH (84 meses)** por menor exposición de riesgo |

Adicionalmente, si la diferencia BH vs AH es estadísticamente significativa por z-test de proporciones (`p < 0.05`) aunque sea < 20 pp, se considera direccional y se reporta para discusión.

#### 8.3 Matriz de lectura

| Observación | Diagnóstico / Acción |
|---|---|
| Tracción ≥ 40% + elegibilidad ≥ 30% | Demanda sólida · **GO** · decidir plazo |
| Tracción alta · BH > AH por ≥ 20 pp | **GO con 120m** |
| Tracción alta · AH > BH por ≥ 20 pp | **GO con 84m** |
| Tracción alta · AH ≈ BH (diff < 10 pp) | **GO con 84m** por menor exposición |
| Tracción 20–40% | **ITERATE** · iterar copy o extender |
| Tracción < 20% | **KILL** o pivot a producto alterno |
| Tracción alta solo en 1–2 segmentos | **GO selectivo** sobre segmentos núcleo |

Esta sección lee directo de los df ya filtrados; los umbrales (40%, 20pp, 720) están en constantes al tope del archivo para que se puedan ajustar sin tocar la lógica.

---

## Persistencia de scores · cómo funciona

El problema: cada vez que carga el dashboard no queremos pedir score a 40+ cédulas (costo, latencia, rate limit). El Sheet es el cache.

Columnas auto-creadas por `gsheets.ensure_columns()`:

| Columna | Contenido |
|---|---|
| `Aplica` | `si` / `no` / `pending` / `error` — derivado del score (≥720 = sí) |
| `Metadata` | JSON con respuesta cruda del API: Score, Nivel Riesgo, Ingresos Mensuales, Fuente Ingresos, Cuota Máxima, Razón, Estado |

### Flow

```
[fakedoor frontend] usuario firma T&C
  ↓
[/api/sheets/form] appendea la fila a Leads
  ↓ after()
[score-lead.ts] createCreditApp + confirmTyc + pollUntilTerminal (4 min)
  ↓
[score-lead.ts] writeback al Sheet (Aplica + Metadata)

[dashboard] solo lee Aplica + Metadata del Sheet
```

### Override pending → si/no por score

Si el engine devuelve `aplica=pending` (típicamente `income_validation_pending`) pero ya hay score, ambos extremos (Python `risk_score._parse_metadata` y TypeScript `credit-app.ts::parseScoreOutcome`) deciden por umbral 720. Esto evita que un lead con score 826 quede amarillo en el pipeline por una bandera del engine.

---

## Cómo correr local

```bash
git clone git@github.com:estebancastelblanco/dashboard-multi-fuente.git
cd dashboard-multi-fuente
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# editar .env con valores reales

streamlit run streamlit_app.py
```

Abre `http://localhost:8501`.

---

## Credenciales · dos formatos

| Donde | Formato | Archivo |
|---|---|---|
| Local | `KEY=value` (dotenv) | `.env` (gitignored) |
| Streamlit Cloud | TOML | Settings → Secrets |

El código de cada `pages/*.py` hace un **bridge** `st.secrets → os.environ` al inicio, así los conectores leen siempre de env vars sin importar el entorno.

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
```

> El dashboard no necesita credenciales del credit application API. El scoring se dispara desde el fakedoor frontend al firmar T&C.

### Formato TOML (para Streamlit Cloud)

Pegar el contenido de `.streamlit/secrets.toml` exactamente como está — los JSONs grandes van entre `'''...'''` (literal multi-línea) para que las secuencias `\n` del private_key se conserven sin que TOML las interprete.

---

## Cache · TTLs

- Sheets (Leads, Entrevista, Infobip): **120s**
- HubSpot deals: **24h** + `persist="disk"`
- HubSpot property labels: **24h** + `persist="disk"`
- BigQuery landing events: **24h** + `persist="disk"`

Botón "Actualizar datos" en el sidebar limpia el cache completo y rerun.

---

## Cómo agregar un experimento nuevo

1. Crear entrada en `src/experiments.py` con `slug`, `title`, `start_date`, `design_doc_url`, `results_doc_url`, `page` y `attachments`.
2. Crear `pages/<N>_<Nombre>.py` copiando el patrón de `pages/1_FakeDoor_Habicapital.py`:
   - `_bootstrap_from_st_secrets()` al inicio
   - `inject_base_css()`
   - Loaders cacheados (`@st.cache_data(ttl=DAY, persist="disk")`)
   - Sidebar con filtros + botón "Actualizar datos"
   - Secciones: KPIs → funnel → distribuciones → pipeline → desglose → insights → decisión
3. (Opcional) adjuntos no-PII en `experiments/<slug>/`.
4. Si necesita propiedades nuevas de HubSpot, agregar a `src/sources/hubspot.py::FAKEDOOR_PROPS`.

---

## Permisos del service account

`ctl-reader-service@try12-455405.iam.gserviceaccount.com` debe tener acceso **Editor** en el Sheet (no Viewer). Si no, `ensure_columns()` y `update_rows_by_key()` fallan con 403.

---

## Troubleshooting

**`GSpreadException: get_all_records ... headers`**
La pestaña tiene headers vacíos o duplicados. `gsheets.fetch_tab` cae a `get_all_values()` y deduplica automáticamente.

**`ModuleNotFoundError: plotly`**
Falta en requirements. Verifica `plotly>=5.20`.

**`Invalid format: please enter valid TOML`**
Pegaste `.env`. Streamlit quiere TOML (`KEY = "value"`). Para JSONs grandes usa `'''...'''` literal, NO `"""..."""`.

**HubSpot devuelve propiedades vacías**
Algún internal name de `FAKEDOOR_PROPS` no matchea el real. Abre el expander "Debug · propiedades vacías" — propiedades al 100% NaN tienen otro nombre. Compara con HubSpot → Settings → Properties → Deal.

**Lead con `Aplica` vacío**
El frontend no completó el scoring (engine timeout, error de auth o lead nuevo). Revisa logs de Vercel en `EstebanCastel/habicapitalliquidez`. Como fallback, rellenar Aplica + Metadata manualmente en el Sheet.

**Lead amarillo (pendiente_score) con score lleno**
El engine devolvió `income_validation_pending`. Tanto Python como TypeScript hacen override por score ≥ 720. Si sigue amarillo tras un refresh, revisa que `Metadata` tenga el campo `score` parseable como int.

---

## Roadmap visible

- [ ] Integración Infobip live para `Enviados WA` (hoy es 77% × con conjunto).
- [ ] Generalizar `hubspot.FAKEDOOR_PROPS` a esquema por experimento.
- [ ] Z-test de proporciones AH vs BH integrado en la sección Decisión.
- [ ] Histograma de scores con línea vertical en 720.
- [ ] Segmentación de Decisión por fuente (Top, Comité/Remo, MM, MM+Inmo) para soportar GO selectivo.
