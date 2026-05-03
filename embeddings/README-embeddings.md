# Embedding Ingestion

Embed JSONL content with OpenAI and upsert the vectors into Supabase `knowledge_chunks`.

## Files

- `embed-to-db.py`: CLI for batch JSONL ingestion.
- `ingest_jsonl_items.py`: reusable list/single item ingestion helpers.
- `supabase_store.py`: small PostgREST adapter for `knowledge_chunks` and `ingest_runs`.
- `businessData/kosoo-chunks.jsonl`: example standardized JSONL dataset.

## Environment Variables

Required:

- `OPENAI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `INSTAGRAM_ACCOUNT_ID`

`INSTAGRAM_ACCOUNT_ID` is the internal UUID from `public.instagram_accounts.id`, not the Meta Instagram user ID.

Optional:

- `INGEST_SOURCE_NAME` defaults to `chunks.jsonl`
- `INGEST_TRACK_RUNS` defaults to `true`

## JSONL Format

Each line must be a JSON object with:

- `id`: UUID string
- `text`: string to embed

Optional:

- `metadata.type`
- `metadata.source_url`
- `metadata.page_path`
- `metadata.title`
- `metadata.meta_description`
- `metadata.Image_url`, `metadata.image_url`, or `metadata.imageUrl`
- any other metadata keys, stored in `knowledge_chunks.extra_metadata`

Example:

```json
{"id":"376110e9-de5a-4707-b8ee-e8a5403eacab","text":"type: All You Can Eat Menu\ncategory: Lunch Menu\nitem_name: Combo A","metadata":{"type":"All You Can Eat Menu","category":"Lunch Menu","item_name":"Combo A","price":26.99}}
```

## CLI Usage

Install dependencies:

```bash
python3 -m pip install -r embeddings/requirements.txt
```

Run ingestion:

```bash
python3 embeddings/embed-to-db.py embeddings/businessData/kosoo-chunks.jsonl
python3 embeddings/embed-to-db.py embeddings/businessData/kosoo-chunks.jsonl 32
```

The optional second argument is `batch_size`, defaulting to `128`.

## API Usage

```python
from ingest_jsonl_items import ingest_jsonl_items

results = ingest_jsonl_items(
    items=[
        {
            "id": "376110e9-de5a-4707-b8ee-e8a5403eacab",
            "text": "Chunk text",
            "metadata": {"type": "menu_item", "title": "Combo A"},
        }
    ],
    instagram_account_id="internal-instagram-account-uuid",
    batch_size=32,
)
```

Return value:

```python
[
  {"id": "...", "instagram_account_id": "...", "content_hash": "..."}
]
```
