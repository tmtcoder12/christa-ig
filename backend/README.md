# Backend technical guide

The backend is a Flask application that coordinates Instagram, OpenAI, Supabase, and Twilio. It receives webhooks, applies promotion and conversation rules, stores state, and sends outbound messages.

## Main files

- `app.py`: routes, event parsing, workflow logic, validation, and outbound API calls
- `openai_client.py`: embeddings, chat replies, classification, lead extraction, and promotional copy
- `supabase_client.py`: a small PostgREST data-access layer
- `database/schema.sql`: tables, indexes, triggers, row-level security, and vector search
- `backfill_instagram_media.py`: imports existing Instagram posts as non-promotional posts
- `process_due_followups.py`: calls the protected follow-up endpoint from a cron service

## Runtime architecture

```text
Meta webhooks ───────┐
                     ├─> Flask ─> Supabase/Postgres + pgvector
Staff dashboard ─────┤       ├─> OpenAI
                     │       ├─> Instagram Graph API
Twilio webhooks ─────┘       └─> Twilio SMS API

Render cron ────────────────> protected follow-up endpoint
```

The backend uses the Supabase REST API through Python's standard `urllib` module. It does not use the Supabase Python SDK. Backend database requests use the service-role key and therefore bypass row-level security; every staff-facing route must perform its own user and account-access checks.

## Routes

| Method | Path | Purpose | Authentication |
| --- | --- | --- | --- |
| `GET` | `/` | Health check | None |
| `GET` | `/webhook` | Meta webhook verification | `META_VERIFY_TOKEN` query check |
| `POST` | `/webhook` | Instagram DM and comment events | No request-signature check in the current code |
| `POST` | `/api/twilio/sms-webhook` | Inbound customer SMS | Twilio signature |
| `GET` | `/api/knowledge-chunks` | List and filter account knowledge | Supabase bearer token |
| `POST` | `/api/knowledge-chunks` | Embed and create one knowledge chunk | Supabase bearer token |
| `POST` | `/api/promotions` | Start promotion-post polling | Supabase bearer token |
| `GET` | `/api/promotions/<id>` | Read promotion setup status | Supabase bearer token |
| `POST` | `/api/promo-codes/redeem` | Redeem a code and schedule follow-up | Supabase bearer token |
| `POST` | `/api/followups/process-due` | Send due follow-up SMS messages | `X-Followup-Cron-Secret` header |

The `/api/*` routes also answer CORS preflight requests. Localhost is allowed by default. A deployed dashboard must exactly match `FRONTEND_ORIGIN`.

## Authentication and tenant access

The frontend sends its Supabase access token as `Authorization: Bearer <token>`.

For a protected request, the backend:

1. Sends the token to Supabase Auth's `/auth/v1/user` endpoint.
2. Reads the authenticated user ID.
3. Looks up the requested Instagram account.
4. Checks for a matching `business_users` membership.
5. Continues only if the user belongs to that business.

The browser also reads `businesses` and `instagram_accounts` directly from Supabase. The row-level security policies in `database/schema.sql` limit those reads to business members.

## Instagram DM flow

`POST /webhook` treats an entry with a non-empty `messaging` array as DM-related.

The parser ignores:

- Read receipts
- Echoes created by the business account
- Messages without text
- Invalid or incomplete payloads

For a valid text DM with Supabase configured, the backend:

1. Finds `instagram_accounts` using the webhook's `entry.id`.
2. Creates or updates the sender in `ig_contacts`.
3. Creates or reuses one open `ig_dm_sessions` row for the contact.
4. Rejects a duplicate `instagram_message_id`.
5. Saves the inbound message.
6. Checks whether the contact has an active promotion lead.
7. If there is no active lead, retrieves recent conversation history and relevant knowledge.
8. Generates a reply with OpenAI.
9. Stores the assistant message and sends it through the Instagram Graph API.
10. Updates the session state and last-activity time.

If Supabase is not configured, DMs use an in-memory history dictionary. This fallback is useful for basic development but loses all data on restart and does not support promotion automation.

### RAG retrieval

For normal DMs, restaurant-intent promotion copy, and SMS replies, `retrieve_knowledge_context`:

1. Embeds the incoming text with `text-embedding-3-small` by default.
2. Calls the `match_knowledge_chunks` Supabase RPC.
3. Restricts matches to the current `instagram_account_id`.
4. Selects the closest vectors by cosine distance.
5. Adds up to `RAG_MATCH_COUNT` chunks to the OpenAI instructions.

RAG is enabled by default. If embedding or retrieval fails, the failure is logged and message generation continues without knowledge context.

## Comment and promotion flow

An entry containing `changes` is treated as comment-related. The comment parser extracts the comment ID, media ID, text, commenter, and timestamp. Self-comments and incomplete events are ignored.

The backend stores the comment, then checks that its `ig_posts` record is:

- Marked `promotional`
- Enabled for automation
- Inside its optional start and end time
- Not already automated for the same post and contact

### Trigger modes

Each promotional post has one of three modes:

#### `keywords`

The comment triggers when any configured keyword appears anywhere in the text. Matching is case-insensitive and uses substring matching. No OpenAI classification is performed.

#### `restaurant_intent`

OpenAI returns structured JSON containing:

- `should_trigger`
- `category`
- `confidence`
- `reasoning`

The current prompt accepts positive or neutral restaurant intent such as menu, dietary, pricing, hours, location, reservation, availability, purchase-interest, or positive-experience comments. Complaints, negative feedback, spam, tag-only comments, emoji-only comments, and unrelated text should not trigger.

A comment triggers only when:

- OpenAI reports `should_trigger=true`
- Confidence is at least `COMMENT_CLASSIFIER_MIN_CONFIDENCE` (default `0.65`)
- The normalized category is not `spam_or_unrelated` or `complaint_or_negative`

Classification attempts are stored in `ig_comment_classifications`. Errors fail closed, so a classifier failure does not send a message or issue a code.

#### `keywords_or_restaurant_intent`

Keywords are checked first. OpenAI is called only when no keyword matches.

### Actions after a match

After a qualifying comment, the backend:

1. Creates or reuses the contact.
2. Creates one promo code for the post/contact pair.
3. Creates a promotion lead in the `collecting` state.
4. Sends a public comment reply.
5. Sends a private reply asking for the customer's name and phone number.
6. Stores message IDs and the final automation status.

Keyword triggers use the configured static public reply and standard lead-capture DM. Restaurant-intent triggers retrieve business knowledge and ask OpenAI to write both messages. If copy generation fails, they fall back to the configured reply and standard lead-capture message.

The public reply is limited to 220 characters and is adjusted to mention the DM inbox. Only one automation attempt is allowed per post/contact pair.

## Promotion setup polling

`POST /api/promotions` does not require staff to paste a media ID. Instead it:

1. Fetches and stores currently unknown Instagram media as regular posts.
2. Takes a snapshot of all known media IDs.
3. Creates an `ig_promotion_setups` row.
4. Starts a background thread.
5. Polls Instagram every 30 seconds for up to five minutes.
6. Converts the newest unseen media item into an enabled promotional post.

Only one pending or polling setup is allowed per Instagram account. The frontend polls the setup-status route every five seconds.

This polling runs inside the Flask process. A process restart or multi-instance deployment can interrupt it; a production version should move this work to a durable job queue.

## Promotion lead capture

Incoming DMs are checked for an active `ig_promo_leads` row before the normal chatbot runs.

OpenAI attempts to extract `customer_name` and `phone` as JSON from recent DM history. The backend also has simple cleanup and phone fallback logic. Phone normalization currently supports:

- Numbers already beginning with `+`, with 8 to 15 digits
- Ten-digit US/Canada numbers, converted to `+1...`
- Eleven-digit US/Canada numbers beginning with `1`

If either value is missing, the Instagram reply asks only for the missing information. When both are available, the backend records SMS consent time, stores the contact details, sends the code through Twilio, and marks the lead `code_sms_sent` or `code_sms_failed`.

## Promo-code redemption and follow-up

The redemption route checks the code inside the selected Instagram account. Its response distinguishes:

- `redeemed`
- `expired`
- `already_redeemed`
- `void`
- `not_found`

A successful redemption records the staff user and optional notes, updates or creates the customer's profile, and creates one `post_redemption_followup` SMS row. OpenAI writes a short follow-up using the customer's first name and staff notes; deterministic text is used if generation fails.

The message is scheduled for `FOLLOWUP_DELAY_MINUTES` after redemption. It is not sent by an in-process timer.

The cron job runs `process_due_followups.py`, which calls:

```text
POST /api/followups/process-due
X-Followup-Cron-Secret: <FOLLOWUP_CRON_SECRET>
```

The endpoint finds due messages, claims each by changing it from `pending` to `sending`, sends it through Twilio, and marks it `sent` or `failed`.

## Inbound SMS flow

Twilio posts customer replies to `/api/twilio/sms-webhook`.

The backend:

1. Validates the Twilio signature when `TWILIO_VALIDATE_SIGNATURE` is true.
2. Deduplicates messages by Twilio SID.
3. Finds the newest promotion lead with the sender's normalized phone number.
4. Ignores numbers that do not belong to a known lead.
5. Creates or reuses an SMS conversation.
6. Closes the conversation for standard stop words such as `STOP` or `CANCEL`.
7. Otherwise loads history and account knowledge, generates a concise reply, sends it, and stores both sides.

## OpenAI behavior

`openai_client.py` uses:

- `gpt-4o-mini` as the default text model
- `text-embedding-3-small` as the default embedding model
- Up to 20 recent messages of conversation history
- Account-specific `instagram_accounts.system_prompt` instructions when available

OpenAI is used for general replies, restaurant comment classification, dynamic promotion copy, lead extraction, and redemption follow-ups. Generation functions return structured success/error metadata so callers can record model, response ID, token usage, latency, and fallbacks.

## Database model

The main relationship is:

```text
businesses
├── business_users ── profiles/auth.users
└── instagram_accounts
    ├── knowledge_chunks
    ├── ig_contacts
    │   ├── ig_dm_sessions ── ig_dm_messages
    │   ├── ig_promo_leads
    │   ├── ig_sms_conversations ── ig_sms_conversation_messages
    │   └── ig_customer_profiles
    └── ig_posts
        ├── ig_comments ── ig_comment_classifications
        └── ig_promo_codes ── ig_sms_messages
```

See `database/schema.sql` for the complete set of tables, constraints, indexes, and policies.

## Configuration

### Required for the main backend

```env
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_ANON_KEY=
OPENAI_API_KEY=
INSTAGRAM_ACCESS_TOKEN=
META_VERIFY_TOKEN=
FRONTEND_ORIGIN=http://localhost:5173
```

### Required for SMS and scheduled follow-up

```env
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_MESSAGING_SERVICE_SID=
FOLLOWUP_CRON_SECRET=
```

### Optional

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPENAI_MODEL` | `gpt-4o-mini` | Text generation and extraction model |
| `COMMENT_CLASSIFIER_MODEL` | `OPENAI_MODEL` | Comment classification model |
| `COMMENT_CLASSIFIER_MIN_CONFIDENCE` | `0.65` | Minimum restaurant-intent confidence |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Query embedding model |
| `OPENAI_SYSTEM_PROMPT` | Built-in assistant prompt | Fallback account instructions |
| `OPENAI_FALLBACK_MESSAGE` | Built-in thank-you message | DM fallback text |
| `RAG_ENABLED` | `true` | Enables vector retrieval |
| `RAG_MATCH_COUNT` | `5` | Maximum retrieved chunks |
| `FOLLOWUP_DELAY_MINUTES` | `10` | Delay after redemption |
| `FOLLOWUP_BATCH_SIZE` | `20` | Due messages processed per call |
| `TWILIO_VALIDATE_SIGNATURE` | `true` | Validates incoming Twilio requests |
| `PORT` | `5000` | Flask listening port |

`app.py` does not currently call `load_dotenv`. Export the file before starting locally:

```bash
set -a
source backend/.env
set +a
python backend/app.py
```

## Local development

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt

set -a
source backend/.env
set +a

python backend/app.py
```

Test the health route:

```bash
curl http://127.0.0.1:5000/
```

Test Meta verification:

```bash
curl "http://127.0.0.1:5000/webhook?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=12345"
```

## Deployment notes

`render.yaml` defines:

- A Flask web service
- A cron service that processes follow-ups every five minutes

Set all secrets in the hosting platform rather than committing `.env`. The cron service also needs:

```env
BACKEND_URL=https://your-backend.example.com
FOLLOWUP_CRON_SECRET=the-same-value-as-the-web-service
```

The backend logs webhook payloads and processing metadata. Payloads can contain personal or sensitive data, so production logging should be reduced or sanitized.

## Current limitations

- Meta webhook signature validation is not implemented for `POST /webhook`.
- Webhook work, including OpenAI and outbound network calls, runs synchronously.
- Promotion polling uses an in-process thread rather than a durable worker.
- The intent classifier is restaurant-specific.
- Phone normalization is primarily designed for US/Canada numbers.
- There is no automated test suite yet.
