"""Clasificar motivos de venta de los leads del FakeDoor con OpenAI.

Universo: nids que abrieron la landing del FakeDoor (habicapitalliquidez.vercel.app)
∩ tienen motivo_venta_string en seller_digital_co_recepcionista_mm.

Para cada motivo, el LLM asigna 1 categoría y marca si es candidato al
crédito de libre inversión con garantía hipotecaria.

Uso:
    OPENAI_API_KEY=sk-... python scripts/classify_motivos_llm.py

Output: data/categorias_llm_fakedoor.csv
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

# Bootstrap env
ROOT = Path(__file__).resolve().parents[1]
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(ROOT))

from openai import OpenAI

from src.sources import bigquery as bq_src

OUT_CSV = ROOT / "data" / "categorias_llm_fakedoor.csv"
MODEL = "gpt-4o-mini"

# Categorías candidatas (etiqueta + es_candidato al crédito de libre inversión)
# El crédito requiere que el cliente NO quiera deshacerse del inmueble — lo usa
# como garantía. Categorías marcadas como candidato son las que SUGIEREN que
# el cliente podría conservar el inmueble y tomar el crédito.
CATEGORIES = [
    ("Necesita liquidez urgente",     True,  "Deudas, gastos urgentes, problemas de plata sin querer vender"),
    ("Quiere invertir en otro negocio", True, "Capital para emprender, comprar bienes no-inmobiliarios, oportunidad"),
    ("Quiere comprar otro inmueble",   False, "Quiere mudarse o invertir en otro inmueble — necesita liquidar"),
    ("Mudanza / cambio de ciudad",     False, "Se va a vivir a otro lado, no sigue con el inmueble"),
    ("Separación / divorcio",          False, "División de bienes, no van a conservar el inmueble"),
    ("Cambio personal o familiar",     False, "Salud, familia que creció, mascotas, problemas con el inmueble"),
    ("Cambio de vivienda",             False, "Más grande, otra zona, cambio de barrio dentro de la misma ciudad"),
    ("Viaje / vida fuera del país",    False, "Trasladarse al exterior, no conserva el inmueble"),
    ("Otro / no claro",                False, "Motivo ambiguo o sin información suficiente"),
]

CATS_LABELS = [c[0] for c in CATEGORIES]
CATS_IS_CANDIDATE = {c[0]: c[1] for c in CATEGORIES}

PROMPT_SYSTEM = (
    "Eres un analista de Habi Capital. Clasificas motivos de venta de inmuebles "
    "para decidir si el cliente podría tomar un crédito de libre inversión con "
    "garantía hipotecaria (= conservar el inmueble como garantía, NO venderlo).\n\n"
    "Categorías disponibles (responde con una EXACTA de esta lista):\n"
    + "\n".join(f"- {label}: {desc}" for label, _, desc in CATEGORIES)
    + "\n\nResponde SOLO un JSON con esta estructura:\n"
    + '{"categoria": "<una de la lista>", "razon": "<frase corta explicando>"}'
)


def _classify_one(client: OpenAI, nid: int, motivo: str) -> dict:
    """Llama OpenAI y devuelve dict con nid, motivo, categoria, candidato, razon."""
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": f"Motivo: {motivo}"},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=200,
        )
        body = json.loads(resp.choices[0].message.content)
        cat = body.get("categoria", "Otro / no claro")
        if cat not in CATS_IS_CANDIDATE:
            cat = "Otro / no claro"
        return {
            "nid": nid,
            "motivo_original": motivo,
            "categoria_llm": cat,
            "candidato_credito": CATS_IS_CANDIDATE[cat],
            "razon_llm": body.get("razon", ""),
        }
    except Exception as exc:
        return {
            "nid": nid,
            "motivo_original": motivo,
            "categoria_llm": "Otro / no claro",
            "candidato_credito": False,
            "razon_llm": f"ERROR: {type(exc).__name__}: {exc}",
        }


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY no está en env.")

    bq = bq_src._client()
    print("Cargando nids del FakeDoor con motivos...")
    sql = r"""
    WITH abrieron AS (
      SELECT DISTINCT REGEXP_EXTRACT(context_page_url, r'([0-9a-fA-F\-]{36})') AS uuid
      FROM `sellers-main-prod.javascript9.pages`
      WHERE context_page_url LIKE 'https://habicapitalliquidez.vercel.app/%'
    ),
    deals AS (
      SELECT CAST(nid AS INT64) AS nid, LOWER(deal_uuid) AS uuid
      FROM `sellers-main-prod.hubspot.deals`
      WHERE deal_uuid IS NOT NULL AND nid IS NOT NULL
    ),
    nids_abrieron AS (
      SELECT DISTINCT d.nid FROM deals d JOIN abrieron a ON a.uuid = d.uuid
    ),
    motivos AS (
      SELECT CAST(nid AS INT64) AS nid,
             STRING_AGG(DISTINCT motivo_venta_string, ' | ') AS motivo
      FROM `sellers-main-prod.mid_funnel_ibuyer.seller_digital_co_recepcionista_mm`
      WHERE motivo_venta_string IS NOT NULL
      GROUP BY nid
    )
    SELECT m.nid, m.motivo
    FROM motivos m
    INNER JOIN nids_abrieron a ON a.nid = m.nid
    """
    df = bq.query(sql).to_dataframe()
    print(f"  {len(df)} nids para clasificar")

    if df.empty:
        sys.exit("Sin nids para procesar.")

    client = OpenAI()
    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_classify_one, client, int(row["nid"]), str(row["motivo"])): row
            for _, row in df.iterrows()
        }
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            results.append(r)
            if i % 5 == 0 or i == len(futures):
                print(f"  {i}/{len(futures)}")

    out = pd.DataFrame(results).sort_values("nid").reset_index(drop=True)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV, index=False)
    print(f"\nGuardado: {OUT_CSV} ({len(out)} filas)")
    print("\nDistribución por categoría:")
    print(out["categoria_llm"].value_counts().to_string())
    print(f"\nCandidatos al crédito: {out['candidato_credito'].sum()} / {len(out)}")


if __name__ == "__main__":
    main()
