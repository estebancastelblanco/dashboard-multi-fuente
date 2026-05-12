# dashboard-multi-fuente

Dashboard sencillo en Streamlit que consume datos **en vivo** de:

- **BigQuery** — top 10 de `papyrus-data-mx.habi_wh_hesh.production_hesh`
- **HubSpot** — ultimos 10 deals
- **Google Sheets** — hoja `Leads`

Cada tabla muestra maximo 10 filas y 4 columnas.

## Correr localmente

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # luego edita .env con tus valores
streamlit run streamlit_app.py
```

El `.env` esta en `.gitignore` — nunca se commitea.

## Deploy en Streamlit Community Cloud

1. https://share.streamlit.io/ → Deploy an app
2. Repository: `estebancastelblanco/dashboard-multi-fuente`
3. Branch: `main` · Main file: `streamlit_app.py`
4. **Advanced settings → Secrets**: pega un TOML con las mismas variables del `.env`
   (ver formato exacto en el commit message inicial o en el chat de Claude que lo creo).

El bridge `_bootstrap_from_st_secrets()` en `streamlit_app.py` copia `st.secrets`
a variables de entorno para que el codigo no distinga entre local y cloud.

## Estructura

```
.
├── streamlit_app.py        # Entrypoint
├── src/sources/
│   ├── bigquery.py
│   ├── hubspot.py
│   └── gsheets.py
├── .env.example
└── requirements.txt
```
