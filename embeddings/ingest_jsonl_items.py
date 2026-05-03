"""API-style ingestion helpers for embedding JSONL items into Supabase pgvector."""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any, Dict, List, Optional

from openai import OpenAI

try:
    from supabase_store import SupabaseStore
except ImportError:  # pragma: no cover - supports package-style imports.
    from .supabase_store import SupabaseStore


EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
IMAGE_URL_KEYS = ("Image_url", "image_url", "imageUrl")


def create_supabase_store_from_env() -> SupabaseStore:
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supabase_url or not service_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    return SupabaseStore(supabase_url, service_key)


def require_instagram_account_id(instagram_account_id: Optional[str] = None) -> str:
    raw = (instagram_account_id or os.getenv("INSTAGRAM_ACCOUNT_ID", "")).strip()
    if not raw:
        raise RuntimeError("INSTAGRAM_ACCOUNT_ID is required")
    try:
        return str(uuid.UUID(raw))
    except ValueError as exc:
        raise RuntimeError("INSTAGRAM_ACCOUNT_ID must be a valid UUID") from exc


def require_chunk_id(chunk_id: Any) -> str:
    raw = str(chunk_id or "").strip()
    if not raw:
        raise RuntimeError("chunk id is required")
    try:
        return str(uuid.UUID(raw))
    except ValueError as exc:
        raise RuntimeError("chunk id must be a valid UUID") from exc


def embed_texts(client: OpenAI, texts: List[str]) -> List[List[float]]:
    resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
    out: List[List[float]] = [list(map(float, d.embedding)) for d in resp.data]
    for vec in out:
        if len(vec) != EMBED_DIM:
            raise RuntimeError(f"Unexpected embedding dim {len(vec)} (expected {EMBED_DIM})")
    return out


def extract_image_url(meta: Dict[str, Any]) -> Optional[str]:
    for key in IMAGE_URL_KEYS:
        value = meta.get(key)
        if value is None:
            continue
        raw = str(value).strip()
        if raw:
            return raw
    return None


def split_extra_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    keep = {"type", "source_url", "page_path", "title", "meta_description", *IMAGE_URL_KEYS}
    extra = {k: v for k, v in meta.items() if k not in keep}
    image_url = extract_image_url(meta)
    if image_url:
        extra["Image_url"] = image_url
    return extra


def _build_row(item: Dict[str, Any], embedding: List[float], instagram_account_id: str) -> Dict[str, Any]:
    md = item.get("metadata", {}) or {}
    text = item["text"]
    return {
        "id": require_chunk_id(item["id"]),
        "instagram_account_id": instagram_account_id,
        "text": text,
        "type": md.get("type"),
        "source_url": md.get("source_url"),
        "page_path": md.get("page_path"),
        "title": md.get("title"),
        "meta_description": md.get("meta_description"),
        "extra_metadata": split_extra_metadata(md),
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "embedding": embedding,
    }


def ingest_jsonl_items(
    items: List[Dict[str, Any]],
    *,
    client: Optional[OpenAI] = None,
    store: Optional[SupabaseStore] = None,
    instagram_account_id: Optional[str] = None,
    batch_size: int = 128,
) -> List[Dict[str, Any]]:
    """Embed and upsert a list of JSONL objects into Supabase knowledge_chunks."""
    if batch_size <= 0:
        raise RuntimeError("batch_size must be greater than 0")

    local_client = client or OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    local_store = store or create_supabase_store_from_env()
    account_id = require_instagram_account_id(instagram_account_id)

    results: List[Dict[str, Any]] = []
    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        for item in batch:
            if "id" not in item:
                raise RuntimeError("JSONL item must include 'id'")
            if "text" not in item:
                raise RuntimeError("JSONL item must include 'text'")

        texts = [item["text"] for item in batch]
        embeddings = embed_texts(local_client, texts)
        rows = [_build_row(item, emb, instagram_account_id=account_id) for item, emb in zip(batch, embeddings)]
        local_store.upsert_knowledge_chunks(rows)

        for row in rows:
            results.append(
                {
                    "id": row["id"],
                    "instagram_account_id": account_id,
                    "content_hash": row["content_hash"],
                }
            )

    return results


def ingest_jsonl_item(
    item: Dict[str, Any],
    *,
    client: Optional[OpenAI] = None,
    store: Optional[SupabaseStore] = None,
    instagram_account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Backward-compatible single-item wrapper."""
    return ingest_jsonl_items(
        [item],
        client=client,
        store=store,
        instagram_account_id=instagram_account_id,
        batch_size=1,
    )[0]


# Backward-compatible typo aliases.
injest_jsonl_items = ingest_jsonl_items
injest_jsonl_item = ingest_jsonl_item
