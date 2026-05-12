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
    """Clasifica un deal en una de 4 fuentes (replica logica de resultadosfakedoor/dashboard.py)."""
    flag = str(row.get("flag_fakedoor", "")).strip()
    if flag == "Top":
        return "Top"
    remo = str(row.get("comite_remodelaciones", "")).strip()
    if remo and remo not in ("nan",) and remo in REMO_VALUES:
        return "Rechazos Remo"
    op = str(row.get("oportunidad_del_negocio", "")).strip()
    if op == "Descartado por comité":
        return "Rechazos Comite"
    return "MM + Inmo"


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


def fetch_fakedoor_deals(since_iso: str = "2026-04-20") -> pd.DataFrame:
    """Trae deals con flag_fakedoor IS_NOT_EMPTY y createdate >= since_iso.

    Pagina 100 a la vez hasta agotar resultados. Devuelve un DataFrame con
    las columnas internas (snake_case) que el dashboard sabrá renombrar.
    """
    since_ms = int(datetime.fromisoformat(since_iso).replace(tzinfo=timezone.utc).timestamp() * 1000)
    properties = list(FAKEDOOR_PROPS.keys())
    url = "https://api.hubapi.com/crm/v3/objects/deals/search"
    rows: list[dict] = []
    after: str | None = None
    for _ in range(50):  # max 5000 deals
        payload = {
            "filterGroups": [{
                "filters": [
                    {"propertyName": "createdate", "operator": "GTE", "value": str(since_ms)},
                    {"propertyName": "flag_fakedoor", "operator": "HAS_PROPERTY"},
                ],
            }],
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


def list_deal_properties() -> pd.DataFrame:
    """Devuelve todas las propiedades del objeto deal (para descubrir internal names)."""
    url = "https://api.hubapi.com/crm/v3/properties/deals"
    resp = requests.get(url, headers=_headers(), timeout=20)
    resp.raise_for_status()
    rows = [{"name": p["name"], "label": p.get("label", "")} for p in resp.json().get("results", [])]
    return pd.DataFrame(rows).sort_values("label").reset_index(drop=True)
