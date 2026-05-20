"""Clasificar conversaciones (WhatsApp + llamadas) de los leads del FakeDoor.

Universo: nids con ab_test_landing IN ('AH','BH') ∩ aparecen en la query
del usuario (oportunidad ∈ Cierre - Comprado, No interesado, Rechazó oferta).

Para cada conversación normalizada el LLM asigna 1 categoría y marca si
es candidato al crédito de libre inversión.

Uso:
    OPENAI_API_KEY=sk-... python scripts/classify_conversaciones_llm.py

Output: data/categorias_llm_conversaciones.csv
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
sys.path.insert(0, str(ROOT))

from openai import OpenAI

from src.sources import bigquery as bq_src

OUT_CSV = ROOT / "data" / "categorias_llm_conversaciones.csv"
MODEL = "gpt-4o-mini"
MAX_CHARS = 4000  # truncar conversación a este tamaño para ahorrar tokens

# Mismas categorías que el script de motivos para consistencia.
CATEGORIES = [
    ("Necesita liquidez urgente",     True,  "Deudas, gastos urgentes, problemas de plata sin querer vender"),
    ("Quiere invertir en otro negocio", True, "Capital para emprender, comprar bienes no-inmobiliarios"),
    ("Quiere comprar otro inmueble",   False, "Quiere otro inmueble — necesita liquidar"),
    ("Mudanza / cambio de ciudad",     False, "Se va a vivir a otro lado"),
    ("Separación / divorcio",          False, "División de bienes"),
    ("Cambio personal o familiar",     False, "Salud, familia, mascotas, problemas con el inmueble"),
    ("Cambio de vivienda",             False, "Más grande, otra zona, cambio de barrio"),
    ("Viaje / vida fuera del país",    False, "Trasladarse al exterior"),
    ("Otro / no claro",                False, "Motivo ambiguo o sin información suficiente"),
]
CATS_IS_CANDIDATE = {c[0]: c[1] for c in CATEGORIES}

PROMPT_SYSTEM = (
    "Eres un analista de Habi Capital. Lees conversaciones (WhatsApp + "
    "transcripciones de llamadas) entre el cliente y un agente, y clasificas "
    "el MOTIVO real por el que el cliente quiere o no vender el inmueble.\n\n"
    "Objetivo: decidir si el cliente podría tomar un crédito de libre "
    "inversión con garantía hipotecaria (= conservar el inmueble, NO venderlo).\n\n"
    "Categorías disponibles (responde EXACTA una de esta lista):\n"
    + "\n".join(f"- {label}: {desc}" for label, _, desc in CATEGORIES)
    + "\n\nResponde SOLO un JSON: "
    + '{"categoria": "<una de la lista>", "razon": "<frase corta>"}'
)


# ─────────────────────────────────────────────────────────────────────────────
# Normalización de texto para ahorrar tokens
# ─────────────────────────────────────────────────────────────────────────────
EMOJI_RX = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)
URL_RX = re.compile(r"https?://\S+")
WHITESPACE_RX = re.compile(r"\s+")


def _normalize(text: str) -> str:
    if not text:
        return ""
    # Sustituir emojis y URLs por placeholders cortos
    text = EMOJI_RX.sub("", text)
    text = URL_RX.sub("[url]", text)
    # Normalizar unicode (NFKC) — combina caracteres compuestos
    text = unicodedata.normalize("NFKC", text)
    # Lowercase para ahorrar variantes
    text = text.lower()
    # Colapsar whitespace
    text = WHITESPACE_RX.sub(" ", text).strip()
    return text[:MAX_CHARS]


def _classify_one(client: OpenAI, nid: int, conv: str) -> dict:
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": f"Conversación:\n{conv}"},
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
            "conv_chars": len(conv),
            "categoria_llm": cat,
            "candidato_credito": CATS_IS_CANDIDATE[cat],
            "razon_llm": body.get("razon", ""),
        }
    except Exception as exc:
        return {
            "nid": nid,
            "conv_chars": len(conv),
            "categoria_llm": "Otro / no claro",
            "candidato_credito": False,
            "razon_llm": f"ERROR: {type(exc).__name__}: {exc}",
        }


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY no está en env.")

    bq = bq_src._client()
    print("Cargando conversaciones del FakeDoor con la query del usuario...")
    # Universo: nids del FakeDoor (AH/BH) que tengan conversación (WA o llamada),
    # sin filtrar por oportunidad_del_negocio (= todo el universo del experimento).
    sql = r"""
    WITH llamadas_efectivas AS (
      SELECT CAST(nid AS INT64) AS nid, transcribed_at, transcription
      FROM `sellers-main-prod.hubspot.enriched_transcriptions`
      WHERE call_duration_seconds > 30 AND nid IS NOT NULL
        AND transcription IS NOT NULL
    ),
    mensajes_whatsapp AS (
      SELECT CAST(nid AS INT64) AS nid, message, timestamp_message
      FROM `sellers-main-prod.hubspot.whatsapp_messages`
      WHERE nid IS NOT NULL AND message IS NOT NULL
    ),
    fakedoor AS (
      SELECT DISTINCT CAST(nid AS INT64) AS nid
      FROM `sellers-main-prod.hubspot.deals`
      WHERE ab_test_landing IN ('AH','BH') AND nid IS NOT NULL
    ),
    contactos AS (
      SELECT fd.nid, 'M' AS t, mw.message AS contenido, mw.timestamp_message AS f
      FROM fakedoor fd JOIN mensajes_whatsapp mw ON fd.nid = mw.nid
      UNION ALL
      SELECT fd.nid, 'L' AS t, le.transcription AS contenido, le.transcribed_at AS f
      FROM fakedoor fd JOIN llamadas_efectivas le ON fd.nid = le.nid
    )
    SELECT nid,
           STRING_AGG(CONCAT(t, ': ', contenido), '\n' ORDER BY f) AS conv
    FROM contactos
    WHERE contenido IS NOT NULL
    GROUP BY nid
    """
    df = bq.query(sql).to_dataframe()
    print(f"  {len(df)} nids para clasificar (FakeDoor ∩ query usuario)")

    if df.empty:
        sys.exit("Sin conversaciones para procesar.")

    # Normalizar conversaciones
    df["conv_norm"] = df["conv"].apply(_normalize)
    df = df[df["conv_norm"].astype(bool)]  # quitar vacías

    avg_chars = int(df["conv_norm"].str.len().mean())
    total_chars = int(df["conv_norm"].str.len().sum())
    print(f"  Promedio chars por nid (truncado a {MAX_CHARS}): {avg_chars}")
    print(f"  Total chars a enviar: {total_chars:,}")

    client = OpenAI()
    results = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(_classify_one, client, int(row["nid"]), row["conv_norm"]): row
            for _, row in df.iterrows()
        }
        for i, fut in enumerate(as_completed(futures), 1):
            results.append(fut.result())
            if i % 10 == 0 or i == len(futures):
                print(f"  LLM {i}/{len(futures)}")

    out = pd.DataFrame(results).sort_values("nid").reset_index(drop=True)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV, index=False)
    print(f"\nGuardado: {OUT_CSV} ({len(out)} filas)")
    print("\nDistribución:")
    print(out["categoria_llm"].value_counts().to_string())
    print(f"\nCandidatos al crédito: {out['candidato_credito'].sum()} / {len(out)}")


if __name__ == "__main__":
    main()
