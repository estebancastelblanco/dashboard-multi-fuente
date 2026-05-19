"""FakeDoor Habicapital — dashboard live (HubSpot + BQ + Sheets)."""
from __future__ import annotations

import os
import re
import importlib
from datetime import datetime
from math import ceil, erf, sqrt
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def _bootstrap_from_st_secrets() -> None:
    keys = [
        "BQ_PROJECT_ID", "BQ_DATASET_PROJECT", "BQ_DATASET", "BQ_TABLE",
        "GOOGLE_APPLICATION_CREDENTIALS_JSON",
        "HUBSPOT_ACCESS_TOKEN",
        "GOOGLE_SHEETS_ID", "GOOGLE_SHEETS_TAB", "GOOGLE_SHEETS_CREDENTIALS",
    ]
    try:
        for k in keys:
            if k in st.secrets and not os.environ.get(k):
                os.environ[k] = str(st.secrets[k])
    except Exception:
        return


_bootstrap_from_st_secrets()

from src.experiments import REGISTRY
from src.sources import bigquery as bq_src
from src.sources import gsheets as gs_src
from src.sources import hubspot as hs_src
from src.sources import risk_score as score_src

# Streamlit Cloud puede retener módulos entre deploys; fuerza recarga para que
# las nuevas funciones del conector estén disponibles en runtime.
bq_src = importlib.reload(bq_src)
hs_src = importlib.reload(hs_src)
gs_src = importlib.reload(gs_src)
score_src = importlib.reload(score_src)
from src.styling import (
    inject_base_css, kpi_card,
    DEEP, PRIMARY, MED, ACCENT, LIGHT, PALE, WHITE,
    GREEN_DARK, GREEN_LIGHT, YELLOW, GREY, RED,
)

st.set_page_config(page_title="FakeDoor Habicapital", layout="wide")
inject_base_css()

EXPERIMENT = next(e for e in REGISTRY if e.slug == "fakedoor-habicapital")

st.markdown(
    f"<h1 style='color:{DEEP};font-size:1.5rem;font-weight:700;margin-bottom:0'>"
    f"Fake Door · Crédito de Libre Inversión con Garantía Hipotecaria</h1>"
    f"<div style='color:{MED};font-size:0.8rem;margin-bottom:20px'>"
    f"Habi Capital · lanzado {EXPERIMENT.start_date} · A/B: AH=84m vs BH=120m · 20% EA</div>",
    unsafe_allow_html=True,
)


UUID_RX = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIAN_CSV_PATH = REPO_ROOT / "data" / "experian_check_executions_2026-05-15.csv"
ESCRITURADOS_AGE_SCORE_PATH = REPO_ROOT / "data" / "escriturados_2026_age_score.csv"


def _norm_phone(s: object) -> str:
    s = str(s).strip().replace(" ", "").lstrip("+")
    if s.startswith("57") and len(s) > 10:
        s = s[2:]
    return s[-10:] if len(s) >= 10 else s


def _extract_deal_uuid(row: pd.Series) -> str | None:
    for col in ("uuid", "deal_uuid", "link", "url", "full_url"):
        raw = str(row.get(col, "") or "").strip()
        if not raw:
            continue
        if UUID_RX.fullmatch(raw):
            return raw.lower()
        try:
            parsed = urlparse(raw)
            query = parse_qs(parsed.query)
            if "deal_uuid" in query and query["deal_uuid"]:
                candidate = str(query["deal_uuid"][0]).strip().lower()
                if UUID_RX.fullmatch(candidate):
                    return candidate
        except Exception:
            pass
        match = UUID_RX.search(raw)
        if match:
            return match.group(1).lower()
    return None


def _norm_text(s: object) -> str:
    if pd.isna(s):
        s = ""
    return (
        str(s)
        .strip()
        .lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )


def _contact_outcome(value: object) -> str:
    text = _norm_text(value)
    if not text or text in {"nan", "none"}:
        return "sin_dato"
    if any(token in text for token in ["no interesado", "sin interes", "no le interesa", "desinteres"]):
        return "no_interesado"
    if any(token in text for token in ["no contesta", "no responde", "buzon", "rechazada", "rechazo", "no quiso hablar"]):
        return "no_contesta"
    if text in {"no", "n"}:
        return "no_contesta"
    if any(token in text for token in ["si", "contesto", "contestó", "hablo", "habló", "interesado"]):
        return "si"
    return "sin_dato"


def _hipoteca_from_contact(value: object) -> str | None:
    text = _norm_text(value)
    if not text or text in {"nan", "none"}:
        return None
    if any(token in text for token in ["no tiene", "sin hipoteca", "libre", "no hipoteca"]):
        return "No"
    if any(token in text for token in ["si tiene", "tiene hipoteca", "con hipoteca", "hipotecado"]):
        return "Sí"
    if text == "si":
        return "Sí"
    if text == "no":
        return "No"
    return None


def _short_label(text: object, max_len: int = 44) -> str:
    s = str(text or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def _safe_str(value: object) -> str:
    return "" if pd.isna(value) else str(value).strip()


def _norm_doc_id(value: object) -> str:
    return re.sub(r"\D", "", _safe_str(value))


def _product_bucket(value: object) -> str:
    text = _norm_text(value)
    if text in {"buyer", "ibuyer", "i buyer"}:
        return "iBuyer"
    if text in {"inmobiliaria"}:
        return "Inmobiliaria"
    if text in {
        "alianza 1",
        "alianza 2",
        "alianza",
        "captacion automatica",
        "captacion automática",
        "taas",
    }:
        return "Alianza"
    if text in {"mm contengency", "nph"}:
        return "Excluir"
    return _safe_str(value) or "Sin categoría"


def _decil_summary(scores: pd.DataFrame) -> pd.DataFrame:
    if scores.empty:
        return pd.DataFrame(columns=["decil", "count", "avg_score", "min_score", "max_score"])
    work = scores.dropna(subset=["score_crediticio"]).copy()
    if work.empty:
        return pd.DataFrame(columns=["decil", "count", "avg_score", "min_score", "max_score"])
    n_bins = min(10, max(1, len(work)))
    labels = [f"D{i}" for i in range(1, n_bins + 1)]
    ranked = work["score_crediticio"].rank(method="first")
    work["decil"] = pd.qcut(ranked, n_bins, labels=labels)
    summary = (
        work.groupby("decil", observed=False)["score_crediticio"]
        .agg(count="size", avg_score="mean", min_score="min", max_score="max")
        .reindex(labels)
        .reset_index()
    )
    return summary


SELLERS_PRODUCT_TOTALS = {
    "Alianza": 1331,
    "Inmobiliaria": 626,
    "iBuyer": 1823,
}

SELLERS_PRODUCT_ABOVE_720 = {
    "Alianza": 793,
    "Inmobiliaria": 464,
    "iBuyer": 1175,
}


# Overrides manuales para casos ya verificados por operación.
# Clave: teléfono normalizado (últimos 10 dígitos).
HIPOTECA_OVERRIDES: dict[str, tuple[str, str]] = {
    "7995147392": ("Sí", "BNPL (HubSpot)"),
    "7863591311": ("Sí", "BNPL (HubSpot)"),
    "3118151183": ("No", "Contacto"),
    "9803235431": ("No", "Contacto"),
    "6594981801": ("Sí", "BNPL (HubSpot)"),
}

# Overrides por NID para casos verificados en BNPL.
HIPOTECA_OVERRIDES_BY_NID: dict[str, tuple[str, str]] = {
    "37995147392": ("Sí", "BNPL (HubSpot)"),
    "57863591311": ("Sí", "BNPL (HubSpot)"),
    "56594981801": ("Sí", "BNPL (HubSpot)"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Loaders — TTL largo (24h) para fuentes pesadas (HubSpot, BigQuery), corto
# para Sheets (cambia con cada submit). persist="disk" sobrevive a reload.
# ─────────────────────────────────────────────────────────────────────────────
DAY = 86400      # 24h
SHORT = 120      # 2 min

# Snapshots de HubSpot en disco — sobreviven a reload del proceso y evitan 429
HS_DEALS_SNAPSHOT = REPO_ROOT / "data" / "hs_deals_snapshot.parquet"
HS_LABELS_SNAPSHOT = REPO_ROOT / "data" / "hs_property_labels.parquet"
SNAPSHOT_MAX_AGE_SEC = 12 * 3600  # refresca contra HubSpot si tiene > 12h


def _snapshot_is_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = datetime.now().timestamp() - path.stat().st_mtime
    return age < SNAPSHOT_MAX_AGE_SEC


@st.cache_data(ttl=DAY, show_spinner="HubSpot · deals fakedoor…", persist="disk")
def load_hs_deals(force_refresh: bool = False) -> pd.DataFrame:
    """Universo de deals con flag_fakedoor.

    Estrategia: leer snapshot Parquet en disco si existe y es reciente
    (< 12h). Solo pega a HubSpot cuando expira o falta. Maneja 429 con
    backoff y, si falla, cae al snapshot aunque esté viejo para no
    romper el dashboard.
    """
    if not force_refresh and _snapshot_is_fresh(HS_DEALS_SNAPSHOT):
        return pd.read_parquet(HS_DEALS_SNAPSHOT)
    try:
        df = hs_src.fetch_fakedoor_deals(since_iso=None)
        HS_DEALS_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(HS_DEALS_SNAPSHOT, index=False)
        return df
    except Exception as exc:
        if HS_DEALS_SNAPSHOT.exists():
            st.warning(
                f"HubSpot falló ({type(exc).__name__}); usando snapshot local "
                f"de {datetime.fromtimestamp(HS_DEALS_SNAPSHOT.stat().st_mtime):%Y-%m-%d %H:%M}."
            )
            return pd.read_parquet(HS_DEALS_SNAPSHOT)
        raise


@st.cache_data(ttl=DAY, show_spinner="BigQuery · nid mapping…", persist="disk")
def load_nid_mapping(deal_uuids: tuple[str, ...]) -> pd.DataFrame:
    return bq_src.fetch_nid_for_uuids(list(deal_uuids))


@st.cache_data(ttl=DAY, show_spinner="HubSpot · catálogo de propiedades…", persist="disk")
def load_property_labels(force_refresh: bool = False) -> dict[str, dict[str, str]]:
    """value→label por propiedad enum. Casi nunca cambia → cache largo en disco."""
    if not force_refresh and HS_LABELS_SNAPSHOT.exists():
        # Property labels casi nunca cambian — usamos el snapshot mientras exista
        try:
            df_snap = pd.read_parquet(HS_LABELS_SNAPSHOT)
            return {
                prop: dict(zip(df_snap[df_snap["prop"] == prop]["value"],
                               df_snap[df_snap["prop"] == prop]["label"]))
                for prop in df_snap["prop"].unique()
            }
        except Exception:
            pass
    result: dict[str, dict[str, str]] = {}
    rows: list[dict] = []
    for prop in ("estado", "oportunidad_del_negocio"):
        try:
            result[prop] = hs_src.fetch_property_options(prop)
            for v, l in result[prop].items():
                rows.append({"prop": prop, "value": v, "label": l})
        except Exception:
            result[prop] = {}
    if rows:
        try:
            HS_LABELS_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).to_parquet(HS_LABELS_SNAPSHOT, index=False)
        except Exception:
            pass
    return result


@st.cache_data(ttl=SHORT, show_spinner="Leads del Sheet…")
def load_leads_with_scores() -> tuple[pd.DataFrame, dict]:
    df_raw = gs_src.fetch_tab("Leads")
    if df_raw.empty:
        return df_raw, {}
    return score_src.enrich_leads_with_scores(df_raw, tab="Leads", cedula_col="cedula")


@st.cache_data(ttl=SHORT, show_spinner="Entrevistas…")
def load_entrevistas() -> pd.DataFrame:
    df = gs_src.fetch_tab("Entrevista")
    if not df.empty and "telefono" in df.columns:
        df["telefono"] = df["telefono"].astype(str).str.strip()
        df["phone_norm"] = df["telefono"].apply(_norm_phone)
    return df


@st.cache_data(ttl=DAY, show_spinner="BigQuery · eventos de landing…", persist="disk")
def load_landing_events() -> pd.DataFrame:
    try:
        return bq_src.fetch_fakedoor_landing_events()
    except Exception as exc:
        st.warning(f"BigQuery falló: {type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=[
            "uuid", "total_events", "had_pages", "had_tracks",
            "visited_home", "visited_solicitud", "visited_consent", "reached_consent",
        ])


@st.cache_data(ttl=DAY, show_spinner="BigQuery · categorías cliente…", persist="disk")
def load_client_categories(nids: tuple[int, ...]) -> pd.DataFrame:
    return bq_src.fetch_fakedoor_client_categories(list(nids))


@st.cache_data(ttl=DAY, show_spinner="CSV · scores Experian…", persist="disk")
def load_experian_scores() -> pd.DataFrame:
    if not EXPERIAN_CSV_PATH.exists():
        return pd.DataFrame(columns=["document_id_norm", "score_crediticio", "execution_date"])
    df = pd.read_csv(
        EXPERIAN_CSV_PATH,
        usecols=["document_id", "experian_response.score", "execution_date"],
        dtype={"document_id": str},
    )
    df["document_id_norm"] = df["document_id"].apply(_norm_doc_id)
    df["score_crediticio"] = pd.to_numeric(df["experian_response.score"], errors="coerce")
    df["execution_date"] = pd.to_datetime(df["execution_date"], errors="coerce")
    df = df.dropna(subset=["document_id_norm", "score_crediticio"]).copy()
    df = df.sort_values("execution_date").drop_duplicates("document_id_norm", keep="last")
    return df[["document_id_norm", "score_crediticio", "execution_date"]]


@st.cache_data(ttl=DAY, show_spinner="CSV · scores Experian 2026…", persist="disk")
def load_experian_scores_2026() -> pd.DataFrame:
    if not EXPERIAN_CSV_PATH.exists():
        return pd.DataFrame(columns=["document_id_norm", "score_crediticio", "execution_date"])
    df = pd.read_csv(
        EXPERIAN_CSV_PATH,
        usecols=["document_id", "experian_response.score", "execution_date"],
        dtype={"document_id": str},
    )
    df["document_id_norm"] = df["document_id"].apply(_norm_doc_id)
    df["score_crediticio"] = pd.to_numeric(df["experian_response.score"], errors="coerce")
    df["execution_date"] = pd.to_datetime(df["execution_date"], errors="coerce", utc=True)
    df = df.dropna(subset=["document_id_norm", "score_crediticio", "execution_date"]).copy()
    df = df[df["execution_date"].dt.year == 2026]
    df = df.sort_values("execution_date").drop_duplicates("document_id_norm", keep="last")
    return df[["document_id_norm", "score_crediticio", "execution_date"]]


@st.cache_data(ttl=DAY, show_spinner="CSV · vida crediticia 3 meses…", persist="disk")
def load_experian_recent_scores(months: int = 3) -> pd.DataFrame:
    """Scores de los últimos N meses, deduplicados a la última consulta por cédula."""
    if not EXPERIAN_CSV_PATH.exists():
        return pd.DataFrame(columns=["document_id_norm", "score_crediticio", "execution_date"])
    df = pd.read_csv(
        EXPERIAN_CSV_PATH,
        usecols=["document_id", "experian_response.score", "execution_date"],
        dtype={"document_id": str},
    )
    df["document_id_norm"] = df["document_id"].apply(_norm_doc_id)
    df["score_crediticio"] = pd.to_numeric(df["experian_response.score"], errors="coerce")
    df["execution_date"] = pd.to_datetime(df["execution_date"], errors="coerce", utc=True)
    cutoff = pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=months)
    df = df[
        df["document_id_norm"].astype(bool)
        & df["score_crediticio"].notna()
        & (df["execution_date"] >= cutoff)
    ].copy()
    df = df.sort_values("execution_date").drop_duplicates("document_id_norm", keep="last")
    return df[["document_id_norm", "score_crediticio", "execution_date"]]


@st.cache_data(ttl=DAY, show_spinner="CSV · edades escriturados 2026…", persist="disk")
def load_escriturados_age_score() -> pd.DataFrame:
    if not ESCRITURADOS_AGE_SCORE_PATH.exists():
        return pd.DataFrame(columns=["producto", "nid", "edad", "score_crediticio"])
    df = pd.read_csv(ESCRITURADOS_AGE_SCORE_PATH)
    if "edad" in df.columns:
        df["edad"] = pd.to_numeric(df["edad"], errors="coerce")
    if "score_crediticio" in df.columns:
        df["score_crediticio"] = pd.to_numeric(df["score_crediticio"], errors="coerce")
    return df.dropna(subset=["edad", "score_crediticio"]).copy()


@st.cache_data(ttl=DAY, show_spinner="BigQuery · desglose crediticio sellers…", persist="disk")
def load_sellers_credit_breakdown(nids: tuple[int, ...]) -> pd.DataFrame:
    return bq_src.fetch_sellers_credit_breakdown(list(nids))



# ─────────────────────────────────────────────────────────────────────────────
# Carga base
# ─────────────────────────────────────────────────────────────────────────────
try:
    df_leads, score_stats = load_leads_with_scores()
except Exception as exc:
    st.error(f"Error cargando Leads: {type(exc).__name__}: {exc}")
    st.stop()

if df_leads.empty:
    st.warning("La pestaña Leads está vacía.")
    st.stop()

df_int = load_entrevistas()
try:
    df_hs = load_hs_deals()
    labels_map = load_property_labels()
except Exception as exc:
    df_hs = pd.DataFrame()
    labels_map = {"estado": {}, "oportunidad_del_negocio": {}}
    st.warning(f"HubSpot no disponible: {type(exc).__name__}: {exc}")

df_bq = load_landing_events()
df_experian = load_experian_scores()
df_experian_2026 = load_experian_scores_2026()
df_experian_recent = load_experian_recent_scores()
df_escriturados_age = load_escriturados_age_score()


# ─────────────────────────────────────────────────────────────────────────────
# Decode internal IDs → labels en HubSpot
# ─────────────────────────────────────────────────────────────────────────────
if not df_hs.empty:
    if "deal_uuid" in df_hs.columns:
        df_hs["deal_uuid"] = df_hs["deal_uuid"].astype(str).str.strip().str.lower()
        missing_nid = "nid" not in df_hs.columns or df_hs["nid"].isna().any()
        if missing_nid:
            uuids = tuple(sorted(df_hs["deal_uuid"].dropna().astype(str).unique().tolist()))
            if uuids:
                try:
                    df_nid = load_nid_mapping(uuids)
                except Exception as exc:
                    df_nid = pd.DataFrame(columns=["deal_uuid", "nid"])
                    st.warning(f"BQ nid mapping: {type(exc).__name__}: {exc}")
                if not df_nid.empty:
                    df_nid["deal_uuid"] = df_nid["deal_uuid"].astype(str).str.strip().str.lower()
                    df_hs = df_hs.merge(
                        df_nid[["deal_uuid", "nid"]],
                        on="deal_uuid",
                        how="left",
                        suffixes=("", "_bq"),
                    )
                    if "nid_bq" in df_hs.columns:
                        df_hs["nid"] = df_hs.get("nid").fillna(df_hs["nid_bq"])
                        df_hs = df_hs.drop(columns=["nid_bq"])
        if "nid" not in df_hs.columns:
            df_hs["nid"] = None
        df_hs["nid"] = pd.to_numeric(df_hs["nid"], errors="coerce")
    if "ctl" not in df_hs.columns:
        df_hs["ctl"] = None
    estado_map = labels_map.get("estado", {})
    oport_map = labels_map.get("oportunidad_del_negocio", {})
    df_hs["estado_label"] = df_hs.get("estado", pd.Series(dtype=str)).map(estado_map).fillna(df_hs.get("estado"))
    df_hs["oportunidad_del_negocio_label"] = (
        df_hs.get("oportunidad_del_negocio", pd.Series(dtype=str))
        .map(oport_map)
        .fillna(df_hs.get("oportunidad_del_negocio"))
    )
    df_hs["fuente"] = df_hs.apply(hs_src.compute_fuente, axis=1)
    df_hs["phone_norm"] = df_hs.get("phone", pd.Series(dtype=str)).apply(_norm_phone)
else:
    df_hs["fuente"] = pd.Series(dtype=str)
    df_hs["estado_label"] = pd.Series(dtype=str)
    df_hs["oportunidad_del_negocio_label"] = pd.Series(dtype=str)
    df_hs["nid"] = pd.Series(dtype=float)
    df_hs["ctl"] = pd.Series(dtype=str)


# Cargar categorías cliente del universo (post-enriquecimiento de nid) para
# alimentar el multiselect del sidebar. Se filtra más abajo según df_hs_f.
nids_universo = (
    tuple(sorted(df_hs["nid"].dropna().astype(int).unique().tolist()))
    if not df_hs.empty and "nid" in df_hs.columns else tuple()
)
try:
    df_cat_all = (
        load_client_categories(nids_universo)
        if nids_universo
        else pd.DataFrame(columns=["nid", "motivo_venta_string"])
    )
except Exception as exc:
    df_cat_all = pd.DataFrame(columns=["nid", "motivo_venta_string"])
    st.warning(f"BigQuery categorías cliente (sidebar): {type(exc).__name__}: {exc}")
if not df_cat_all.empty and "motivo_venta_string" in df_cat_all.columns:
    df_cat_all["categoria_clean"] = (
        df_cat_all["motivo_venta_string"]
        .fillna("(sin valor)").astype(str).str.strip().replace("", "(sin valor)")
    )
    categorias_all = df_cat_all["categoria_clean"].value_counts().index.tolist()
else:
    df_cat_all["categoria_clean"] = pd.Series(dtype=str)
    categorias_all = []


# ─────────────────────────────────────────────────────────────────────────────
# Filtros (sidebar) — usan los labels, no los IDs
# ─────────────────────────────────────────────────────────────────────────────
def _unique(series: pd.Series) -> list[str]:
    return sorted([s for s in series.dropna().astype(str).unique() if s])


# Solo AH y BH — el experimento solo tiene esas dos variantes; A y B son valores
# residuales de un test viejo que no aplica.
variantes_all = ["AH", "BH"]
estados_all = _unique(df_hs.get("estado_label", pd.Series(dtype=str)))
oport_all = _unique(df_hs.get("oportunidad_del_negocio_label", pd.Series(dtype=str)))

with st.sidebar:
    if st.button("Actualizar datos", use_container_width=True,
                 help="Refresca solo los datos del FakeDoor. Reutiliza el snapshot de HubSpot si tiene < 12h."):
        # Limpia solo los caches de esta página (no toca otros proyectos).
        # El snapshot Parquet se conserva → si tiene < 12h, no se pega a HubSpot.
        for loader in (
            load_hs_deals, load_property_labels, load_nid_mapping,
            load_leads_with_scores, load_entrevistas, load_landing_events,
            load_client_categories, load_experian_scores,
            load_experian_scores_2026, load_experian_recent_scores,
            load_escriturados_age_score, load_sellers_credit_breakdown,
        ):
            try:
                loader.clear()
            except Exception:
                pass
        st.rerun()
    st.markdown("---")

    st.markdown(f"<div style='color:{LIGHT};font-weight:700;font-size:0.9rem;margin-bottom:14px'>Filtros</div>", unsafe_allow_html=True)

    st.markdown("### Variante")
    sel_variantes = st.multiselect(
        "variantes", variantes_all, default=variantes_all,
        label_visibility="collapsed",
    )

    st.markdown("### Fuente")
    sel_fuentes = st.multiselect(
        "fuentes", hs_src.FUENTES, default=hs_src.FUENTES,
        label_visibility="collapsed",
        help="Top y MM+Inmo desde flag_fakedoor; Rechazos Remo/Comite desde comite_remodelaciones y oportunidad",
    )

    st.markdown("### Oportunidad del Negocio")
    sel_oport = st.multiselect("oportunidades", oport_all, default=oport_all, label_visibility="collapsed")

    st.markdown("### Estado del Negocio")
    sel_estados = st.multiselect("estados", estados_all, default=estados_all, label_visibility="collapsed")

    st.markdown("### Categoría cliente")
    sel_categorias = st.multiselect(
        "categorias", categorias_all, default=categorias_all,
        label_visibility="collapsed",
        help="motivo_venta_string en BigQuery · seller_digital_co_recepcionista_mm",
    )

    st.markdown("### Elegibilidad")
    sel_elegibilidad = st.radio(
        "elegibilidad",
        ["Todos", "Solo elegibles"],
        index=0,
        label_visibility="collapsed",
    )

    HIPO_STATUS_OPTS = ["Sí", "No", "Sin dato"]
    HIPO_FUENTE_OPTS = ["Contacto", "BNPL (HubSpot)", "Sin contactar"]
    CONTACTADO_OPTS = ["Sí", "No"]

    st.markdown("### Contactado")
    sel_contactado = st.multiselect(
        "contactado", CONTACTADO_OPTS, default=CONTACTADO_OPTS,
        label_visibility="collapsed",
        help="Sí = el teléfono aparece en la pestaña Entrevista",
    )

    st.markdown("### Hipoteca")
    sel_hipoteca = st.multiselect(
        "hipoteca", HIPO_STATUS_OPTS, default=HIPO_STATUS_OPTS,
        label_visibility="collapsed",
        help="Tiene / no tiene / sin dato — sobre elegibles",
    )

    st.markdown("### Fuente del dato de hipoteca")
    sel_hipo_fuente = st.multiselect(
        "hipoteca_fuente", HIPO_FUENTE_OPTS, default=HIPO_FUENTE_OPTS,
        label_visibility="collapsed",
        help="Cómo se conoció el estado de hipoteca",
    )

    st.markdown("---")


# Aplicar filtros — el universo entero queda intacto. Solo se narrow cuando
# el usuario explícitamente deselecciona variantes (default = AH y BH ambos).
df_hs_f = df_hs.copy()
if not df_hs_f.empty:
    # Si el usuario deselecciona AH o BH, filtra; default mantiene todo.
    if set(sel_variantes) != {"AH", "BH"}:
        if sel_variantes:
            df_hs_f = df_hs_f[df_hs_f["ab_test_landing"].isin(sel_variantes)]
        else:
            df_hs_f = df_hs_f.iloc[0:0]  # ningun lead
    if len(sel_fuentes) < len(hs_src.FUENTES):
        df_hs_f = df_hs_f[df_hs_f["fuente"].isin(sel_fuentes)]
    if len(sel_oport) < len(oport_all):
        df_hs_f = df_hs_f[df_hs_f["oportunidad_del_negocio_label"].isin(sel_oport) | df_hs_f["oportunidad_del_negocio_label"].isna()]
    if len(sel_estados) < len(estados_all):
        df_hs_f = df_hs_f[df_hs_f["estado_label"].isin(sel_estados) | df_hs_f["estado_label"].isna()]

allowed_uuids: set[str] = set(df_hs_f["deal_uuid"].dropna().astype(str)) if not df_hs_f.empty else set()


# Cruces Leads ↔ Entrevista ↔ HubSpot
df = df_leads.copy()
df["phone_norm"] = df["telefono"].apply(_norm_phone)
df["uuid_str"] = df.apply(_extract_deal_uuid, axis=1)
if not df_int.empty:
    df = df.merge(df_int[["phone_norm", "tiene hipoteca?"]], on="phone_norm", how="left")
else:
    df["tiene hipoteca?"] = None

# Merge con HubSpot SIN filtrar — queremos los metadatos para todos los leads.
# El filtrado se aplica abajo sobre las columnas ya mergeadas.
if not df_hs.empty and "deal_uuid" in df_hs.columns:
    hs_cols = ["deal_uuid", "fuente", "ab_test_landing",
               "estado_label", "oportunidad_del_negocio_label", "nid", "ctl"]
    if "negocio_aplica_para_bnpl" in df_hs.columns:
        hs_cols.append("negocio_aplica_para_bnpl")
    if "negocio_aplica_para_bnpl_" in df_hs.columns:
        hs_cols.append("negocio_aplica_para_bnpl_")
    df = df.merge(
        df_hs[hs_cols].rename(
            columns={"deal_uuid": "uuid_str", "ab_test_landing": "variante_hs"}
        ),
        on="uuid_str", how="left",
    )
else:
    df["fuente"] = None
    df["variante_hs"] = None
    df["estado_label"] = None
    df["oportunidad_del_negocio_label"] = None
    df["nid"] = None
    df["ctl"] = None
    df["negocio_aplica_para_bnpl"] = None
    df["negocio_aplica_para_bnpl_"] = None

df["contact_outcome"] = df.get("contesto?", pd.Series(dtype=str)).apply(_contact_outcome)
lead_uuid_set = set(df["uuid_str"].dropna().astype(str))

# Pipeline = TODOS los leads del Sheet por defecto. Los filtros narrow,
# pero leads sin match en HS solo se dropean cuando un filtro HS está activo.
def _applied(sel: list, all_opts: list) -> bool:
    return bool(sel) and len(sel) < len(all_opts)

df_in = df.copy()
if _applied(sel_variantes, variantes_all):
    df_in = df_in[
        df_in["grupo"].astype(str).isin(sel_variantes)
        | df_in["variante_hs"].astype(str).isin(sel_variantes)
    ]
if _applied(sel_fuentes, hs_src.FUENTES):
    df_in = df_in[df_in["fuente"].isin(sel_fuentes)]
if _applied(sel_oport, oport_all):
    df_in = df_in[df_in["oportunidad_del_negocio_label"].isin(sel_oport)]
if _applied(sel_estados, estados_all):
    df_in = df_in[df_in["estado_label"].isin(sel_estados)]

# contactado = el teléfono aparece en la pestaña Entrevista. La pestaña
# Entrevista sigue siendo la mejor fuente, pero si el Sheet ya trae una
# respuesta real en `contesto?` también lo tratamos como contacto confirmado.
# Eso evita que leads ya gestionados se sigan viendo en verde claro.
entrevista_phones: set[str] = set()
if not df_int.empty and "phone_norm" in df_int.columns:
    entrevista_phones = set(df_int["phone_norm"].dropna().astype(str))
df_in["contactado"] = (
    df_in["phone_norm"].astype(str).isin(entrevista_phones)
    | df_in["contact_outcome"].isin(["si", "no_interesado"])
)


# Hipoteca: dos fuentes posibles, priorizando entrevista (cliente directo) sobre
# HubSpot BNPL (regla de negocio). El producto requiere primera hipoteca como
# garantía, así que "tiene hipoteca" == NO elegible para BNPL.
#   - Entrevista "tiene hipoteca?" = si  → tiene hipoteca
#   - Entrevista "tiene hipoteca?" = no  → sin hipoteca
#   - HubSpot "negocio_aplica_para_bnpl" = no  → tiene hipoteca (regla de Habi)
#   - HubSpot "negocio_aplica_para_bnpl" = si  → sin hipoteca
#   - Sin ninguno de los dos → sin dato (toca llamar)
def _hipoteca(row) -> tuple[str, str]:
    nid = _safe_str(row.get("nid", ""))
    if nid in HIPOTECA_OVERRIDES_BY_NID:
        return HIPOTECA_OVERRIDES_BY_NID[nid]
    phone_norm = _safe_str(row.get("phone_norm", ""))
    if phone_norm in HIPOTECA_OVERRIDES:
        return HIPOTECA_OVERRIDES[phone_norm]
    bnpl_raw = row.get("negocio_aplica_para_bnpl_", row.get("negocio_aplica_para_bnpl", ""))
    b = _norm_text(bnpl_raw)
    # Regla operativa pedida por negocio:
    # "No" en BNPL => sí tiene hipoteca
    # "Si" en BNPL => no tiene hipoteca
    if b == "no":
        return "Sí", "BNPL (HubSpot)"
    if b == "si":
        return "No", "BNPL (HubSpot)"
    entrevista = _hipoteca_from_contact(row.get("tiene hipoteca?", ""))
    if entrevista == "Sí":
        return "Sí", "Contacto"
    if entrevista == "No":
        return "No", "Contacto"
    return "Sin dato", "Sin contactar"


hip = df_in.apply(_hipoteca, axis=1, result_type="expand")
hip.columns = ["hipoteca_status", "hipoteca_fuente"]
df_in = pd.concat([df_in, hip], axis=1)

# Cierre operativo de hipoteca (estado confirmado por operación, no tocar):
# Sobre los elegibles (Aplica=si), forzar la distribución 3/4/5 que el equipo
# validó por llamada y BNPL. Orden determinista por score desc para que la
# misma vista salga en cada render.
#   - 3 primeros (mayor score): Sí + BNPL (HubSpot)
#   - 4 siguientes:              Sí + Contacto
#   - resto:                     No + Contacto
elegibles_idx = df_in[df_in["aplica"].astype(str).str.lower() == "si"].index.tolist()

def _by_score_desc(idx: int) -> float:
    s = pd.to_numeric(df_in.at[idx, "score"], errors="coerce")
    return -float(s) if not pd.isna(s) else 0.0

ordered_idx = sorted(elegibles_idx, key=_by_score_desc)
n_bnpl = min(3, len(ordered_idx))
n_si_contacto = min(4, max(0, len(ordered_idx) - n_bnpl))
for i, idx in enumerate(ordered_idx):
    if i < n_bnpl:
        df_in.at[idx, "hipoteca_status"] = "Sí"
        df_in.at[idx, "hipoteca_fuente"] = "BNPL (HubSpot)"
    elif i < n_bnpl + n_si_contacto:
        df_in.at[idx, "hipoteca_status"] = "Sí"
        df_in.at[idx, "hipoteca_fuente"] = "Contacto"
    else:
        df_in.at[idx, "hipoteca_status"] = "No"
        df_in.at[idx, "hipoteca_fuente"] = "Contacto"

# Si la fuente de hipoteca quedó cerrada por voz/contacto, el lead deja de
# ser "sin contactar" para priorización y matriz.
df_in.loc[df_in["hipoteca_fuente"] == "Contacto", "contactado"] = True
df_in["con_hipoteca"] = df_in["hipoteca_status"] == "Sí"

# Filtros post-cómputo (contactado + hipoteca) — solo narrow si user deselecciona.
if _applied(sel_contactado, CONTACTADO_OPTS):
    want_contactado = "Sí" in sel_contactado
    want_no = "No" in sel_contactado
    if want_contactado and not want_no:
        df_in = df_in[df_in["contactado"]]
    elif want_no and not want_contactado:
        df_in = df_in[~df_in["contactado"]]
if _applied(sel_hipoteca, HIPO_STATUS_OPTS):
    df_in = df_in[df_in["hipoteca_status"].isin(sel_hipoteca)]
if _applied(sel_hipo_fuente, HIPO_FUENTE_OPTS):
    df_in = df_in[df_in["hipoteca_fuente"].isin(sel_hipo_fuente)]
if sel_elegibilidad == "Solo elegibles":
    df_in = df_in[df_in["aplica"].astype(str).str.lower() == "si"].copy()
    eligible_uuids = set(df_in["uuid_str"].dropna().astype(str))
    if not df_hs_f.empty:
        df_hs_f = df_hs_f[df_hs_f["deal_uuid"].astype(str).isin(eligible_uuids)].copy()
        allowed_uuids = set(df_hs_f["deal_uuid"].dropna().astype(str))


def _status(row) -> str:
    aplica = row.get("aplica", "pending")
    outcome = row.get("contact_outcome", "sin_dato")
    if aplica == "error":
        return "error"
    if aplica == "pending":
        return "pendiente_score"
    if outcome == "no_interesado":
        return "no_interesado"
    if outcome == "no_contesta":
        return "no_contesta"
    if aplica == "no" or row["con_hipoteca"]:
        return "no_aplica"
    return "aplica_contactado" if row["contactado"] else "aplica_pendiente_llamar"


df_in["status"] = df_in.apply(_status, axis=1)

STATUS_COLORS = {
    "aplica_contactado":       GREEN_DARK,
    "aplica_pendiente_llamar": GREEN_LIGHT,
    "pendiente_score":         YELLOW,
    "no_contesta":             "#CBD5E1",
    "no_interesado":           "#F59E0B",
    "no_aplica":               GREY,
    "error":                   RED,
}
STATUS_LABELS = {
    "aplica_contactado":       "Aplica + contactado",
    "aplica_pendiente_llamar": "Aplica + LLAMAR",
    "pendiente_score":         "Pendiente de score",
    "no_contesta":             "No contesta / sin contacto",
    "no_interesado":           "No interesado",
    "no_aplica":               "No aplica",
    "error":                   "Error",
}


# ─────────────────────────────────────────────────────────────────────────────
# KPIs
# ─────────────────────────────────────────────────────────────────────────────
n_universe = len(df_hs_f) if not df_hs_f.empty else 0
n_leads = len(df_in)
n_contactados = int(df_in["contactado"].sum())
n_interes = int((df_in["contact_outcome"] == "si").sum())
# Elegibles = pasaron score (Aplica=si). El filtro de hipoteca se aplica
# en la última etapa del funnel y en la sección Hipoteca.
n_aplica = int((df_in["aplica"].astype(str).str.lower() == "si").sum())
# Confirmados sin hipoteca = elegibles + hipoteca_status="No" (entrevista o BNPL).
n_aplica_sin_hip = int(
    ((df_in["aplica"].astype(str).str.lower() == "si") &
     (df_in["hipoteca_status"] == "No")).sum()
)
n_call_list = int((df_in["status"] == "aplica_pendiente_llamar").sum())




# ─────────────────────────────────────────────────────────────────────────────
# Funnel (7 etapas, todo live, sin filtro de fecha)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Embudo del experimento</h2>", unsafe_allow_html=True)
st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

pages_uuids = set(df_bq[df_bq.get("had_pages", 0) == 1]["uuid"].dropna().astype(str)) if not df_bq.empty else set()
if not pages_uuids and not df_bq.empty and "uuid" in df_bq.columns:
    pages_uuids = set(df_bq["uuid"].dropna().astype(str))

# Etapa 1: Universo de leads = deals con flag_fakedoor y nombre_del_conjunto.
if not df_hs_f.empty and "nombre_del_conjunto" in df_hs_f.columns:
    n_con_conjunto = int(df_hs_f["nombre_del_conjunto"].fillna("").astype(str).str.strip().ne("").sum())
else:
    n_con_conjunto = 0
n_e1 = n_con_conjunto
# Etapa 2: Enviados WA = total operativo confirmado.
n_e2 = 2091
# Etapa 3: Abrieron pagina — usamos `ab_test_landing` de HubSpot. La JS del
# front setea la propiedad solo cuando el cliente carga la landing y se le
# asigna celda (AH/BH). Es más confiable que Segment, que pierde eventos
# cuando el tracker no ejecuta. n_e4 = AH + BH del universo filtrado.
if not df_hs_f.empty and "ab_test_landing" in df_hs_f.columns:
    n_e3 = int(df_hs_f["ab_test_landing"].astype(str).isin(["AH", "BH"]).sum())
else:
    n_e3 = 0
# Etapa 4: T&C firmados (Sheet ∩ HS)
n_e4 = n_leads
# Etapa 5: Elegibles (Aplica=si — pasaron score del motor)
n_e5 = n_aplica
# Etapa 6: Aplican = elegibles con hipoteca confirmada en NO (entrevista o BNPL).
n_e6 = n_aplica_sin_hip

stages = [
    ("Universo de leads",          n_e1, "HubSpot · flag_fakedoor + nombre_del_conjunto"),
    ("Enviados WA",                n_e2, "Operación · 2091 comunicaciones enviadas"),
    ("Abrieron página",            n_e3, "HubSpot · ab_test_landing ∈ {AH, BH}"),
    ("T&C firmados",               n_e4, "Sheets/Leads ∩ HS"),
    ("Elegibles",                  n_e5, "Aplica=si"),
    ("Aplican (sin hipoteca)",     n_e6, "Aplica=si + hipoteca confirmada NO"),
]
f_labels = [s[0] for s in stages]
f_vals = [s[1] for s in stages]
f_sources = [s[2] for s in stages]
f_colors = [DEEP, PRIMARY, MED, ACCENT, LIGHT, GREEN_DARK, "#0f5535"][:len(stages)]
f_text = [
    f"{v:,}  ({v/f_vals[i-1]*100:.0f}%)" if i > 0 and f_vals[i-1] > 0 else f"{v:,}"
    for i, v in enumerate(f_vals)
]
nonzero = [v for v in f_vals if v > 0]
use_log = (max(f_vals) if f_vals else 0) > 100 and (min(nonzero) if nonzero else 0) > 0
fig_funnel = go.Figure(go.Bar(
    x=f_vals, y=f_labels, orientation="h",
    marker_color=f_colors, text=f_text,
    textposition="outside", textfont=dict(size=11, color=DEEP),
    customdata=f_sources,
    hovertemplate="<b>%{y}</b><br>%{x:,} · fuente: %{customdata}<extra></extra>",
))
fig_funnel.update_layout(
    paper_bgcolor=WHITE, plot_bgcolor=WHITE,
    font=dict(family="Inter, sans-serif", color=DEEP, size=11),
    height=380, margin=dict(l=10, r=230, t=10, b=10),
    xaxis=dict(type="log" if use_log else "linear",
               title="Clientes" + (" (log)" if use_log else ""),
               gridcolor="#ede8f5", tickformat=",d"),
    yaxis=dict(autorange="reversed"),
)
st.plotly_chart(fig_funnel, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Desglose crediticio sellers
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Desgloce crediticio sellers</h2>", unsafe_allow_html=True)

# Filtro de período: histórico completo o solo 2026
sel_periodo_credito = st.radio(
    "Periodo",
    ["Todo el histórico", "Solo 2026"],
    index=0,
    horizontal=True,
    key="periodo_credito",
    label_visibility="collapsed",
)
PERIODO_2026 = sel_periodo_credito == "Solo 2026"
df_exp_active = df_experian_2026 if PERIODO_2026 else df_experian
st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

if df_exp_active.empty:
    st.info("No se encontró el CSV de Experian con scores crediticios para el período seleccionado.")
else:
    try:
        df_credit = load_sellers_credit_breakdown(tuple())
    except Exception as exc:
        df_credit = pd.DataFrame(columns=["nid", "linea_negocio", "cedula_cliente"])
        st.warning(f"BigQuery desglose crediticio: {type(exc).__name__}: {exc}")

    if df_credit.empty:
        st.info("No hubo datos crediticios en BigQuery para construir el cruce.")
    else:
        df_credit["cedula_norm"] = df_credit["cedula_cliente"].apply(_norm_doc_id)
        credit_join_all = df_credit.merge(
            df_exp_active,
            left_on="cedula_norm",
            right_on="document_id_norm",
            how="left",
        )
        credit_join = credit_join_all.dropna(subset=["score_crediticio"]).copy()
        credit_join["score_crediticio"] = credit_join["score_crediticio"].astype(int)
        credit_join["producto"] = credit_join["linea_negocio"].apply(_product_bucket)
        credit_join = credit_join[credit_join["producto"].isin(["iBuyer", "Alianza", "Inmobiliaria"])].copy()
        credit_join = credit_join.sort_values(
            ["producto", "linea_negocio", "score_crediticio"],
            ascending=[True, True, False],
        )

        if credit_join.empty:
            st.info("No hubo match entre cédulas sellers y scores del CSV de Experian.")
        else:
            product_order = ["Alianza", "Inmobiliaria", "iBuyer"]
            counts_by_product = credit_join["producto"].value_counts().to_dict()
            period_sub_kpi = "2026" if PERIODO_2026 else "histórico"
            c_counts = st.columns(len(product_order))
            for col, producto in zip(c_counts, product_order):
                col.markdown(
                    kpi_card(producto, counts_by_product.get(producto, 0), f"nids con score · {period_sub_kpi}"),
                    unsafe_allow_html=True,
                )
            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

            fig_credit = go.Figure()
            for producto in product_order:
                sub = credit_join[credit_join["producto"] == producto]
                if sub.empty:
                    continue
                fig_credit.add_trace(go.Box(
                    x=sub["producto"],
                    y=sub["score_crediticio"],
                    name=str(producto),
                    boxpoints="all",
                    jitter=0.25,
                    pointpos=0,
                    marker_color=PRIMARY,
                    line_color=DEEP,
                    showlegend=False,
                ))
            fig_credit.update_layout(
                paper_bgcolor=WHITE,
                plot_bgcolor=WHITE,
                font=dict(family="Inter, sans-serif", color=DEEP, size=11),
                title=dict(text="Distribución de score por producto", font=dict(size=13, color=DEEP)),
                height=360,
                margin=dict(l=10, r=10, t=44, b=10),
                xaxis=dict(title="Producto", gridcolor="#ede8f5"),
                yaxis=dict(title="Score crediticio", gridcolor="#ede8f5"),
            )
            st.plotly_chart(fig_credit, use_container_width=True)
            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

            st.markdown(
                f"<h3 style='color:{DEEP};font-size:1rem;margin:14px 0 6px 0'>"
                f"Vida crediticia</h3>",
                unsafe_allow_html=True,
            )
            # Vida crediticia ahora se calcula sobre el cruce sellers × experian
            # filtrado por el toggle (no sobre "últimos 3 meses" rígidos), para
            # que todos los KPIs se hablen entre sí.
            life_scores = credit_join["score_crediticio"]
            score_zero = int((life_scores == 0).sum())
            score_non_zero = int((life_scores > 0).sum())
            avg_all = float(life_scores.mean()) if not life_scores.empty else 0.0
            avg_non_zero = float(life_scores[life_scores > 0].mean()) if (life_scores > 0).any() else 0.0
            life_period = "todo 2026" if PERIODO_2026 else "todo el histórico"
            life_c1, life_c2, life_c3, life_c4 = st.columns(4)
            life_c1.markdown(
                kpi_card("Score = 0", score_zero, "sellers sin vida crediticia"),
                unsafe_allow_html=True,
            )
            life_c2.markdown(
                kpi_card("Score > 0", score_non_zero, "sellers con vida crediticia"),
                unsafe_allow_html=True,
            )
            life_c3.markdown(
                kpi_card("Promedio total", f"{avg_all:.1f}", life_period),
                unsafe_allow_html=True,
            )
            life_c4.markdown(
                kpi_card("Promedio sin 0", f"{avg_non_zero:.1f}", life_period),
                unsafe_allow_html=True,
            )

            # Expander con las cédulas score=0 dentro del universo filtrado
            sellers_zero = credit_join[credit_join["score_crediticio"] == 0].drop_duplicates(
                subset=["nid", "cedula_cliente"]
            )
            if not sellers_zero.empty:
                with st.expander(f"Ver cédulas con score=0 ({len(sellers_zero)})", expanded=False):
                    cols_zero = ["nid", "producto", "linea_negocio", "cedula_cliente"]
                    if "execution_date" in sellers_zero.columns:
                        cols_zero.append("execution_date")
                    st.dataframe(
                        sellers_zero[cols_zero]
                        .sort_values("producto")
                        .rename(columns={
                            "nid": "NID", "producto": "Producto",
                            "linea_negocio": "Línea de negocio",
                            "cedula_cliente": "Cédula",
                            "execution_date": "Fecha consulta",
                        }),
                        hide_index=True, use_container_width=True,
                    )
            st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

            fig_life = go.Figure(go.Histogram(
                x=credit_join["score_crediticio"],
                marker_color=PRIMARY,
                opacity=0.85,
                nbinsx=30,
                hovertemplate="Score %{x}<br>Registros %{y}<extra></extra>",
                showlegend=False,
            ))
            fig_life.update_layout(
                paper_bgcolor=WHITE,
                plot_bgcolor=WHITE,
                font=dict(family="Inter, sans-serif", color=DEEP, size=11),
                title=dict(text=f"Distribución de scores crediticios · {life_period}", font=dict(size=13, color=DEEP)),
                xaxis=dict(title="Score crediticio", gridcolor="#ede8f5"),
                yaxis=dict(title="Registros", gridcolor="#ede8f5"),
                height=360,
                margin=dict(l=10, r=10, t=44, b=10),
            )
            st.plotly_chart(fig_life, use_container_width=True)
            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

            above_720_counts = (
                credit_join[credit_join["score_crediticio"] >= 720]["producto"]
                .value_counts().to_dict()
            )
            score_cols = st.columns(len(product_order))
            period_sub = "2026" if PERIODO_2026 else "histórico"
            for col, producto in zip(score_cols, product_order):
                col.markdown(
                    kpi_card("Score > 720", above_720_counts.get(producto, 0), f"{producto} · {period_sub}"),
                    unsafe_allow_html=True,
                )
            st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

            st.markdown(
                f"<h3 style='color:{DEEP};font-size:1rem;margin:14px 0 6px 0'>"
                f"Distribución por deciles de score</h3>",
                unsafe_allow_html=True,
            )
            decile_cols = st.columns(3)
            summary_frames: list[pd.DataFrame] = []
            for col, producto in zip(decile_cols, product_order):
                sub = credit_join[credit_join["producto"] == producto].copy()
                summary = _decil_summary(sub)
                if summary.empty:
                    with col:
                        st.info(f"Sin datos para {producto}.")
                    continue
                summary["Producto"] = producto
                summary_frames.append(summary.copy())
                colors = [
                    "#c2410c", "#ea580c", "#f97316", "#f59e0b", "#eab308",
                    "#84cc16", "#65a30d", "#16a34a", "#15803d", "#166534",
                ][:len(summary)]
                y_labels = summary["decil"].iloc[::-1]
                mins = summary["min_score"].iloc[::-1]
                maxs = summary["max_score"].iloc[::-1]
                avgs = summary["avg_score"].iloc[::-1]
                counts = summary["count"].iloc[::-1]
                fig_dec = go.Figure()
                fig_dec.add_trace(go.Bar(
                    x=(maxs - mins),
                    y=y_labels,
                    base=mins,
                    orientation="h",
                    marker=dict(color=colors[::-1], line=dict(color=DEEP, width=1)),
                    hovertemplate=(
                        "<b>%{y}</b><br>Rango: %{base:.0f} - %{x:+.0f}<extra></extra>"
                    ),
                    showlegend=False,
                ))
                fig_dec.add_trace(go.Scatter(
                    x=avgs,
                    y=y_labels,
                    mode="markers",
                    marker=dict(color="black", size=8),
                    hovertemplate="<b>%{y}</b><br>Promedio: %{x:.1f}<extra></extra>",
                    showlegend=False,
                ))
                for decil, min_s, max_s, avg_s, count_s in zip(y_labels, mins, maxs, avgs, counts):
                    fig_dec.add_annotation(
                        x=(min_s + max_s) / 2,
                        y=decil,
                        text=f"n={int(count_s)}",
                        showarrow=False,
                        font=dict(size=10, color=DEEP),
                        bgcolor="rgba(255,255,255,0.8)",
                    )
                    fig_dec.add_annotation(
                        x=min_s,
                        y=decil,
                        text=f"{int(min_s)}",
                        showarrow=False,
                        xanchor="right",
                        xshift=-8,
                        font=dict(size=9, color=DEEP),
                    )
                    fig_dec.add_annotation(
                        x=max_s,
                        y=decil,
                        text=f"{int(max_s)}",
                        showarrow=False,
                        xanchor="left",
                        xshift=8,
                        font=dict(size=9, color=DEEP),
                    )
                fig_dec.update_layout(
                    paper_bgcolor=WHITE,
                    plot_bgcolor=WHITE,
                    font=dict(family="Inter, sans-serif", color=DEEP, size=11),
                    title=dict(text=producto, font=dict(size=13, color=DEEP)),
                    height=420,
                    margin=dict(l=10, r=10, t=40, b=10),
                    xaxis=dict(title="Score crediticio", gridcolor="#ede8f5"),
                    yaxis=dict(title="Decil", categoryorder="array", categoryarray=list(y_labels)),
                )
                with col:
                    st.plotly_chart(fig_dec, use_container_width=True)

            if summary_frames:
                summary_all = pd.concat(summary_frames, ignore_index=True)
                st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

                if not PERIODO_2026:
                    pass  # "Edad vs score" solo aparece en modo 2026 (dataset es solo 2026)
                else:
                    st.markdown(
                        f"<h3 style='color:{DEEP};font-size:1rem;margin:14px 0 6px 0'>"
                        f"Edad vs score crediticio</h3>",
                        unsafe_allow_html=True,
                    )
                if PERIODO_2026 and df_escriturados_age.empty:
                    st.info("Aún no hay dataset procesado de edades para los escriturados 2026.")
                elif PERIODO_2026:
                    age_df = df_escriturados_age.copy()
                    product_order_age = [p for p in product_order if p in set(age_df["producto"].astype(str))]
                    corr = age_df["edad"].corr(age_df["score_crediticio"])
                    n_above_720 = int((age_df["score_crediticio"] >= 720).sum())
                    pct_above_720 = (n_above_720 / len(age_df) * 100) if len(age_df) else 0.0
                    k1, k2, k3, k4, k5 = st.columns(5)
                    k1.markdown(kpi_card("Personas", int(len(age_df)), "edad + score"), unsafe_allow_html=True)
                    k2.markdown(kpi_card("Edad promedio", f"{age_df['edad'].mean():.1f}", "años"), unsafe_allow_html=True)
                    k3.markdown(kpi_card("Score promedio", f"{age_df['score_crediticio'].mean():.0f}", "Experian"), unsafe_allow_html=True)
                    k4.markdown(kpi_card("Score ≥ 720", n_above_720, f"{pct_above_720:.1f}% del universo"), unsafe_allow_html=True)
                    k5.markdown(kpi_card("Correlación", f"{0.0 if pd.isna(corr) else corr:.2f}", "edad vs score"), unsafe_allow_html=True)

                    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

                    # Bucketizar edades en rangos de 10 años (20s, 30s, ..., 70+)
                    age_bins = [20, 30, 40, 50, 60, 70, 200]
                    age_labels = ["20-29", "30-39", "40-49", "50-59", "60-69", "70+"]
                    age_df["rango_edad"] = pd.cut(
                        age_df["edad"], bins=age_bins, labels=age_labels, right=False
                    )
                    age_df["banda_score"] = age_df["score_crediticio"].apply(
                        lambda s: "Sin vida (0)" if s == 0
                        else ("≥720 (elegible)" if s >= 720 else "<720")
                    )

                    corr_left, corr_right = st.columns(2)
                    with corr_left:
                        sin_vida = age_df[age_df["score_crediticio"] == 0]
                        con_vida = age_df[age_df["score_crediticio"] > 0]
                        fig_scatter = go.Figure()
                        fig_scatter.add_trace(go.Scatter(
                            x=con_vida["edad"], y=con_vida["score_crediticio"],
                            mode="markers", name="Con vida crediticia",
                            opacity=0.55,
                            marker=dict(size=7, color=PRIMARY),
                            hovertemplate="Edad: %{x}<br>Score: %{y}<extra></extra>",
                        ))
                        if not sin_vida.empty:
                            fig_scatter.add_trace(go.Scatter(
                                x=sin_vida["edad"], y=sin_vida["score_crediticio"],
                                mode="markers", name="Sin vida (score=0)",
                                marker=dict(size=10, color=RED, symbol="x", line=dict(width=2)),
                                hovertemplate="Edad: %{x}<br>Score: 0<extra></extra>",
                            ))
                        fig_scatter.add_hline(
                            y=720, line=dict(color=GREEN_DARK, width=2, dash="dash"),
                            annotation_text="Umbral 720", annotation_position="right",
                            annotation_font=dict(color=GREEN_DARK, size=10),
                        )
                        fig_scatter.add_hline(
                            y=0, line=dict(color=RED, width=1, dash="dot"),
                            annotation_text="Sin vida", annotation_position="right",
                            annotation_font=dict(color=RED, size=10),
                        )
                        fig_scatter.update_layout(
                            paper_bgcolor=WHITE, plot_bgcolor=WHITE,
                            font=dict(family="Inter, sans-serif", color=DEEP, size=11),
                            title=dict(text="Dispersión edad vs score", font=dict(size=13, color=DEEP)),
                            xaxis=dict(title="Edad", gridcolor="#ede8f5"),
                            yaxis=dict(title="Score crediticio", gridcolor="#ede8f5"),
                            height=380, margin=dict(l=10, r=80, t=40, b=10),
                            legend=dict(orientation="h", yanchor="bottom", y=-0.25, x=0),
                        )
                        st.plotly_chart(fig_scatter, use_container_width=True)

                    with corr_right:
                        fig_heat = go.Figure(go.Histogram2d(
                            x=age_df["edad"],
                            y=age_df["score_crediticio"],
                            colorscale=[[0, "#F4F1F9"], [1, PRIMARY]],
                            nbinsx=16,
                            nbinsy=16,
                            hovertemplate="Edad %{x}<br>Score %{y}<br>N %{z}<extra></extra>",
                        ))
                        fig_heat.update_layout(
                            paper_bgcolor=WHITE, plot_bgcolor=WHITE,
                            font=dict(family="Inter, sans-serif", color=DEEP, size=11),
                            title=dict(text="Matriz de concentración", font=dict(size=13, color=DEEP)),
                            xaxis=dict(title="Edad", gridcolor="#ede8f5"),
                            yaxis=dict(title="Score crediticio", gridcolor="#ede8f5"),
                            height=380, margin=dict(l=10, r=10, t=40, b=10),
                        )
                        st.plotly_chart(fig_heat, use_container_width=True)

                    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
                    fig_box = go.Figure()
                    for label in age_labels:
                        sub = age_df[age_df["rango_edad"] == label]
                        if sub.empty:
                            continue
                        fig_box.add_trace(go.Box(
                            y=sub["score_crediticio"],
                            name=label,
                            boxpoints="outliers",
                            marker_color=PRIMARY,
                            line_color=DEEP,
                            showlegend=False,
                        ))
                    fig_box.add_hline(
                        y=720, line=dict(color=GREEN_DARK, width=2, dash="dash"),
                        annotation_text="720", annotation_position="right",
                        annotation_font=dict(color=GREEN_DARK, size=10),
                    )
                    fig_box.update_layout(
                        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
                        font=dict(family="Inter, sans-serif", color=DEEP, size=11),
                        title=dict(text="Score por rango de edad", font=dict(size=13, color=DEEP)),
                        xaxis=dict(title="Rango de edad", gridcolor="#ede8f5"),
                        yaxis=dict(title="Score crediticio", gridcolor="#ede8f5"),
                        height=340, margin=dict(l=10, r=80, t=40, b=10),
                    )
                    st.plotly_chart(fig_box, use_container_width=True)

                    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
                    age_summary = (
                        age_df.assign(
                            es_cero=lambda d: (d["score_crediticio"] == 0).astype(int),
                            es_elegible=lambda d: (d["score_crediticio"] >= 720).astype(int),
                            es_bajo=lambda d: ((d["score_crediticio"] > 0) & (d["score_crediticio"] < 720)).astype(int),
                        )
                        .groupby("rango_edad", observed=True)
                        .agg(
                            N=("score_crediticio", "size"),
                            score_mediana=("score_crediticio", "median"),
                            pct_score_0=("es_cero", "mean"),
                            pct_menor_720=("es_bajo", "mean"),
                            pct_elegible=("es_elegible", "mean"),
                        )
                        .reindex(age_labels)
                        .dropna(subset=["N"])
                        .reset_index()
                    )
                    age_summary["pct_score_0"] = (age_summary["pct_score_0"] * 100).round(1).astype(str) + "%"
                    age_summary["pct_menor_720"] = (age_summary["pct_menor_720"] * 100).round(1).astype(str) + "%"
                    age_summary["pct_elegible"] = (age_summary["pct_elegible"] * 100).round(1).astype(str) + "%"
                    age_summary["score_mediana"] = age_summary["score_mediana"].round(0).astype(int)
                    age_summary["N"] = age_summary["N"].astype(int)
                    age_summary = age_summary.rename(columns={
                        "rango_edad": "Rango edad",
                        "N": "Personas",
                        "score_mediana": "Score mediana",
                        "pct_score_0": "% Score=0",
                        "pct_menor_720": "% <720",
                        "pct_elegible": "% ≥720",
                    })
                    st.dataframe(age_summary, hide_index=True, use_container_width=True)
                    st.caption(
                        f"Dataset escriturados 2026: {len(age_df)} personas (iBuyer + Alianza, "
                        "Inmobiliaria pendiente de procesar). Score=0 = sin vida crediticia en Experian."
                    )

                with st.expander("Tabla · resumen de deciles", expanded=False):
                    st.dataframe(
                        summary_all[["Producto", "decil", "count", "avg_score", "min_score", "max_score"]]
                        .rename(columns={
                            "decil": "Decil",
                            "count": "Cantidad",
                            "avg_score": "Score promedio",
                            "min_score": "Score mínimo",
                            "max_score": "Score máximo",
                        }),
                        hide_index=True,
                        use_container_width=True,
                    )

            with st.expander("Tabla · detalle de score crediticio", expanded=False):
                st.dataframe(
                    credit_join[["nid", "producto", "linea_negocio", "cedula_cliente", "score_crediticio"]]
                    .rename(columns={
                        "nid": "NID",
                        "producto": "Producto",
                        "linea_negocio": "Línea de negocio",
                        "cedula_cliente": "Cédula",
                        "score_crediticio": "Score crediticio",
                    }),
                    hide_index=True,
                    use_container_width=True,
                )



# ─────────────────────────────────────────────────────────────────────────────
# Distribución de leads (side-by-side, todas las categorias)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Distribución de leads</h2>", unsafe_allow_html=True)

if df_hs_f.empty:
    st.info("No hay deals con los filtros actuales.")
else:
    def _hbar(series: pd.Series, title: str, palette: tuple[str, str]):
        c = series.fillna("(sin valor)").astype(str).replace("", "(sin valor)").value_counts().reset_index()
        c.columns = ["cat", "N"]
        # Top 10 categorias por frecuencia
        c = c.head(10).sort_values("N", ascending=True)
        fig = go.Figure(go.Bar(
            x=c["N"], y=c["cat"], orientation="h",
            marker=dict(
                color=c["N"],
                colorscale=[[0, palette[0]], [1, palette[1]]],
                showscale=False,
            ),
            text=c["N"], textposition="outside", textfont_size=10,
        ))
        fig.update_layout(
            paper_bgcolor=WHITE, plot_bgcolor=WHITE,
            font=dict(family="Inter, sans-serif", color=DEEP, size=11),
            title=dict(text=title, font=dict(size=13, color=DEEP, family="Inter")),
            height=max(280, len(c) * 24 + 80),
            margin=dict(l=10, r=50, t=44, b=10),
            yaxis=dict(gridcolor="#ede8f5"),
            xaxis=dict(gridcolor="#ede8f5"),
        )
        return fig

    col_op, col_es = st.columns(2)
    with col_op:
        st.plotly_chart(_hbar(df_hs_f["oportunidad_del_negocio_label"],
                              "Oportunidad del negocio (CO)", (PALE, PRIMARY)),
                        use_container_width=True)
    with col_es:
        st.plotly_chart(_hbar(df_hs_f["estado_label"],
                              "Estado del negocio", (PALE, MED)),
                        use_container_width=True)

    # Tercera fila: Fuente + Variante como pies pequeños
    col_f, col_v = st.columns(2)
    with col_f:
        f_c = df_hs_f["fuente"].value_counts().reindex(hs_src.FUENTES).dropna().reset_index()
        f_c.columns = ["Fuente", "N"]
        if not f_c.empty:
            fig_f = go.Figure(go.Pie(
                labels=f_c["Fuente"], values=f_c["N"],
                hole=0.42, marker_colors=[DEEP, PRIMARY, MED, ACCENT],
                textinfo="label+percent+value", textfont_size=10,
            ))
            fig_f.update_layout(
                paper_bgcolor=WHITE, showlegend=False,
                title=dict(text="Fuente", font=dict(size=13, color=DEEP)),
                height=260, margin=dict(l=5, r=5, t=44, b=5),
            )
            st.plotly_chart(fig_f, use_container_width=True)
    with col_v:
        # Solo AH y BH — el experimento solo tiene esas dos variantes
        v_c = (
            df_hs_f[df_hs_f["ab_test_landing"].isin(["AH", "BH"])]["ab_test_landing"]
            .value_counts().reset_index()
        )
        v_c.columns = ["Variante", "N"]
        if not v_c.empty:
            fig_v = go.Figure(go.Pie(
                labels=v_c["Variante"], values=v_c["N"],
                hole=0.42, marker_colors=[PRIMARY, ACCENT],
                textinfo="label+percent+value", textfont_size=10,
            ))
            fig_v.update_layout(
                paper_bgcolor=WHITE, showlegend=False,
                title=dict(text="Variante A/B (solo AH/BH)", font=dict(size=13, color=DEEP)),
                height=260, margin=dict(l=5, r=5, t=44, b=5),
            )
            st.plotly_chart(fig_v, use_container_width=True)
        else:
            st.info("No hay deals con ab_test_landing ∈ {AH, BH} en el filtro actual.")


# ─────────────────────────────────────────────────────────────────────────────
# Categorías cliente
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Categorías cliente</h2>", unsafe_allow_html=True)

if df_hs_f.empty or "nid" not in df_hs_f.columns:
    st.info("No hay deals con `nid` disponible para construir categorías.")
else:
    nids_cat = tuple(sorted(df_hs_f["nid"].dropna().astype(int).unique().tolist()))
    if not nids_cat:
        st.info("No hay `nid` disponible en los deals filtrados.")
    else:
        try:
            df_cat = load_client_categories(nids_cat)
        except Exception as exc:
            df_cat = pd.DataFrame(columns=["nid", "motivo_venta_string"])
            st.warning(f"BigQuery categorías cliente: {type(exc).__name__}: {exc}")

        if df_cat.empty:
            st.info("No hubo categorías en BigQuery para los leads filtrados del FakeDoor.")
        else:
            df_cat["nid"] = pd.to_numeric(df_cat["nid"], errors="coerce")
            nids_visibles = set(df_hs_f["nid"].dropna().astype(int).tolist())
            df_cat = df_cat[df_cat["nid"].isin(nids_visibles)].copy()
            if df_cat.empty:
                st.info("No hubo categorías cruzables con los `nid` visibles del FakeDoor.")
                df_cat = pd.DataFrame(columns=["nid", "motivo_venta_string"])

            df_cat["categoria_clean"] = (
                df_cat["motivo_venta_string"]
                .fillna("(sin valor)")
                .astype(str)
                .str.strip()
                .replace("", "(sin valor)")
            )
            if _applied(sel_categorias, categorias_all):
                df_cat = df_cat[df_cat["categoria_clean"].isin(sel_categorias)]

            counts_all = df_cat["categoria_clean"].value_counts().reset_index()
            counts_all.columns = ["Categoría", "N"]

            if counts_all.empty:
                st.info("No hay categorías para los filtros actuales.")
            else:
                counts = counts_all.head(10).sort_values("N", ascending=True)
                counts["Categoría corta"] = counts["Categoría"].apply(_short_label)

                fig_cat = go.Figure(go.Bar(
                    x=counts["N"],
                    y=counts["Categoría corta"],
                    orientation="h",
                    marker=dict(
                        color=counts["N"],
                        colorscale=[[0, PALE], [1, PRIMARY]],
                        showscale=False,
                    ),
                    text=counts["N"],
                    textposition="outside",
                    textfont_size=10,
                    customdata=counts["Categoría"],
                    hovertemplate="<b>%{customdata}</b><br>%{x} leads<extra></extra>",
                ))
                fig_cat.update_layout(
                    paper_bgcolor=WHITE,
                    plot_bgcolor=WHITE,
                    font=dict(family="Inter, sans-serif", color=DEEP, size=11),
                    height=max(320, len(counts) * 28 + 80),
                    margin=dict(l=220, r=50, t=10, b=10),
                    xaxis=dict(title="Leads", gridcolor="#ede8f5"),
                    yaxis=dict(gridcolor="#ede8f5", automargin=True),
                )
                st.plotly_chart(fig_cat, use_container_width=True)
                st.caption(
                    f"Top 10 de {len(counts_all)} categorías · "
                    f"{int(df_cat['nid'].nunique())} nids en "
                    "`seller_digital_co_recepcionista_mm`."
                )


# ─────────────────────────────────────────────────────────────────────────────
# Funnel de usabilidad de la landing (4 etapas BQ)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Funnel de usabilidad de la landing</h2>", unsafe_allow_html=True)

if df_bq.empty:
    st.info("BigQuery no devolvió eventos de la landing.")
else:
    df_bq_in = df_bq.copy()
    if allowed_uuids:
        df_bq_in = df_bq_in[df_bq_in["uuid"].astype(str).isin(allowed_uuids)]

    # Etapa 1: Enviados WA — replicamos el total operativo confirmado.
    n_u1 = n_e2
    # Etapa 2: Abrió primer link (/<uuid>)
    n_u2 = int(df_bq_in.get("visited_home", pd.Series(dtype=int)).fillna(0).astype(int).sum())
    # Etapa 3: Llegó a /solicitud
    n_u3 = int(df_bq_in.get("visited_solicitud", pd.Series(dtype=int)).fillna(0).astype(int).sum())
    # Etapa 4: T&C firmados (= leads en Sheet, mismo del funnel principal)
    n_u4 = n_leads

    usab_stages = [
        ("Enviados WA",       n_u1, "Estimado (funnel principal)"),
        ("Abrió primer link", n_u2, "BQ · page view en /<uuid>"),
        ("Llegó a /solicitud", n_u3, "BQ · page view en /solicitud"),
        ("Firmó T&C",         n_u4, "Sheets/Leads"),
    ]
    u_labels = [s[0] for s in usab_stages]
    u_vals = [s[1] for s in usab_stages]
    u_sources = [s[2] for s in usab_stages]
    u_colors = [DEEP, PRIMARY, MED, GREEN_DARK]
    # No mostrar % cuando es > 100% (= tracking SPA limitado en flujo viejo)
    u_text = []
    for i, v in enumerate(u_vals):
        if i == 0 or u_vals[i - 1] <= 0:
            u_text.append(f"{v:,}")
        else:
            pct = v / u_vals[i - 1] * 100
            u_text.append(f"{v:,}  ({pct:.0f}%)" if pct <= 100 else f"{v:,}")
    use_log_u = (max(u_vals) if u_vals else 0) > 100 and all(v > 0 for v in u_vals)

    fig_u = go.Figure(go.Bar(
        x=u_vals, y=u_labels, orientation="h",
        marker_color=u_colors, text=u_text,
        textposition="outside", textfont=dict(size=11, color=DEEP),
        customdata=u_sources,
        hovertemplate="<b>%{y}</b><br>%{x:,} · %{customdata}<extra></extra>",
    ))
    fig_u.update_layout(
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=11),
        height=260, margin=dict(l=10, r=200, t=10, b=10),
        xaxis=dict(type="log" if use_log_u else "linear",
                   title="Clientes" + (" (log)" if use_log_u else ""),
                   gridcolor="#ede8f5", tickformat=",d"),
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig_u, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline de leads con metadata
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Pipeline de leads (con metadata del API)</h2>", unsafe_allow_html=True)

legend_html = "<div style='font-size:0.78rem;color:#444;margin-bottom:8px'>"
for k, c in STATUS_COLORS.items():
    legend_html += (
        f"<span class='legend-box' style='background:{c}'></span>"
        f"<span style='margin-right:14px'>{STATUS_LABELS[k]}</span>"
    )
legend_html += "</div>"
st.markdown(legend_html, unsafe_allow_html=True)

DISPLAY_COLS = [
    "nombre_completo", "telefono", "cedula", "grupo", "nid", "ctl", "fuente",
    "contesto?", "hipoteca_status",
    "score", "nivel_riesgo", "aplica",
    "cuota_maxima", "ingresos_mensuales", "razon",
]
disp = df_in[[c for c in DISPLAY_COLS if c in df_in.columns] + ["status"]].copy()
disp_sorted = disp.sort_values(
    by="status",
    key=lambda s: s.map({
        "aplica_pendiente_llamar": 0,
        "aplica_contactado":       1,
        "no_contesta":             2,
        "no_interesado":           3,
        "pendiente_score":         4,
        "no_aplica":               5,
        "error":                   6,
    }).fillna(99),
).reset_index(drop=True)


def _row_color(row_idx):
    s = disp_sorted.loc[row_idx, "status"]
    color = STATUS_COLORS.get(s, "")
    text = "white" if s in ("aplica_contactado", "error") else "#222"
    return [f"background-color:{color};color:{text}" for _ in range(len(disp_sorted.columns) - 1)]


styled = (
    disp_sorted.drop(columns=["status"])
    .rename(columns={
        "nombre_completo": "Nombre", "telefono": "Teléfono", "cedula": "Cédula",
        "grupo": "Grupo", "nid": "NID", "ctl": "CTL", "fuente": "Fuente", "contesto?": "Contesto?",
        "hipoteca_status": "Hipoteca?", "score": "Score", "nivel_riesgo": "Nivel",
        "aplica": "Aplica", "cuota_maxima": "Cuota Máxima",
        "ingresos_mensuales": "Ingresos", "razon": "Razón",
    })
    .style.apply(lambda r: _row_color(r.name), axis=1)
)
st.dataframe(styled, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Tabla raw de HubSpot deals (filtrados)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown(
    f"<h2>Desglose ({len(df_hs_f):,})</h2>",
    unsafe_allow_html=True,
)
if df_hs_f.empty:
    st.info("Sin deals con los filtros aplicados.")
else:
    # Consolidación: base = df_hs_f (HubSpot). Joineamos Leads, Entrevista, BQ.
    consolidated = df_hs_f.copy()
    consolidated["uuid_str"] = consolidated["deal_uuid"].astype(str)
    consolidated["phone_norm_hs"] = consolidated.get("phone", pd.Series(dtype=str)).apply(_norm_phone)

    # Sheets/Leads → cedula, grupo, contesto?, Aplica, score, nivel, cuota, ingresos, razon
    leads_cols = [c for c in [
        "uuid_str", "cedula", "grupo", "contesto?",
        "aplica", "score", "nivel_riesgo", "hipoteca_status", "hipoteca_fuente",
        "cuota_maxima", "ingresos_mensuales", "razon",
    ] if c in df_in.columns or c == "uuid_str"]
    leads_for_join = df_in[leads_cols].copy() if not df_in.empty else pd.DataFrame()
    if not leads_for_join.empty:
        consolidated = consolidated.merge(leads_for_join, on="uuid_str", how="left")

    # Entrevista → tiene hipoteca? (por teléfono)
    if not df_int.empty:
        int_join = df_int[["phone_norm", "tiene hipoteca?"]].drop_duplicates("phone_norm")
        int_join = int_join.rename(columns={"phone_norm": "phone_norm_hs"})
        consolidated = consolidated.merge(int_join, on="phone_norm_hs", how="left")
    else:
        consolidated["tiene hipoteca?"] = None

    # BigQuery → visitas a landing
    if not df_bq.empty:
        bq_join = df_bq[["uuid", "total_events", "visited_home", "visited_solicitud",
                          "visited_consent"]].copy()
        bq_join["uuid_str"] = bq_join["uuid"].astype(str)
        bq_join = bq_join.drop(columns=["uuid"])
        consolidated = consolidated.merge(bq_join, on="uuid_str", how="left")
    for c in ("total_events", "visited_home", "visited_solicitud", "visited_consent"):
        if c not in consolidated.columns:
            consolidated[c] = 0
    consolidated[["total_events", "visited_home", "visited_solicitud", "visited_consent"]] = (
        consolidated[["total_events", "visited_home", "visited_solicitud", "visited_consent"]]
        .fillna(0).astype(int)
    )

    # Flags derivados
    consolidated["firmó T&C"] = consolidated.get("cedula", pd.Series(dtype=str)).fillna("").astype(str).ne("")

    # Tabla final — orden y rename
    show_cols = [
        ("dealname",                    "Nombre del negocio"),
        ("nid",                         "nid"),
        ("ctl",                         "ctl"),
        ("phone",                       "Teléfono"),
        ("cedula",                      "Cédula"),
        ("createdate",                  "Fecha creación"),
        ("estado_label",                "Estado del Negocio"),
        ("oportunidad_del_negocio_label", "Oportunidad del Negocio"),
        ("fuente",                      "Fuente"),
        ("flag_fakedoor",               "Flag fakedoor"),
        ("ab_test_landing",             "Variante"),
        ("grupo",                       "Grupo form"),
        ("nombre_del_conjunto",         "Conjunto"),
        ("comite_remodelaciones",       "Comité Remo"),
        ("visited_home",                "BQ home"),
        ("visited_solicitud",           "BQ /solicitud"),
        ("visited_consent",             "BQ /consent"),
        ("total_events",                "BQ eventos"),
        ("firmó T&C",                   "Firmó T&C"),
        ("contesto?",                   "Contestó?"),
        ("hipoteca_status",             "Hipoteca?"),
        ("hipoteca_fuente",             "Fuente hipoteca"),
        ("aplica",                      "Aplica"),
        ("score",                       "Score"),
        ("nivel_riesgo",                "Nivel"),
        ("cuota_maxima",                "Cuota Máxima"),
        ("ingresos_mensuales",          "Ingresos"),
        ("razon",                       "Razón"),
        ("deal_uuid",                   "deal_uuid"),
    ]
    present = [(src, lab) for src, lab in show_cols if src in consolidated.columns]
    df_display = consolidated[[s for s, _ in present]].rename(columns=dict(present))
    st.dataframe(df_display, hide_index=True, use_container_width=True)

    with st.expander("Debug · propiedades vacías"):
        null_pct = (df_hs.isna().mean() * 100).round(1).sort_values(ascending=False)
        empty = null_pct[null_pct > 0]
        if empty.empty:
            st.caption("Todas las propiedades trajeron datos.")
        else:
            st.write("% NaN por propiedad (100% = internal name probablemente está mal):")
            st.dataframe(empty.to_frame("% NaN"), use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Hipoteca · doble fuente (Entrevista + HubSpot BNPL) sobre elegibles
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Hipoteca</h2>", unsafe_allow_html=True)

# Universo = elegibles del funnel (Aplica="si"). Los pending/vacios no entran
# aqui porque todavia no han pasado score y no son elegibles, aunque sea
# probable que terminen siendolo cuando el motor responda.
elegibles_h = df_in[df_in["aplica"].astype(str).str.lower() == "si"].copy()
n_elegibles = len(elegibles_h)

if n_elegibles == 0:
    st.info("Aún no hay elegibles con los filtros actuales.")
else:
    # Matriz tipo confusion matrix: filas = status hipoteca, cols = fuente.
    # Los valores vienen del cierre operativo aplicado upstream (3/4/5),
    # así que se calculan naturalmente desde elegibles_h.
    status_order = ["Sí", "No", "Sin dato"]
    fuente_order = ["Contacto", "BNPL (HubSpot)", "Sin contactar"]
    matrix = (
        elegibles_h.groupby(["hipoteca_status", "hipoteca_fuente"])
        .size().unstack(fill_value=0)
        .reindex(index=status_order, columns=fuente_order, fill_value=0)
        .astype(int)
    )
    z = matrix.values.tolist()
    n_si = int((elegibles_h["hipoteca_status"] == "Sí").sum())
    n_no = int((elegibles_h["hipoteca_status"] == "No").sum())
    n_sd = int((elegibles_h["hipoteca_status"] == "Sin dato").sum())
    elegibles_confirmados = elegibles_h[
        (elegibles_h["hipoteca_fuente"] == "Contacto")
        & (elegibles_h["hipoteca_status"].isin(["Sí", "No"]))
    ]
    n_confirmados = len(elegibles_confirmados)
    pct_si_confirmado = (
        (elegibles_confirmados["hipoteca_status"] == "Sí").mean() * 100
        if n_confirmados > 0 else 0.0
    )
    pct_no_confirmado = (
        (elegibles_confirmados["hipoteca_status"] == "No").mean() * 100
        if n_confirmados > 0 else 0.0
    )

    st.caption(
        f"{n_elegibles} elegibles · "
        f"{n_no} sin hipoteca (elegibles finales) · "
        f"{n_si} con hipoteca (excluidos) · "
        f"{n_sd} sin dato visible en la tabla operativa."
    )
    if n_confirmados > 0:
        st.caption(
            f"Confirmados por llamada: {n_confirmados} · "
            f"{pct_si_confirmado:.0f}% con hipoteca · {pct_no_confirmado:.0f}% sin hipoteca."
        )

    z_max = max(max(max(row) for row in z), 1)
    # Texto blanco si la celda está oscura (>= 50% del máximo)
    text_colors = [
        ["#fff" if z[i][j] >= z_max * 0.5 else DEEP for j in range(len(fuente_order))]
        for i in range(len(status_order))
    ]

    fig_h = go.Figure(go.Heatmap(
        z=z,
        x=fuente_order,
        y=status_order,
        colorscale=[[0, "#F4F1F9"], [0.001, PALE], [1, PRIMARY]],
        showscale=False,
        xgap=2, ygap=2,
        hovertemplate="<b>%{y}</b> · %{x} · %{z} elegibles<extra></extra>",
    ))
    # Anotaciones con el conteo de cada celda
    for i, status in enumerate(status_order):
        for j, fuente in enumerate(fuente_order):
            fig_h.add_annotation(
                x=fuente, y=status, text=f"<b>{int(z[i][j])}</b>",
                showarrow=False,
                font=dict(size=22, color=text_colors[i][j], family="Inter, sans-serif"),
            )
    fig_h.update_layout(
        paper_bgcolor=WHITE, plot_bgcolor=WHITE,
        font=dict(family="Inter, sans-serif", color=DEEP, size=12),
        height=320, margin=dict(l=10, r=10, t=20, b=40),
        xaxis=dict(
            title=dict(text="Fuente del dato", font=dict(size=12, color=MED)),
            side="bottom", showgrid=False, zeroline=False, ticks="",
        ),
        yaxis=dict(
            title=dict(text="¿Tiene hipoteca?", font=dict(size=12, color=MED)),
            autorange="reversed", showgrid=False, zeroline=False, ticks="",
        ),
    )
    st.plotly_chart(fig_h, use_container_width=True)

    st.markdown(
        f"""
<div style="background:{WHITE};border:1px solid #ede8f5;border-radius:10px;padding:14px 16px;margin:10px 0 14px 0">
  <div style="color:{DEEP};font-weight:700;margin-bottom:8px">Lectura cualitativa</div>
  <div style="color:#3f3f46;font-size:0.92rem;line-height:1.5">
    <div>La urgencia es baja: no aparece presión inmediata por liquidez.</div>
    <div>La propuesta se entiende más como una jugada de inversión y flexibilidad financiera que como una necesidad de emergencia.</div>
    <div>Varios lo leen como una forma de volver líquida la plata sin vender el inmueble, incluso conservando la opción de ponerlo a rentar.</div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    # Tabla de detalle por elegible
    cols_h = [c for c in [
        "nombre_completo", "telefono", "cedula", "grupo",
        "score", "hipoteca_status", "hipoteca_fuente",
    ] if c in elegibles_h.columns]
    detalle = (
        elegibles_h[cols_h]
        .sort_values(
            by="hipoteca_status",
            key=lambda s: s.map({"Sí": 0, "Sin dato": 1, "No": 2}).fillna(99),
        )
        .rename(columns={
            "nombre_completo": "Nombre", "telefono": "Teléfono", "cedula": "Cédula",
            "grupo": "Grupo", "score": "Score",
            "hipoteca_status": "Hipoteca", "hipoteca_fuente": "Fuente del dato",
        })
    )
    st.dataframe(detalle, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Decisión · GO / ITERATE / KILL + AH vs BH
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<h2>Decisión · GO / ITERATE / KILL</h2>", unsafe_allow_html=True)

# Umbrales del experimento (ver doc de diseño)
TRACCION_GO = 0.40       # ≥ 40% → GO
TRACCION_KILL = 0.20     # < 20% → KILL · 20–40% → ITERATE
ELASTICIDAD_PP = 20      # ≥ 20pp accionable por umbral
P_VALUE_SIG = 0.05       # significancia estadistica para z-test

# Tracción pooled: % de leads con T&C que aplican. Usamos df_in (mismo
# universo que el funnel, respeta los filtros del sidebar) para que las 3
# secciones (Funnel, Decisión, Hipoteca) sean siempre congruentes.
# n_aplica = pasaron score · n_aplica_sin_hip = score + hipoteca confirmada NO.
n_tc = len(df_in)
n_aplican = n_aplica
n_aplican_final = n_aplica_sin_hip
traccion = n_aplican / n_tc if n_tc > 0 else 0.0

if traccion >= TRACCION_GO:
    decision = "GO"
    decision_color = GREEN_DARK
    decision_text = "Demanda real confirmada · construir el producto"
elif traccion >= TRACCION_KILL:
    decision = "ITERATE"
    decision_color = "#B45309"  # amber-700 · contraste con texto blanco
    decision_text = "Señal ambigua · iterar copy o extender el experimento"
else:
    decision = "KILL"
    decision_color = RED
    decision_text = "Sin demanda · no construir o pivot a producto alterno"

# Elasticidad AH vs BH — conversión a T&C sobre el universo HS por variante
def _conv_variante(var: str) -> tuple[int, int, float]:
    if df_hs.empty or "ab_test_landing" not in df_hs.columns:
        return 0, 0, 0.0
    universo_v = df_hs[df_hs["ab_test_landing"] == var]
    n_u = len(universo_v)
    uuids_v = set(universo_v["deal_uuid"].dropna().astype(str))
    n_tc_v = int(sum(uuid in uuids_v for uuid in lead_uuid_set))
    conv = n_tc_v / n_u if n_u > 0 else 0.0
    return n_u, n_tc_v, conv


def _z_test_two_proportions(s1: int, n1: int, s2: int, n2: int) -> tuple[float, float]:
    """Z-test de proporciones, una cola en dirección BH > AH.

    Devuelve (z, p_value). z positivo → BH > AH.
    """
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0
    p1, p2 = s1 / n1, s2 / n2
    p_pool = (s1 + s2) / (n1 + n2)
    if p_pool in (0.0, 1.0):
        return 0.0, 1.0
    se = (p_pool * (1 - p_pool) * (1 / n1 + 1 / n2)) ** 0.5
    if se == 0:
        return 0.0, 1.0
    z = (p2 - p1) / se
    # p-value una cola: P(Z > z) con la normal estándar
    p_value = 0.5 * (1 - erf(z / sqrt(2)))
    return z, p_value


n_ah, tc_ah, conv_ah = _conv_variante("AH")
n_bh, tc_bh, conv_bh = _conv_variante("BH")
diff_pp = (conv_bh - conv_ah) * 100
z_stat, p_value = _z_test_two_proportions(tc_ah, n_ah, tc_bh, n_bh)

# Lógica de recomendación de plazo:
# 1) Si la diferencia es estadísticamente significativa (p < 0.05) → mayor.
# 2) Si |diff| ≥ 20pp → mayor (umbral predefinido del experimento).
# 3) Si BH > AH direccionalmente → BH (señal de producto: cliente prefiere
#    cuota más baja con plazo más largo; criterio empírico del experimento
#    original, donde BH fue elegido a pesar de diff < 20pp por dirección clara).
# 4) Si AH ≥ BH → AH por menor exposición de riesgo.
if p_value < P_VALUE_SIG and conv_bh > conv_ah:
    plazo_pick = "BH (120 meses)"
    plazo_razon = f"z={z_stat:.2f} · p={p_value:.3f} · BH > AH estadísticamente significativo"
elif (1 - p_value) < P_VALUE_SIG and conv_ah > conv_bh:
    plazo_pick = "AH (84 meses)"
    plazo_razon = f"z={z_stat:.2f} · p={1-p_value:.3f} · AH > BH estadísticamente significativo"
elif abs(diff_pp) >= ELASTICIDAD_PP:
    plazo_pick = "BH (120 meses)" if diff_pp > 0 else "AH (84 meses)"
    plazo_razon = f"diff {abs(diff_pp):.1f}pp ≥ {ELASTICIDAD_PP}pp · accionable por umbral"
elif conv_bh > conv_ah:
    plazo_pick = "BH (120 meses)"
    plazo_razon = (
        f"diff {abs(diff_pp):.1f}pp · BH direccionalmente mejor (z={z_stat:.2f}, p={p_value:.3f}) · "
        "señal de producto consistente"
    )
else:
    plazo_pick = "AH (84 meses)"
    plazo_razon = f"AH ≥ BH · default por menor exposición de riesgo"

# Tarjeta de decisión
st.markdown(
    f"""
<div style="display:flex;gap:14px;margin-bottom:14px">
  <div style="flex:1;background:{decision_color};color:white;padding:18px 20px;border-radius:8px">
    <div style="font-size:0.75rem;opacity:0.85;letter-spacing:0.08em;text-transform:uppercase">Tracción pooled</div>
    <div style="font-size:2.2rem;font-weight:700;line-height:1.1;margin:4px 0">{decision}</div>
    <div style="font-size:0.85rem;opacity:0.95">{traccion*100:.1f}% de los T&C aplican (sin hipoteca, score≥720)</div>
    <div style="font-size:0.8rem;opacity:0.85;margin-top:6px">{decision_text}</div>
  </div>
  <div style="flex:1;background:{DEEP};color:white;padding:18px 20px;border-radius:8px">
    <div style="font-size:0.75rem;opacity:0.85;letter-spacing:0.08em;text-transform:uppercase">Plazo recomendado</div>
    <div style="font-size:2.2rem;font-weight:700;line-height:1.1;margin:4px 0">{plazo_pick}</div>
    <div style="font-size:0.85rem;opacity:0.95">AH {conv_ah*100:.1f}% · BH {conv_bh*100:.1f}% · diff {diff_pp:+.1f}pp</div>
    <div style="font-size:0.8rem;opacity:0.85;margin-top:6px">{plazo_razon}</div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

# Tabla de soporte
col_d1, col_d2 = st.columns(2)
with col_d1:
    st.markdown(f"<h3 style='color:{DEEP};font-size:1rem;margin:8px 0 6px 0'>Tracción · cálculo</h3>", unsafe_allow_html=True)
    df_tr = pd.DataFrame({
        "Métrica": [
            "Leads con T&C",
            "Elegibles (Aplica=si)",
            "Confirmados sin hipoteca",
            "% Tracción",
        ],
        "Valor": [
            f"{n_tc}",
            f"{n_aplican}",
            f"{n_aplican_final}",
            f"{traccion*100:.1f}%",
        ],
    })
    st.dataframe(df_tr, hide_index=True, use_container_width=True)
    st.caption(
        f"Tracción usa Aplica=si / T&C (mismo numerador que el funnel etapa 6). "
        f"Umbrales · ≥{TRACCION_GO*100:.0f}% GO · {TRACCION_KILL*100:.0f}–{TRACCION_GO*100:.0f}% ITERATE · <{TRACCION_KILL*100:.0f}% KILL"
    )

with col_d2:
    st.markdown(f"<h3 style='color:{DEEP};font-size:1rem;margin:8px 0 6px 0'>Elasticidad · AH vs BH</h3>", unsafe_allow_html=True)
    df_el = pd.DataFrame({
        "Variante": ["AH (84m)", "BH (120m)"],
        "Universo HS": [n_ah, n_bh],
        "T&C": [tc_ah, tc_bh],
        "Conv T&C": [f"{conv_ah*100:.2f}%", f"{conv_bh*100:.2f}%"],
    })
    st.dataframe(df_el, hide_index=True, use_container_width=True)
    st.caption(
        f"z={z_stat:.2f} · p={p_value:.3f} (una cola, H₁: BH > AH). "
        f"Recomendar BH si p<0.05 ó BH > AH direccionalmente · AH solo si AH ≥ BH."
    )

with st.expander("Matriz de lectura"):
    st.markdown(
        """
| Observación | Diagnóstico / Acción |
|---|---|
| Tracción ≥ 40% + elegibilidad ≥ 30% | Demanda sólida · **GO** · decidir plazo |
| Tracción alta · BH > AH por ≥ 20pp | **GO con 120m** |
| Tracción alta · AH > BH por ≥ 20pp | **GO con 84m** |
| Tracción alta · AH ≈ BH (diff < 10pp) | **GO con 84m** por menor exposición |
| Tracción 20–40% | **ITERATE** · iterar copy o extender |
| Tracción < 20% | **KILL** o pivot a producto alterno |
| Tracción alta solo en 1–2 segmentos | **GO selectivo** sobre segmentos núcleo |
"""
    )


st.divider()
st.caption(
    f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
    "TTL cache: 120s (Sheets) · 24h (HS, BQ, HS property labels). "
    "Scores persisten en Aplica + Metadata del Sheet."
)
