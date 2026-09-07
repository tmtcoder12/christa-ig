"""CLI: embed chunk text from JSONL and upsert vectors into Supabase pgvector."""

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from ingest_jsonl_items import (
    EMBED_MODEL,
    create_supabase_store_from_env,
    ingest_jsonl_items,
    require_instagram_account_id,
)
from openai import OpenAI


def parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_jsonl(path: str) -> List[Dict[str, Any]]:
    items = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def supabase_mode(chunks: List[Dict[str, Any]], client: OpenAI, batch_size: int):
    store = create_supabase_store_from_env()
    instagram_account_id = require_instagram_account_id()
    source_name = os.getenv("INGEST_SOURCE_NAME", "chunks.jsonl")
    track_runs = parse_bool_env("INGEST_TRACK_RUNS", True)

    run_id = None
    embedded = 0
    if track_runs:
        run_id = store.begin_ingest_run(
            instagram_account_id=instagram_account_id,
            model=EMBED_MODEL,
            source_name=source_name,
            total_chunks=len(chunks),
        )

    try:
        results = ingest_jsonl_items(
            chunks,
            client=client,
            store=store,
            instagram_account_id=instagram_account_id,
            batch_size=batch_size,
        )
        embedded = len(results)

        if track_runs:
            store.finish_ingest_run(run_id, status="success", embedded_chunks=embedded)
        print(f"Upserted {embedded} chunks into Supabase knowledge_chunks for Instagram account {instagram_account_id}")
    except Exception as exc:  # noqa: BLE001
        if track_runs:
            store.finish_ingest_run(run_id, status="error", embedded_chunks=embedded, error_message=str(exc))
        raise


def main(
    input_jsonl: str,
    batch_size: int = 128,
):
    load_dotenv()
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    chunks = load_jsonl(input_jsonl)
    supabase_mode(chunks, client, batch_size=batch_size)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python embed-to-db.py chunks.jsonl [batch_size]")
        print("Requires INSTAGRAM_ACCOUNT_ID=<uuid>, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, OPENAI_API_KEY.")
        raise SystemExit(1)

    input_jsonl = sys.argv[1]
    batch_size = int(sys.argv[2]) if len(sys.argv) >= 3 else 128
    main(input_jsonl, batch_size)
