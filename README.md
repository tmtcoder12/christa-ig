# Python Meta Webhook for Render

This is a Flask app for receiving Meta webhook requests on Render. It supports webhook verification, replies to inbound Instagram DMs with OpenAI-generated text, can trigger comment-to-DM automations on promotional posts, and can use Supabase `pgvector` knowledge chunks as RAG context.

## Endpoints

- `GET /` returns a healthcheck response.
- `GET /webhook` handles Meta webhook verification.
- `POST /webhook` processes Instagram webhook events and returns a fast `200 OK`.

For inbound text `dm-related` webhook events, the app looks up the connected Instagram account in Supabase, persists the contact/session/messages, retrieves relevant `knowledge_chunks` for that Instagram account, generates a reply with OpenAI from database-backed chat history plus RAG context, stores the assistant reply, and sends the reply back to the message sender.

For `comment-related` webhook events, promotional posts can be configured with trigger keywords. When a matching comment arrives, the app stores the comment, issues or reuses a unique promo code for that customer/post, sends a static public comment reply, retrieves relevant knowledge chunks, generates a private reply DM with OpenAI, and sends it using Meta's comment private-reply flow.

## Environment variables

- `META_VERIFY_TOKEN` is the verify token you will also enter in the Meta developer dashboard.
- `INSTAGRAM_ACCESS_TOKEN` is the access token used to send Instagram DM replies through the Meta Graph API.
- `SUPABASE_URL` is your Supabase project URL.
- `SUPABASE_SERVICE_ROLE_KEY` is used by the backend webhook to insert and update tenant data. Keep this server-side only.
- `OPENAI_API_KEY` is used to authenticate with OpenAI.
- `OPENAI_MODEL` optionally overrides the default OpenAI model.
- `OPENAI_EMBEDDING_MODEL` optionally overrides the embedding model used for RAG queries. Defaults to `text-embedding-3-small`.
- `OPENAI_SYSTEM_PROMPT` optionally overrides the default general assistant prompt when no account-specific prompt is provided.
- `OPENAI_FALLBACK_MESSAGE` optionally overrides the fallback reply used when OpenAI fails.
- `RAG_ENABLED` optionally enables or disables knowledge retrieval. Defaults to `true`.
- `RAG_MATCH_COUNT` optionally sets how many knowledge chunks are sent to OpenAI. Defaults to `5`.
- `PORT` is provided by Render automatically.

## Supabase setup

Run the schema in `database/schema.sql` in your Supabase SQL editor or with `psql`. The schema enables `pgcrypto` and `vector`, creates the Instagram/DM/comment tables, creates `knowledge_chunks`, and defines the `match_knowledge_chunks(...)` RPC used by RAG.

Before the webhook can persist a DM, the receiving Instagram account must exist in `instagram_accounts`. In Meta DM webhooks, `entry.id` is your business Instagram account ID and `messaging[].sender.id` is the contact. For example, with this payload:

```json
{
  "entry": [
    {
      "id": "17841476354816630",
      "messaging": [
        {
          "sender": {
            "id": "25391124670525123"
          }
        }
      ]
    }
  ]
}
```

Seed `instagram_accounts.instagram_user_id` with `17841476354816630`. Set `instagram_accounts.system_prompt` to control the assistant instructions for that Instagram account. The webhook will create or update the `ig_contacts` row for `25391124670525123`, then create the DM session and messages.

For RAG, insert embeddings into `knowledge_chunks` with the internal `instagram_accounts.id` value in `knowledge_chunks.instagram_account_id`. The app embeds the latest inbound DM or comment-trigger query, calls `match_knowledge_chunks(...)`, and adds the retrieved text as business knowledge when generating the reply.

If `SUPABASE_URL` or `SUPABASE_SERVICE_ROLE_KEY` is missing, local development falls back to the old in-memory history behavior.

## Embedding ingestion

The `embeddings/` folder contains a CLI for bulk-loading standardized JSONL data into `knowledge_chunks`.

Create a `.env` file with:

```env
OPENAI_API_KEY=your-openai-key
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
INSTAGRAM_ACCOUNT_ID=your-internal-instagram-account-uuid
INGEST_SOURCE_NAME=kosoo-chunks.jsonl
INGEST_TRACK_RUNS=true
```

Then run:

```bash
python3 embeddings/embed-to-db.py embeddings/businessData/kosoo-chunks.jsonl 32
```

`INGEST_TRACK_RUNS=true` records the import in `ingest_runs`.

## Promotional comment automation

To enable comment-to-DM automation for a post, mark an `ig_posts` row as promotional and set trigger keywords:

```sql
update public.ig_posts
set
  post_type = 'promotional',
  automation_enabled = true,
  trigger_keywords = '["DM", "Test"]'::jsonb,
  comment_reply_text = 'Check DMs',
  dm_prompt = 'Send a friendly private reply about this promotion.',
  promotion_metadata = '{"code_prefix": "KOSOO"}'::jsonb
where instagram_media_id = 'YOUR_INSTAGRAM_MEDIA_ID';
```

Automation is limited to one attempted DM per `post_id` and `contact_id`. The app stores one readable promo code per customer/post in `ig_promo_codes` and includes that exact code in the private reply DM. If `promotion_metadata.code_prefix` is not set, codes use the `PROMO` prefix.

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Then send a test request:

```bash
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "object": "instagram",
    "entry": [
      {
        "time": 1777318396685,
        "id": "17841476354816630",
        "messaging": [
          {
            "sender": {
              "id": "25391124670525123"
            },
            "recipient": {
              "id": "17841476354816630"
            },
            "timestamp": 1777318396235,
            "message": {
              "mid": "local-test-message-1",
              "text": "Qwerty"
            }
          }
        ]
      }
    ]
  }'
```

Test Meta verification locally:

```bash
curl "http://localhost:5000/webhook?hub.mode=subscribe&hub.verify_token=your-token&hub.challenge=12345"
```

## Deploy on Render

1. Push this folder to GitHub.
2. In Render, create a new Web Service from that repo.
3. Render should detect `render.yaml` automatically.
4. After deploy, your webhook URL will be:

```text
https://your-render-service.onrender.com/webhook
```

In the Meta developer dashboard:

1. Set the callback URL to your Render `/webhook` URL.
2. Set the verify token to the same value as `META_VERIFY_TOKEN`.
3. Complete webhook verification.

## View console output

Open your service in Render and check the **Logs** tab to see:

- verification attempts
- detected event type (`comment-related`, `dm-related`, or `unknown`)
- processing results such as `replied`, `fallback_sent`, `duplicate_dm_ignored`, `instagram_account_not_configured`, `skipped_echo`, or `skipped_read_receipt`
- comment automation results such as `comment_automation_sent`, `comment_no_keyword_match`, `comment_duplicate_automation`, `comment_public_reply_failed`, or `comment_private_reply_failed`
- full webhook payloads
- the Supabase IDs for the business, Instagram account, contact, session, and messages
- the OpenAI generation result, including how many RAG knowledge chunks were included
- the send-message API response when a reply is attempted
