"""Conector HubSpot - token desde variable de entorno."""
from __future__ import annotations

import os

import pandas as pd
import requests


def fetch_recent_deals(limit: int = 10) -> pd.DataFrame:
    token = os.environ["HUBSPOT_ACCESS_TOKEN"]
    properties = ["dealname", "amount", "dealstage", "createdate"]
    url = "https://api.hubapi.com/crm/v3/objects/deals/search"
    payload = {
        "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}],
        "properties": properties,
        "limit": int(limit),
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    resp.raise_for_status()
    results = resp.json().get("results", [])

    rows = [{k: d.get("properties", {}).get(k) for k in properties} for d in results]
    df = pd.DataFrame(rows, columns=properties).rename(columns={
        "dealname": "Deal",
        "amount": "Monto",
        "dealstage": "Etapa",
        "createdate": "Creado",
    })
    return df
