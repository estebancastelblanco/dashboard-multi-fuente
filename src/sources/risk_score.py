"""Consulta al score-api de Habi Capital.

Si `SCORE_API_URL` y `SCORE_API_TOKEN` están en env, hace la llamada real.
Si no, devuelve `pending` para todas las cédulas.
"""
from __future__ import annotations

import os
from typing import TypedDict

import pandas as pd
import requests


class ScoreResult(TypedDict, total=False):
    cedula: str
    score: int | None
    nivel_riesgo: str | None
    aplica: str  # "si" | "no" | "error" | "pending"
    motivo: str | None


def _is_configured() -> bool:
    return bool(os.environ.get("SCORE_API_URL") and os.environ.get("SCORE_API_TOKEN"))


def consultar(cedula: str) -> ScoreResult:
    """Consulta una sola cédula. No lanza excepción — encapsula errores en aplica='error'."""
    cedula = str(cedula).strip()
    if not cedula:
        return {"cedula": cedula, "aplica": "error", "motivo": "cedula vacia"}
    if not _is_configured():
        return {"cedula": cedula, "aplica": "pending",
                "motivo": "configurar SCORE_API_URL y SCORE_API_TOKEN"}
    url = os.environ["SCORE_API_URL"].rstrip("/") + f"/{cedula}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {os.environ['SCORE_API_TOKEN']}"},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        score = body.get("score")
        return {
            "cedula": cedula,
            "score": int(score) if score is not None else None,
            "nivel_riesgo": body.get("nivel") or body.get("risk_level"),
            "aplica": "si" if (score and int(score) >= 720) else "no",
            "motivo": body.get("estado") or body.get("status"),
        }
    except Exception as exc:
        return {"cedula": cedula, "aplica": "error", "motivo": f"{type(exc).__name__}: {exc}"}


def consultar_batch(cedulas: list[str]) -> pd.DataFrame:
    return pd.DataFrame([consultar(c) for c in cedulas])


def is_configured() -> bool:
    return _is_configured()
