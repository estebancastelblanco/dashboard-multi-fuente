from __future__ import annotations

import base64
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
INPUTS = [
    (Path("/Users/usermac/Hc/ibuyer.xlsx"), "iBuyer"),
    (Path("/Users/usermac/Hc/alianza.xlsx"), "Alianza"),
]
EXPERIAN_CSV = DATA_DIR / "experian_check_executions_2026-05-15.csv"
OUTPUT_CSV = DATA_DIR / "escriturados_2026_age_score.csv"
CACHE_CSV = DATA_DIR / "escriturados_2026_extracted_cache.csv"
API_URL = "https://dtkcy45k7a.execute-api.us-east-2.amazonaws.com/prod/extract-document"


@dataclass
class ExtractResult:
    link: str
    document_number: str | None
    birth_date: str | None
    error: str | None


def norm_doc_id(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return "".join(ch for ch in text if ch.isdigit())


def load_source_rows() -> pd.DataFrame:
    rows: list[dict] = []
    for path, producto in INPUTS:
        df = pd.read_excel(path)
        link_cols = [c for c in df.columns if "Cédula" in c or "cedula" in c or "ciudadania" in c or "ciudadanía" in c]
        for _, row in df.iterrows():
            nid = str(row["Título"]).strip()
            for col in link_cols:
                value = row.get(col)
                if pd.notna(value) and str(value).strip().startswith("http"):
                    rows.append(
                        {
                            "producto": producto,
                            "nid": nid,
                            "source_col": col,
                            "link": str(value).strip(),
                        }
                    )
    return pd.DataFrame(rows).drop_duplicates()


def load_latest_scores() -> pd.DataFrame:
    df = pd.read_csv(
        EXPERIAN_CSV,
        usecols=["document_id", "experian_response.score", "execution_date"],
        dtype={"document_id": str},
    )
    df["document_id_norm"] = df["document_id"].apply(norm_doc_id)
    df["score_crediticio"] = pd.to_numeric(df["experian_response.score"], errors="coerce")
    df["execution_date"] = pd.to_datetime(df["execution_date"], errors="coerce")
    df = df.dropna(subset=["document_id_norm", "score_crediticio"]).copy()
    df = df.sort_values("execution_date").drop_duplicates("document_id_norm", keep="last")
    return df[["document_id_norm", "score_crediticio"]]


def parse_birth_date(value: str | None) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    value = str(value).strip()
    if not value:
        return None
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def compute_age(birth: date | None, as_of: date) -> int | None:
    if birth is None:
        return None
    years = as_of.year - birth.year
    if (as_of.month, as_of.day) < (birth.month, birth.day):
        years -= 1
    return years


def extract_one(session: requests.Session, api_key: str, link: str) -> ExtractResult:
    try:
        pdf_resp = session.get(link, timeout=60)
        pdf_resp.raise_for_status()
        encoded = base64.b64encode(pdf_resp.content).decode("ascii")
        payload = {
            "processor": "dni_colombia",
            "document_type": "cedula_ciudadania",
            "source": {
                "type": "base64",
                "data": encoded,
                "mime_type": "application/pdf",
            },
        }
        api_resp = session.post(
            API_URL,
            headers={"content-type": "application/json", "x-api-key": api_key},
            json=payload,
            timeout=120,
        )
        api_resp.raise_for_status()
        body = api_resp.json()
        extracted = (body.get("data") or {}).get("extracted_data") or {}
        return ExtractResult(
            link=link,
            document_number=norm_doc_id(extracted.get("document_number")),
            birth_date=extracted.get("birth_date"),
            error=None,
        )
    except Exception as exc:
        return ExtractResult(link=link, document_number=None, birth_date=None, error=str(exc))


def save_cache(results: list[ExtractResult]) -> None:
    if not results:
        return
    CACHE_CSV.parent.mkdir(parents=True, exist_ok=True)
    fresh = pd.DataFrame(
        [
            {
                "link": r.link,
                "document_id_norm": r.document_number,
                "birth_date_raw": r.birth_date,
                "extract_error": r.error,
            }
            for r in results
        ]
    )
    if CACHE_CSV.exists():
        old = pd.read_csv(CACHE_CSV)
        fresh = pd.concat([old, fresh], ignore_index=True)
    fresh = fresh.drop_duplicates("link", keep="last")
    fresh.to_csv(CACHE_CSV, index=False)


def main() -> None:
    api_key = os.environ["DOC_EXTRACTOR_API_KEY"]
    source = load_source_rows()
    scores = load_latest_scores()
    links = source["link"].drop_duplicates().tolist()
    cached_links: set[str] = set()
    cached_df = pd.DataFrame(columns=["link", "document_id_norm", "birth_date_raw", "extract_error"])
    if CACHE_CSV.exists():
        cached_df = pd.read_csv(CACHE_CSV)
        cached_links = set(cached_df["link"].dropna().astype(str))
    pending_links = [link for link in links if link not in cached_links]

    results: list[ExtractResult] = []
    if pending_links:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {}
            for link in pending_links:
                session = requests.Session()
                futures[pool.submit(extract_one, session, api_key, link)] = link
            for i, future in enumerate(as_completed(futures), start=1):
                result = future.result()
                results.append(result)
                if i % 50 == 0 or i == len(futures):
                    save_cache(results)
                    results.clear()
                    print(f"processed {i}/{len(futures)}")
        save_cache(results)

    extracted = pd.read_csv(CACHE_CSV) if CACHE_CSV.exists() else cached_df
    if "document_id_norm" in extracted.columns:
        extracted["document_id_norm"] = extracted["document_id_norm"].apply(norm_doc_id)
    merged = source.merge(extracted, on="link", how="left")
    merged["birth_date"] = merged["birth_date_raw"].apply(parse_birth_date)
    today = date.today()
    merged["edad"] = merged["birth_date"].apply(lambda d: compute_age(d, today))
    merged = merged.merge(scores, on="document_id_norm", how="left")

    final = merged[
        ["producto", "nid", "source_col", "edad", "score_crediticio", "document_id_norm", "extract_error"]
    ].copy()
    final = final.dropna(subset=["edad", "score_crediticio"]).copy()
    final["edad"] = final["edad"].astype(int)
    final["score_crediticio"] = pd.to_numeric(final["score_crediticio"], errors="coerce")
    final = final.drop_duplicates(["producto", "nid", "document_id_norm", "edad", "score_crediticio"])
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    final.to_csv(OUTPUT_CSV, index=False)
    print(f"wrote {len(final)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
