# Embedding ingestion technical guide

This folder contains a command-line tool for loading business knowledge into Supabase in bulk.

The main app can add one knowledge chunk at a time through the dashboard. This tool is intended for larger prepared datasets such as product catalogs, service lists, menus, policies, FAQs, and website content.

## Files

- `embed-to-db.py`: command-line entry point and JSONL loader
- `ingest_jsonl_items.py`: validation, batching, metadata mapping, and OpenAI embedding logic
- `supabase_store.py`: PostgREST writes to `knowledge_chunks` and `ingest_runs`
- `requirements.txt`: Python dependencies for this tool

## Data flow

```text
JSONL file
   ↓
Validate item IDs and text
   ↓
Send text batches to OpenAI Embeddings
   ↓
Build 1,536-value vector rows and metadata
   ↓
Upsert into Supabase knowledge_chunks by ID
   ↓
Record success or error in ingest_runs
```

Each run belongs to one internal `instagram_accounts.id`. This separates the knowledge used by different accounts and businesses.

## Requirements

- Python 3.12
- The migrated Supabase schema, or the fresh-install snapshot at `backend/database/schema.sql`
- A valid row in `instagram_accounts`
- An OpenAI API key
- A Supabase service-role key

Install the dependencies from the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r embeddings/requirements.txt
```

If the same virtual environment will also run the backend, install both requirement files.

## Environment variables

Copy the tracked placeholder file, then fill in its values:

```bash
cp .env.example .env
```

The file contains:

```env
OPENAI_API_KEY=your-openai-key
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
INSTAGRAM_ACCOUNT_ID=your-internal-instagram-account-uuid
INGEST_SOURCE_NAME=business-knowledge.jsonl
INGEST_TRACK_RUNS=true
```

Required:

- `OPENAI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `INSTAGRAM_ACCOUNT_ID`

`INSTAGRAM_ACCOUNT_ID` is the UUID from `public.instagram_accounts.id`. It is not the public Instagram user ID or username.

Optional:

- `INGEST_SOURCE_NAME`: label stored with the run; defaults to `chunks.jsonl`
- `INGEST_TRACK_RUNS`: records progress in `ingest_runs`; defaults to `true`

Keep the service-role key private. It bypasses Supabase row-level security.

## JSONL input format

JSONL contains one JSON object per line. Blank lines are ignored.

Every object requires:

- `id`: a UUID string
- `text`: the text that will be embedded and later supplied to the AI

The optional `metadata` object can contain:

- `type`
- `source_url`
- `page_path`
- `title`
- `meta_description`
- `Image_url`, `image_url`, or `imageUrl`
- Any additional custom fields

Example:

```json
{"id":"376110e9-de5a-4707-b8ee-e8a5403eacab","text":"Premium members can book classes seven days in advance.","metadata":{"type":"membership_policy","category":"booking","title":"Advance booking","source_url":"https://example.com/memberships"}}
{"id":"6d213c1e-cf1a-4388-ad7d-12c849337969","text":"The blue jacket is available in sizes XS through XL.","metadata":{"type":"product","category":"outerwear","title":"Blue jacket"}}
```

Standard metadata fields receive their own database columns. Other fields are stored in `extra_metadata`. Image URL spellings are normalized into `extra_metadata.Image_url`.

The tool also stores a SHA-256 hash of the text in `content_hash`.

## Run the importer

From the repository root:

```bash
python embeddings/embed-to-db.py path/to/business-knowledge.jsonl
```

Pass a second argument to change the batch size:

```bash
python embeddings/embed-to-db.py path/to/business-knowledge.jsonl 32
```

The default batch size is `128`. Each batch is sent to the OpenAI Embeddings API in one request.

`embed-to-db.py` calls `load_dotenv()` and searches for a nearby `.env` file. Run the command from the repository root so it loads the root file consistently.

Database changes are versioned under `supabase/migrations/`. For a local database, replay them before importing:

```bash
supabase db reset
```

## Embedding model and database dimensions

The importer currently uses a fixed model:

```text
text-embedding-3-small
```

It verifies that every returned embedding contains exactly 1,536 numbers. This matches the `vector(1536)` column in `backend/database/schema.sql`.

Changing the model to one with a different vector size also requires a database migration and an update to `EMBED_DIM`.

## Upsert and retry behavior

Rows are upserted on the chunk `id`:

- A new ID inserts a chunk.
- An existing ID updates that chunk.
- Reusing stable IDs makes an import repeatable.

The importer can finish some batches before a later batch fails. Those earlier rows remain in Supabase. It is safe to correct the input and run it again because rows use deterministic IDs.

When run tracking is enabled, the tool creates an `ingest_runs` row with `running` status and later changes it to `success` or `error`. Run tracking describes the overall attempt; the database may still contain rows from completed batches after an error.

## Using the ingestion functions from Python

`ingest_jsonl_items` can also be called directly:

```python
from ingest_jsonl_items import ingest_jsonl_items

results = ingest_jsonl_items(
    items=[
        {
            "id": "376110e9-de5a-4707-b8ee-e8a5403eacab",
            "text": "Premium members can book classes seven days in advance.",
            "metadata": {
                "type": "membership_policy",
                "category": "booking",
            },
        }
    ],
    instagram_account_id="internal-instagram-account-uuid",
    batch_size=32,
)
```

The return value contains the chunk ID, account ID, and content hash for each processed item.

Optional `client` and `store` arguments allow callers or tests to supply their own OpenAI client and `SupabaseStore`.

## How the main app uses these rows

When a DM, qualifying comment, or SMS reply arrives, the backend:

1. Embeds the customer's text.
2. Calls the `match_knowledge_chunks` database function.
3. Filters by the current Instagram account.
4. Selects the closest chunks using cosine similarity.
5. Adds their text and source information to the OpenAI prompt.

Good chunks should be short, specific, understandable without surrounding text, and limited to one fact or closely related group of facts.

## Common errors

- `INSTAGRAM_ACCOUNT_ID must be a valid UUID`: use `instagram_accounts.id`, not the Meta ID.
- Foreign-key failure: create the Instagram account row before importing.
- Unexpected embedding dimension: the OpenAI model no longer matches the database vector size.
- Supabase `401` or `403`: check the URL and service-role key.
- Duplicate or overwritten content: make sure unrelated chunks do not reuse the same UUID.
- Poor retrieval: split broad documents into smaller, focused chunks and put the useful wording in `text`.
