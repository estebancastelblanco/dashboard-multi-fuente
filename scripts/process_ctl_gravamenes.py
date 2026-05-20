"""Procesa los CTL de los nids del FakeDoor con la propiedad ctl en HubSpot.

Para cada nid:
1. Descarga el PDF del CTL desde HubSpot (vía Files API)
2. Lo pasa por el CTL extractor (PyMuPDF + parsing)
3. Analiza el field 'anotaciones' para detectar gravámenes:
   - hipoteca / leasing / patrimonio de familia
4. Decide aplica = True si NO tiene ninguno de los 3 gravámenes

Output: data/ctl_gravamenes_fakedoor.csv con columnas
  nid, ctl_id, tiene_hipoteca, tiene_leasing, tiene_patrimonio_familia,
  aplica, anotaciones_raw, error

Uso:
    HUBSPOT_ACCESS_TOKEN=... python scripts/process_ctl_gravamenes.py
"""
from __future__ import annotations

import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
# Bootstrap env desde .env
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

# El extractor del repo vive como sub-paquete dentro de scripts/
CTL_REPO = ROOT / "scripts" / "ctl-reader-service-main"
sys.path.insert(0, str(CTL_REPO / "backend"))
sys.path.insert(0, str(ROOT))

# HubSpot pide HUBSPOT_API_TOKEN, no HUBSPOT_ACCESS_TOKEN
os.environ.setdefault("HUBSPOT_API_TOKEN", os.environ.get("HUBSPOT_ACCESS_TOKEN", ""))

from services.ctl_extractor import CTLExtractor  # noqa: E402
from services.hubspot_service import HubSpotService  # noqa: E402

from src.sources import bigquery as bq_src  # noqa: E402

OUT_CSV = ROOT / "data" / "ctl_gravamenes_fakedoor.csv"

# Regex case-insensitive para gravámenes que invalidan el crédito
GRAVAMEN_PATTERNS = {
    "tiene_hipoteca": re.compile(r"\bhipotec[ao]?\b", re.I),
    "tiene_leasing": re.compile(r"\bleasing\b", re.I),
    "tiene_patrimonio_familia": re.compile(
        r"\bpatrimonio\s+(?:de\s+)?familia\b|\bpatrimonio\s+familiar\b", re.I,
    ),
}


def _classify_anotaciones(anotaciones: str) -> dict:
    """Detecta gravámenes en el texto de anotaciones y decide si aplica."""
    if not anotaciones:
        return {
            "tiene_hipoteca": False,
            "tiene_leasing": False,
            "tiene_patrimonio_familia": False,
            "aplica": True,
        }
    flags = {
        name: bool(pattern.search(anotaciones))
        for name, pattern in GRAVAMEN_PATTERNS.items()
    }
    flags["aplica"] = not any(flags.values())
    return flags


def _assessments_to_text(extracted: dict) -> str:
    """Concatena los assessments del extractor a un solo string buscable.

    `assessments` es una lista de dicts. Los dicts pueden tener 'found_data'
    (el match), 'specifications' (detalle textual de la anotación), o ser
    objetos planos. Concatenamos todo lo textual para buscar gravámenes.
    """
    parts = []
    for a in extracted.get("assessments", []) or []:
        if isinstance(a, dict):
            if a.get("found_data"):
                parts.append(str(a["found_data"]))
            for spec in a.get("specifications", []) or []:
                if isinstance(spec, dict):
                    detail = spec.get("annotation_detail") or spec.get("detail")
                    if detail:
                        parts.append(str(detail))
                else:
                    parts.append(str(spec))
        else:
            parts.append(str(a))
    return " | ".join(parts)


def _process_nid(extractor, hubspot, nid: int, ctl: str) -> dict:
    """Descarga CTL del nid y extrae gravámenes."""
    try:
        # download_ctl_by_nid retorna (pdf_bytes, file_name, deal_data) o None
        result = hubspot.download_ctl_by_nid(str(nid))
        if not result:
            return {
                "nid": nid, "ctl_id": ctl, "error": "no_pdf_found",
                "aplica": False, "tiene_hipoteca": None, "tiene_leasing": None,
                "tiene_patrimonio_familia": None, "anotaciones_raw": "",
            }
        pdf_bytes, file_name, _ = result
        extracted = extractor.extract_from_bytes(pdf_bytes)
        if not extracted.get("success", True) and extracted.get("error"):
            return {
                "nid": nid, "ctl_id": ctl,
                "error": f"extract_error: {extracted.get('error')}",
                "aplica": False, "tiene_hipoteca": None, "tiene_leasing": None,
                "tiene_patrimonio_familia": None, "anotaciones_raw": "",
            }
        anotaciones = _assessments_to_text(extracted)
        flags = _classify_anotaciones(anotaciones)
        return {
            "nid": nid,
            "ctl_id": ctl,
            "anotaciones_raw": anotaciones[:5000],  # truncar para CSV manejable
            "error": "",
            **flags,
        }
    except Exception as exc:
        return {
            "nid": nid, "ctl_id": ctl, "error": f"{type(exc).__name__}: {exc}",
            "aplica": False, "tiene_hipoteca": None, "tiene_leasing": None,
            "tiene_patrimonio_familia": None, "anotaciones_raw": "",
        }


def main() -> None:
    if not os.environ.get("HUBSPOT_API_TOKEN"):
        sys.exit("HUBSPOT_API_TOKEN no está en env (set HUBSPOT_ACCESS_TOKEN o HUBSPOT_API_TOKEN).")

    bq = bq_src._client()
    print("Cargando nids del FakeDoor (AH/BH) con ctl asignado...")
    sql = """
    SELECT CAST(nid AS INT64) AS nid, ctl
    FROM `sellers-main-prod.hubspot.deals`
    WHERE ab_test_landing IN ('AH','BH')
      AND ctl IS NOT NULL
      AND nid IS NOT NULL
    """
    df = bq.query(sql).to_dataframe()
    print(f"  {len(df)} nids para procesar")

    if df.empty:
        sys.exit("Sin nids con CTL.")

    extractor = CTLExtractor()
    hubspot = HubSpotService()

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(_process_nid, extractor, hubspot, int(row["nid"]), str(row["ctl"])): row
            for _, row in df.iterrows()
        }
        for i, fut in enumerate(as_completed(futures), 1):
            results.append(fut.result())
            if i % 10 == 0 or i == len(futures):
                print(f"  {i}/{len(futures)}")

    out = pd.DataFrame(results).sort_values("nid").reset_index(drop=True)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV, index=False)
    print(f"\nGuardado: {OUT_CSV} ({len(out)} filas)")
    print()
    print("Distribución de gravámenes:")
    print(f"  Con hipoteca:           {out['tiene_hipoteca'].sum()}")
    print(f"  Con leasing:            {out['tiene_leasing'].sum()}")
    print(f"  Con patrimonio familia: {out['tiene_patrimonio_familia'].sum()}")
    print(f"  Aplican (sin gravamen): {out['aplica'].sum()}")
    print(f"  Errores:                {(out['error'].astype(str) != '').sum()}")


if __name__ == "__main__":
    main()
