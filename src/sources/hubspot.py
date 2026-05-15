"""Conector HubSpot - token desde variable de entorno."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import requests

# Propiedades del experimento FakeDoor (internal_name -> label en HubSpot)
FAKEDOOR_PROPS: dict[str, str] = {
    "hs_object_id":                 "ID de registro",
    "dealname":                     "Nombre del negocio",
    "hubspot_owner_id":             "Propietario del negocio",
    "pipeline":                     "Pipeline",
    "createdate":                   "Fecha de creación",
    "estado":                       "Estado del Negocio",
    "deal_uuid":                    "deal_uuid",
    "nid":                          "nid",
    "ctl":                          "ctl",
    "flag_fakedoor":                "flag fakedoor",
    "negocio_aplica_para_bnpl":     "¿Negocio aplica para BNPL?",
    "oportunidad_del_negocio":      "Oportunidad del negocio (CO)",
    "phone":                        "Teléfono",
    "nombre_del_conjunto":          "nombre del conjunto",
    "abc_test_landing_co":          "ABC test landing Co",
    "ab_test_landing":              "ab_test_landing",
    "comite_remodelaciones":        "Comité Remodelaciones",
    "razon_rechazo_comite":         "Razón rechazo comité",
}


# Valores de comite_remodelaciones que indican "Rechazos Remo" (del dashboard original)
REMO_VALUES = {
    "No se validó parqueadero y/o depósito",
    "Rechazado Visita vencida",
    "Se Realizó A. Virtual se Requiere A. Presencial.",
    "Solucionar cliente - Inmueble con humedad",
}

FUENTES = ["Top", "MM + Inmo", "Rechazos Comite", "Rechazos Remo"]


def compute_fuente(row) -> str:
    """Clasifica un deal en una de 4 fuentes (replica logica de resultadosfakedoor/dashboard.py).

    Para la comparacion con 'Descartado por comité' usa la columna *_label*
    si existe (post-decode de internal values); fallback al valor crudo.
    """
    flag = str(row.get("flag_fakedoor", "")).strip()
    if flag == "Top":
        return "Top"
    remo = str(row.get("comite_remodelaciones", "")).strip()
    if remo and remo not in ("nan",) and remo in REMO_VALUES:
        return "Rechazos Remo"
    op = str(
        row.get("oportunidad_del_negocio_label", row.get("oportunidad_del_negocio", ""))
    ).strip()
    if op == "Descartado por comité":
        return "Rechazos Comite"
    return "MM + Inmo"


def fetch_property_options(property_name: str) -> dict[str, str]:
    """Devuelve mapping internal_value -> label para una propiedad enum de Deal.

    HubSpot devuelve `1`, `2`, `descartado_por_comite` etc en la API; el dashboard
    debe mostrar el label legible. Cachear en streamlit con TTL alto (1h).
    """
    url = f"https://api.hubapi.com/crm/v3/properties/deals/{property_name}"
    resp = requests.get(url, headers=_headers(), timeout=20)
    resp.raise_for_status()
    body = resp.json()
    return {opt["value"]: opt.get("label", opt["value"])
            for opt in body.get("options", []) if "value" in opt}


def _token() -> str:
    return os.environ["HUBSPOT_ACCESS_TOKEN"]


def fetch_recent_deals(limit: int = 10) -> pd.DataFrame:
    """Últimos N deals — demo simple."""
    properties = ["dealname", "amount", "dealstage", "createdate"]
    url = "https://api.hubapi.com/crm/v3/objects/deals/search"
    payload = {
        "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}],
        "properties": properties,
        "limit": int(limit),
    }
    resp = requests.post(url, json=payload, headers=_headers(), timeout=20)
    resp.raise_for_status()
    rows = [{k: d.get("properties", {}).get(k) for k in properties}
            for d in resp.json().get("results", [])]
    return pd.DataFrame(rows, columns=properties).rename(columns={
        "dealname": "Deal", "amount": "Monto",
        "dealstage": "Etapa", "createdate": "Creado",
    })


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}


def fetch_fakedoor_deals(since_iso: str | None = None) -> pd.DataFrame:
    """Trae deals con flag_fakedoor IS_NOT_EMPTY.

    Si `since_iso` se pasa, agrega filtro createdate >= since_iso.
    Pagina 100 a la vez hasta agotar resultados. Devuelve un DataFrame con
    las columnas internas (snake_case) que el dashboard sabrá renombrar.
    """
    properties = list(FAKEDOOR_PROPS.keys())
    url = "https://api.hubapi.com/crm/v3/objects/deals/search"
    rows: list[dict] = []
    after: str | None = None
    filters: list[dict] = [{"propertyName": "flag_fakedoor", "operator": "HAS_PROPERTY"}]
    if since_iso:
        since_ms = int(datetime.fromisoformat(since_iso).replace(tzinfo=timezone.utc).timestamp() * 1000)
        filters.append({"propertyName": "createdate", "operator": "GTE", "value": str(since_ms)})
    for _ in range(50):  # max 5000 deals
        payload = {
            "filterGroups": [{"filters": filters}],
            "properties": properties,
            "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}],
            "limit": 100,
        }
        if after:
            payload["after"] = after
        resp = requests.post(url, json=payload, headers=_headers(), timeout=30)
        resp.raise_for_status()
        body = resp.json()
        for deal in body.get("results", []):
            props = deal.get("properties", {})
            rows.append({k: props.get(k) for k in properties})
        paging = body.get("paging", {}).get("next")
        if not paging:
            break
        after = paging.get("after")

    df = pd.DataFrame(rows, columns=properties)
    if "createdate" in df.columns:
        df["createdate"] = pd.to_datetime(df["createdate"], errors="coerce")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# EXP-003 · Pre-Oferta Temprana (MX)
# ─────────────────────────────────────────────────────────────────────────────
PREOFERTA_PROPS: dict[str, str] = {
    "hs_object_id":                     "ID de registro",
    "dealname":                         "Nombre del negocio",
    "createdate":                       "Fecha de creación",
    "pipeline":                         "Pipeline",
    "dealstage":                        "Etapa",
    "deal_uuid":                        "deal_uuid",
    "nid":                              "nid",
    "contacto_digital":                 "Contacto Digital",
    "quiero_recibir_oferta_formal":     "Quiero recibir oferta formal",
    "tengo_preguntas":                  "Tengo preguntas",
    "error_preoferta":                  "Error pre-oferta",
    "phone":                            "Teléfono",
    "precio_maximo_prestamo":           "Precio máximo préstamo",
    "hubspot_owner_id":                 "Propietario del negocio",
    "categoria_comercial":              "Categoría comercial",
    "prioridad_gestion_market_maker":   "Prioridad gestión MM",
}


def fetch_preoferta_deals(
    since_iso: str = "2026-05-07",
    until_iso: str | None = None,
    contacto_digital: str | None = "seller",
) -> pd.DataFrame:
    """Trae deals MX en el rango de fechas.

    Si `contacto_digital` se pasa, filtra por ese valor (default 'seller', el
    canal del experimento). Pasa `None` para traer todos los canales (útil para
    construir el control comparable).
    """
    properties = list(PREOFERTA_PROPS.keys())
    url = "https://api.hubapi.com/crm/v3/objects/deals/search"
    rows: list[dict] = []
    after: str | None = None
    since_ms = int(datetime.fromisoformat(since_iso).replace(tzinfo=timezone.utc).timestamp() * 1000)
    filters = [
        {"propertyName": "createdate", "operator": "GTE", "value": str(since_ms)},
    ]
    if until_iso:
        until_ms = int(datetime.fromisoformat(until_iso).replace(tzinfo=timezone.utc).timestamp() * 1000)
        filters.append({"propertyName": "createdate", "operator": "LTE", "value": str(until_ms)})
    if contacto_digital is not None:
        filters.append({"propertyName": "contacto_digital", "operator": "EQ", "value": contacto_digital})
    for _ in range(100):
        payload = {
            "filterGroups": [{"filters": filters}],
            "properties": properties,
            "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}],
            "limit": 100,
        }
        if after:
            payload["after"] = after
        resp = requests.post(url, json=payload, headers=_headers(), timeout=30)
        resp.raise_for_status()
        body = resp.json()
        for deal in body.get("results", []):
            props = deal.get("properties", {})
            rows.append({k: props.get(k) for k in properties})
        paging = body.get("paging", {}).get("next")
        if not paging:
            break
        after = paging.get("after")

    df = pd.DataFrame(rows, columns=properties)
    if "createdate" in df.columns:
        df["createdate"] = pd.to_datetime(df["createdate"], errors="coerce")
    for col in ("quiero_recibir_oferta_formal", "tengo_preguntas", "error_preoferta"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    return df


def list_deal_properties() -> pd.DataFrame:
    """Devuelve todas las propiedades del objeto deal (para descubrir internal names)."""
    url = "https://api.hubapi.com/crm/v3/properties/deals"
    resp = requests.get(url, headers=_headers(), timeout=20)
    resp.raise_for_status()
    rows = [{"name": p["name"], "label": p.get("label", "")} for p in resp.json().get("results", [])]
    return pd.DataFrame(rows).sort_values("label").reset_index(drop=True)
