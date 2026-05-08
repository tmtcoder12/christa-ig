# Christa IG Monorepo

This repo is organized as a small monorepo:

- `backend/` contains the Flask webhook/API service, Supabase schema, and backend Python dependencies.
- `frontend-login/` contains the Vite React UI for authenticated business users.
- `embeddings/` contains the CLI flow for embedding JSONL knowledge chunks and upserting them into Supabase.

The backend Flask app receives Meta webhook requests on Render. It supports webhook verification, replies to inbound Instagram DMs with OpenAI-generated text, can trigger comment-to-DM automations on promotional posts from keywords or restaurant-intent comments, can send promo-code and follow-up SMS messages through Twilio, and can use Supabase `pgvector` knowledge chunks as RAG context.

## Endpoints

- `GET /` returns a healthcheck response.
- `GET /webhook` handles Meta webhook verification.
- `POST /webhook` processes Instagram webhook events and returns a fast `200 OK`.

For inbound text `dm-related` webhook events, the app looks up the connected Instagram account in Supabase, persists the contact/session/messages, retrieves relevant `knowledge_chunks` for that Instagram account, generates a reply with OpenAI from database-backed chat history plus RAG context, stores the assistant reply, and sends the reply back to the message sender.

For `comment-related` webhook events, promotional posts can be configured with trigger keywords, restaurant-intent comment classification, or both. When a qualifying comment arrives, the app stores the comment, issues or reuses a unique promo code for that customer/post, sends a static public comment reply, creates a promo lead, and sends a private Instagram reply asking the commenter for their name and phone number. Once the customer provides those details in DM, the backend sends the promo code by Twilio SMS.

## Environment variables

- `META_VERIFY_TOKEN` is the verify token you will also enter in the Meta developer dashboard.
- `INSTAGRAM_ACCESS_TOKEN` is the access token used to send Instagram DM replies through the Meta Graph API.
- `SUPABASE_URL` is your Supabase project URL.
- `SUPABASE_SERVICE_ROLE_KEY` is used by the backend webhook to insert and update tenant data. Keep this server-side only.
- `SUPABASE_ANON_KEY` is used by backend API routes to verify Supabase Auth bearer tokens from the frontend.
- `FRONTEND_ORIGIN` optionally allows a deployed frontend origin for backend API CORS. Local dev allows `http://127.0.0.1:5173` and `http://localhost:5173` by default.
- `OPENAI_API_KEY` is used to authenticate with OpenAI.
- `OPENAI_MODEL` optionally overrides the default OpenAI model.
- `COMMENT_CLASSIFIER_MODEL` optionally overrides the OpenAI model used to classify promotional post comments. Defaults to `OPENAI_MODEL`.
- `COMMENT_CLASSIFIER_MIN_CONFIDENCE` optionally sets the minimum confidence for restaurant-intent comment triggers. Defaults to `0.65`.
- `OPENAI_EMBEDDING_MODEL` optionally overrides the embedding model used for RAG queries. Defaults to `text-embedding-3-small`.
- `OPENAI_SYSTEM_PROMPT` optionally overrides the default general assistant prompt when no account-specific prompt is provided.
- `OPENAI_FALLBACK_MESSAGE` optionally overrides the fallback reply used when OpenAI fails.
- `RAG_ENABLED` optionally enables or disables knowledge retrieval. Defaults to `true`.
- `RAG_MATCH_COUNT` optionally sets how many knowledge chunks are sent to OpenAI. Defaults to `5`.
- `FOLLOWUP_CRON_SECRET` protects the follow-up processor endpoint.
- `FOLLOWUP_DELAY_MINUTES` optionally sets the delay between promo-code redemption and the follow-up SMS. Defaults to `10`.
- `FOLLOWUP_BATCH_SIZE` optionally sets how many due follow-ups one processor call handles. Defaults to `20`.
- `TWILIO_ACCOUNT_SID` is the Twilio account SID used for SMS delivery.
- `TWILIO_AUTH_TOKEN` is the Twilio auth token used for SMS delivery. Keep this server-side only.
- `TWILIO_MESSAGING_SERVICE_SID` is the Twilio Messaging Service SID used as the SMS sender.
- `TWILIO_VALIDATE_SIGNATURE` optionally controls Twilio webhook signature validation. Defaults to `true`; set to `false` only for local/manual webhook testing.
- `PORT` is provided by Render automatically.

The `frontend-login` Vite app also uses browser-safe frontend env vars:

```env
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your-public-anon-key
VITE_BACKEND_URL=http://127.0.0.1:5000
```

## Supabase setup

Run the schema in `backend/database/schema.sql` in your Supabase SQL editor or with `psql`. The schema enables `pgcrypto` and `vector`, creates the Instagram/DM/comment tables, creates `knowledge_chunks`, and defines the `match_knowledge_chunks(...)` RPC used by RAG.

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

## Instagram media backfill

When onboarding a connected Instagram account, backfill historical media into `ig_posts` with the internal `instagram_accounts.id` value:

```bash
python3 backend/backfill_instagram_media.py YOUR_INTERNAL_INSTAGRAM_ACCOUNT_UUID --limit 1
```

Omit `--limit` to walk all available Instagram media pages. The script stores historical media as regular posts with `automation_enabled = false`.

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
python3 -m pip install -r embeddings/requirements.txt
python3 embeddings/embed-to-db.py embeddings/businessData/kosoo-chunks.jsonl 32
```

`INGEST_TRACK_RUNS=true` records the import in `ingest_runs`.

## Promotional comment automation

To enable comment-to-DM automation for a post, mark an `ig_posts` row as promotional and choose a `comment_trigger_mode`:

- `keywords` only triggers when the comment contains one of `trigger_keywords`.
- `restaurant_intent` triggers when OpenAI classifies the comment as a positive/neutral genuine restaurant question or comment, such as dietary/menu questions, reservation/location/hours questions, purchase intent, or positive experience comments.
- `keywords_or_restaurant_intent` checks keywords first, then uses restaurant-intent classification if no keyword matched.

Existing promotional posts default to `keywords`.

```sql
update public.ig_posts
set
  post_type = 'promotional',
  automation_enabled = true,
  comment_trigger_mode = 'keywords',
  automation_starts_at = now(),
  automation_ends_at = now() + interval '7 days',
  trigger_keywords = '["DM", "Test"]'::jsonb,
  comment_reply_text = 'Check DMs',
  dm_prompt = 'Send a friendly private reply about this promotion.',
  promo_code_valid_duration_hours = 48,
  promotion_metadata = '{"code_prefix": "KOSOO"}'::jsonb
where instagram_media_id = 'YOUR_INSTAGRAM_MEDIA_ID';
```

For an intent-only promotional post, set `comment_trigger_mode = 'restaurant_intent'` and leave `trigger_keywords = '[]'::jsonb`. Classification results are stored in `ig_comment_classifications`; classifier failures fail closed and do not send a public reply, DM, or promo code.

Automation is limited to one attempted DM per `post_id` and `contact_id`. The app only sends the public reply and private DM while the optional automation window is active:

- `automation_starts_at = NULL` means the automation can start immediately.
- `automation_ends_at = NULL` means the automation has no end date.
- Both timestamps `NULL` means the promotional post behaves like an always-active automation as long as `automation_enabled = true`.
- If `automation_starts_at` is in the future, comments are stored but no public reply, DM, or promo code is sent yet.
- If `automation_ends_at` has passed, comments are stored but no public reply, DM, or promo code is sent.

The app stores one readable promo code per customer/post in `ig_promo_codes`, but the initial Instagram DM does not reveal the code. Instead, the DM asks the customer to reply with their name and phone number so the code can be texted to them. The lead-capture state is stored in `ig_promo_leads`, and successful SMS sends are logged in `ig_sms_messages`. If `promotion_metadata.code_prefix` is not set, codes use the `PROMO` prefix.

Inbound DMs are checked for an active collecting promo lead before the normal RAG chatbot path. The app extracts the customer name and phone number, normalizes US/Canada phone numbers to E.164, stores consent timing, and sends the promo code by Twilio SMS once both fields are available. If either field is missing or the phone number cannot be normalized, the Instagram reply asks only for the missing detail.

If a promo customer replies by SMS, configure the Twilio Messaging Service incoming message webhook to:

```text
POST https://YOUR_BACKEND_HOST/api/twilio/sms-webhook
```

The backend matches inbound SMS by sender phone number against existing `ig_promo_leads`, stores the conversation in `ig_sms_conversations` and `ig_sms_conversation_messages`, retrieves RAG knowledge for the matched Instagram account, generates a concise SMS reply with OpenAI, and sends the reply through Twilio. Unknown phone numbers are ignored for v1. SMS stop keywords such as `STOP`, `UNSUBSCRIBE`, and `CANCEL` close the app-level SMS conversation and do not trigger the LLM.

Promo code validity is controlled by `promo_code_valid_duration_hours` on the post:

- `promo_code_valid_duration_hours = NULL` means newly issued codes do not expire, so `ig_promo_codes.expires_at` stays `NULL`.
- A positive value, such as `48`, means each newly issued code is valid for that many hours from the moment it is created.
- Reused codes keep their original `valid_from` and `expires_at`; changing the post duration later does not rewrite already-issued codes.
- Expiration is checked from `ig_promo_codes.expires_at`; when webhook traffic is processed, issued codes with `expires_at < now()` are marked `expired`.
- `status = 'expired'` means the validity window has passed; `redeemed` means the code was used; `void` means an admin/manual flow invalidated it.

When a staff user redeems a promo code through the `frontend-login` Redeem page, the backend creates one durable `ig_sms_messages` row for that promo code with `purpose = 'post_redemption_followup'`. By default, the follow-up SMS is scheduled for 10 minutes after `ig_promo_codes.redeemed_at` and is sent to the phone number collected for the promo lead.

Follow-ups are not sent by an in-memory timer. Run the due-message processor from a cron service such as Render Cron or Supabase cron:

```bash
curl -X POST https://YOUR_BACKEND_HOST/api/followups/process-due \
  -H "X-Followup-Cron-Secret: YOUR_FOLLOWUP_CRON_SECRET"
```

The processor finds pending SMS rows with `scheduled_for <= now()`, sends them through Twilio, then marks each row `sent` or `failed`.

On Render, this repo defines a separate cron service named `process-promo-followups` in `render.yaml`. It runs every minute and calls the backend processor endpoint through `backend/process_due_followups.py`. Set these env vars on the cron service:

```env
BACKEND_URL=https://your-render-service.onrender.com
FOLLOWUP_CRON_SECRET=the-same-secret-used-by-your-backend
```

The cron service is separate from the web service, so it does not automatically know the backend URL unless `BACKEND_URL` is set.

The `frontend-login` Add Promotion page creates a pending `ig_promotion_setups` row through `POST /api/promotions`. The form includes the promotion trigger mode, trigger keywords when needed, automation window, promo-code validity duration, comment reply text, DM prompt, and code prefix. The backend snapshots the selected account's existing `ig_posts.instagram_media_id` values, polls Instagram media every 30 seconds for up to 5 minutes, and turns the newest unseen media item into a promotional `ig_posts` row. Only one pending/polling setup can exist per Instagram account.

## Run locally

```bash
pip install -r backend/requirements.txt
python backend/app.py
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
- comment automation results such as `comment_automation_sent`, `comment_no_keyword_match`, `comment_no_restaurant_intent_match`, `comment_classifier_failed`, `comment_duplicate_automation`, `comment_public_reply_failed`, or `comment_private_reply_failed`
- full webhook payloads
- the Supabase IDs for the business, Instagram account, contact, session, and messages
- the OpenAI generation result, including how many RAG knowledge chunks were included
- the send-message API response when a reply is attempted
