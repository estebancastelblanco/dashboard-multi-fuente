"""Score risk-engine — solo lectura del Sheet.

Desde que el fakedoor frontend escribe Aplica + Metadata en tiempo real
al firmar T&C, el dashboard ya no necesita consultar el API. Solo
parseamos lo que ya está persistido en las columnas del Sheet.

Las columnas esperadas en la pestaña Leads:
  - Aplica      : "si" | "no" | "pending" | "error"
  - Metadata    : JSON con Score, Nivel Riesgo, Ingresos Mensuales,
                  Cuota Máxima, Razón, etc.
"""
from __future__ import annotations

import json

import pandas as pd

APLICA_COL = "Aplica"
META_COL = "Metadata"


def _parse_metadata(aplica_cell: str, meta_cell: str) -> dict:
    aplica = (aplica_cell or "").strip().lower()
    if aplica in ("sí",):
        aplica = "si"
    if not aplica:
        return {
            "score": None, "nivel_riesgo": None, "cuota_maxima": None,
            "ingresos_mensuales": None, "max_credito": None, "razon": None,
            "aplica": "pending",
        }

    meta: dict = {}
    if meta_cell:
        try:
            meta = json.loads(meta_cell)
        except Exception:
            meta = {"_raw_cell": meta_cell}

    score_raw = meta.get("score") or meta.get("Score")
    try:
        score = int(score_raw) if score_raw is not None else None
    except (TypeError, ValueError):
        score = None

    # Si el engine quedó en pending (típicamente income_validation_pending) pero
    # tenemos un score, decide por score. El umbral del producto es 720.
    if aplica == "pending" and score is not None:
        aplica = "si" if score >= 720 else "no"

    return {
        "score": score,
        "nivel_riesgo": meta.get("nivel_riesgo") or meta.get("Nivel Riesgo"),
        "cuota_maxima": meta.get("Cuota Máxima") or meta.get("cuota_maxima"),
        "ingresos_mensuales": meta.get("Ingresos Mensuales") or meta.get("ingresos_mensuales"),
        "max_credito": meta.get("Máx. Crédito") or meta.get("max_credito"),
        "razon": meta.get("Razón") or meta.get("razon"),
        "aplica": aplica,
    }


def enrich_leads_with_scores(
    df_leads: pd.DataFrame,
    *,
    tab: str = "Leads",  # legacy param — no longer used
    cedula_col: str = "cedula",  # legacy param — kept for API stability
    sheet_id: str | None = None,  # legacy param — kept for API stability
) -> tuple[pd.DataFrame, dict]:
    """Lee Aplica + Metadata de cada fila y expande a columnas tipadas.

    Mantiene la misma firma que antes (para compatibilidad con la página
    del dashboard) pero ya no llama al API ni escribe al Sheet — el
    fakedoor frontend lo hace en tiempo real al firmar T&C.
    """
    del tab, cedula_col, sheet_id  # no usados — preserva API
    df = df_leads.copy()

    has_aplica = APLICA_COL in df.columns
    has_meta = META_COL in df.columns

    parsed = []
    n_filled = n_empty = 0
    for _, row in df.iterrows():
        aplica_cell = str(row.get(APLICA_COL, "") or "").strip() if has_aplica else ""
        meta_cell = str(row.get(META_COL, "") or "").strip() if has_meta else ""
        p = _parse_metadata(aplica_cell, meta_cell)
        if aplica_cell:
            n_filled += 1
        else:
            n_empty += 1
        parsed.append(p)

    df["score"] = [p["score"] for p in parsed]
    df["nivel_riesgo"] = [p["nivel_riesgo"] for p in parsed]
    df["cuota_maxima"] = [p["cuota_maxima"] for p in parsed]
    df["ingresos_mensuales"] = [p["ingresos_mensuales"] for p in parsed]
    df["max_credito"] = [p["max_credito"] for p in parsed]
    df["razon"] = [p["razon"] for p in parsed]
    df["aplica"] = [p["aplica"] for p in parsed]

    stats = {
        "filled": n_filled,
        "empty": n_empty,
        "source": "sheet_only",
    }
    return df, stats
