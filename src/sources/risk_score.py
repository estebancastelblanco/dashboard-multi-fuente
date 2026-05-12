"""Score risk-engine — consulta + persistencia en Google Sheet.

El Sheet sirve como caché: si la columna `Aplica` ya está rellena para una
cédula, no se llama al API. Si está vacía y el API está configurado, se
consulta y se escribe el resultado en `Aplica` + `Metadata` (JSON).

Variables de entorno:
  SCORE_API_URL    base URL, e.g. https://score-api.habicapital.co/v1/score
  SCORE_API_TOKEN  bearer token
"""
from __future__ import annotations

import json
import os
from typing import TypedDict

import pandas as pd
import requests

from . import gsheets

APLICA_COL = "Aplica"
META_COL = "Metadata"


class ScoreResult(TypedDict, total=False):
    cedula: str
    score: int | None
    nivel_riesgo: str | None
    aplica: str  # "si" | "no" | "pending" | "error"
    raw: dict | None


def is_configured() -> bool:
    return bool(os.environ.get("SCORE_API_URL") and os.environ.get("SCORE_API_TOKEN"))


def _consultar_api(cedula: str) -> ScoreResult:
    if not is_configured():
        return {"cedula": cedula, "aplica": "pending"}
    url = os.environ["SCORE_API_URL"].rstrip("/") + f"/{cedula}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {os.environ['SCORE_API_TOKEN']}"},
            timeout=20,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        return {"cedula": cedula, "aplica": "error",
                "raw": {"error": f"{type(exc).__name__}: {exc}"}}

    # El API puede devolver el score en distintos campos según implementación.
    score_raw = body.get("score") or body.get("Score")
    try:
        score = int(score_raw) if score_raw is not None else None
    except (TypeError, ValueError):
        score = None
    nivel = body.get("nivel_riesgo") or body.get("Nivel Riesgo") or body.get("nivel")
    aplica = "si" if (score is not None and score >= 720) else "no"
    return {"cedula": cedula, "score": score, "nivel_riesgo": nivel,
            "aplica": aplica, "raw": body}


def _parse_cached(aplica_cell: str, meta_cell: str) -> ScoreResult:
    aplica = (aplica_cell or "").strip().lower()
    if aplica in ("si", "sí"):
        aplica = "si"
    elif aplica == "no":
        aplica = "no"
    elif aplica:
        aplica = aplica  # respect whatever is there (error, etc)
    else:
        return {"cedula": "", "aplica": "pending"}

    meta: dict = {}
    if meta_cell:
        try:
            meta = json.loads(meta_cell)
        except Exception:
            meta = {"_raw_cell": meta_cell}
    score = meta.get("score") or meta.get("Score")
    try:
        score = int(score) if score is not None else None
    except (TypeError, ValueError):
        score = None
    return {
        "cedula": meta.get("cedula", ""),
        "score": score,
        "nivel_riesgo": meta.get("nivel_riesgo") or meta.get("Nivel Riesgo"),
        "aplica": aplica,
        "raw": meta,
    }


def enrich_leads_with_scores(
    df_leads: pd.DataFrame,
    *,
    tab: str = "Leads",
    cedula_col: str = "cedula",
    sheet_id: str | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Devuelve (df_enriquecido, stats) donde df tiene columnas score, aplica, nivel_riesgo.

    Comportamiento:
      - Si el Sheet ya tiene Aplica/Metadata para una cédula → usa el cache.
      - Si Aplica está vacía y el API está configurado → consulta + escribe back.
      - Si no está configurado → aplica='pending' para las cédulas sin cache.
    """
    df = df_leads.copy()
    df[cedula_col] = df[cedula_col].astype(str).str.strip()

    has_aplica = APLICA_COL in df.columns
    has_meta = META_COL in df.columns

    # asegura columnas en el sheet (idempotente)
    try:
        gsheets.ensure_columns(tab, [APLICA_COL, META_COL], sheet_id=sheet_id)
    except Exception:
        pass  # write puede fallar si el service account no tiene Editor; lo manejamos abajo

    results: list[ScoreResult] = []
    to_write: list[dict] = []
    n_cached = n_consulted = n_pending = n_errors = 0

    for _, row in df.iterrows():
        cedula = str(row.get(cedula_col, "")).strip()
        cached_aplica = str(row.get(APLICA_COL, "") or "").strip() if has_aplica else ""
        cached_meta = str(row.get(META_COL, "") or "").strip() if has_meta else ""

        if cached_aplica:
            res = _parse_cached(cached_aplica, cached_meta)
            res["cedula"] = cedula
            results.append(res)
            n_cached += 1
            continue

        if not cedula:
            results.append({"cedula": "", "aplica": "error"})
            n_errors += 1
            continue

        res = _consultar_api(cedula)
        results.append(res)
        if res["aplica"] == "pending":
            n_pending += 1
        elif res["aplica"] == "error":
            n_errors += 1
        else:
            n_consulted += 1
            to_write.append({
                cedula_col: cedula,
                APLICA_COL: res["aplica"],
                META_COL: json.dumps(res.get("raw") or {}, ensure_ascii=False),
            })

    # Persistir lo nuevo
    n_written = 0
    write_error: str | None = None
    if to_write:
        try:
            n_written = gsheets.update_rows_by_key(
                tab, cedula_col, to_write, sheet_id=sheet_id
            )
        except Exception as exc:
            write_error = f"{type(exc).__name__}: {exc}"

    df["score"] = [r.get("score") for r in results]
    df["nivel_riesgo"] = [r.get("nivel_riesgo") for r in results]
    df["aplica"] = [r.get("aplica", "pending") for r in results]

    stats = {
        "cached": n_cached,
        "consulted": n_consulted,
        "pending": n_pending,
        "errors": n_errors,
        "written": n_written,
        "write_error": write_error,
        "api_configured": is_configured(),
    }
    return df, stats
