# Backend technical guide

The backend receives Instagram and Twilio webhooks, protects staff APIs, coordinates AI workflows, and stores durable state in Supabase.

## Module boundaries

- `app.py`: compatible local and Gunicorn WSGI entrypoint
- `christa_ig/factory.py`: Flask application factory and middleware
- `christa_ig/routes.py`: route registration
- `workflows.py`: DM, comment, promotion, redemption, and SMS workflows
- `christa_ig/worker.py`: durable background processing loop
- `christa_ig/webhook_events.py`: event expansion, deterministic IDs, and retry schedule
- `christa_ig/config.py`: typed settings and production validation
- `christa_ig/security.py`: Meta HMAC and constant-time secret checks
- `christa_ig/observability.py`: request IDs and redacted JSON logs
- `christa_ig/http_client.py`: timeouts and safe-request retry rules
- `openai_client.py`: chat, embeddings, classification, extraction, and generated copy
- `supabase_client.py`: PostgREST data-access functions
- `database/schema.sql`: complete fresh-install database snapshot

The application factory keeps imports testable while `backend/app.py` preserves the existing `app:app` WSGI interface.

## Runtime architecture

```text
Meta POST /webhook
  -> verify raw-body HMAC
  -> expand all events
  -> idempotent webhook_jobs inserts
  -> return plain OK

Background worker
  -> atomically claim queued events
  -> run DM or comment workflow
  -> Supabase + OpenAI + Instagram
  -> clear successful raw payload
  -> retry known transient failures

Background worker
  -> claim due promotion poll ticks -> Instagram media lookup
  -> claim due SMS rows -> Twilio

Staff React app -> bearer-protected Flask APIs -> Supabase
Twilio webhook -> signature validation -> SMS conversation workflow
```

No OpenAI, Instagram, or Twilio call runs in Meta's webhook request path.

## Routes

| Method | Path | Purpose | Protection |
| --- | --- | --- | --- |
| `GET` | `/` | Compatibility health response | Public |
| `GET` | `/health/live` | Process liveness | Public |
| `GET` | `/health/ready` | Required configuration status | Public |
| `GET` | `/webhook` | Meta subscription challenge | Verify token |
| `POST` | `/webhook` | Durable Meta event intake | Meta HMAC |
| `POST` | `/api/twilio/sms-webhook` | Inbound SMS | Twilio signature |
| `GET` | `/api/knowledge-chunks` | Paginated account knowledge | Supabase bearer token |
| `POST` | `/api/knowledge-chunks` | Embed and add knowledge | Supabase bearer token |
| `POST` | `/api/promotions` | Create promotion watcher | Supabase bearer token |
| `GET` | `/api/promotions/<id>` | Read watcher status | Supabase bearer token |
| `POST` | `/api/promo-codes/redeem` | Redeem and schedule follow-up | Supabase bearer token |
| `POST` | `/api/followups/process-due` | Compatibility follow-up runner | Cron secret |

Staff errors use `{ "error", "code", "request_id" }`. Successful response bodies and existing paths are unchanged.

## Security controls

Meta signs the exact raw POST body with the Instagram App Secret. The backend calculates an HMAC-SHA256 digest and compares it with `X-Hub-Signature-256` using a constant-time comparison. Missing, malformed, or incorrect signatures return `401` before any database write.

For Instagram API with Instagram Login, copy the secret from **Instagram → API setup with Instagram login → Instagram App Secret**. Store that value in `META_APP_SECRET`. Do not use the separate general App Secret under **App settings → Basic**; it will make genuine Instagram deliveries fail signature validation.

Twilio webhook validation is enabled by default. `ProxyFix` reconstructs the public scheme and host before the Twilio validator sees the URL, which is important behind Render's proxy.

Other controls include:

- 1 MB request limit
- Exact CORS origin matching; production does not add localhost automatically
- Input count and length limits
- Supabase token and business-membership checks on staff APIs
- Request IDs on every response
- Structured JSON logs that redact secrets, authorization headers, messages, phone numbers, and common PII fields
- Service-role-only access to queue tables and claim functions

The service-role key bypasses row-level security. It must exist only in the backend and worker.

## Durable webhook queue

Each Meta delivery may contain multiple entries and events. `expand_meta_events` creates one small job for every DM or comment event. Meta message IDs and comment IDs become deterministic external event IDs; a stable hash is used only when Meta does not include one.

`webhook_jobs` has a unique `(provider, external_event_id)` constraint. Duplicate delivery inserts are ignored, so only one customer-facing workflow runs for an event.

`claim_webhook_jobs` uses `FOR UPDATE SKIP LOCKED` inside a service-role-only function. A claim:

- Changes the job to `processing`
- Increments its attempt count
- Sets the worker ID and five-minute lease
- Can reclaim a processing job after its lease expires

Known transient workflow failures retry up to five attempts after 2, 10, 30, 120, and 300 seconds. Successful and intentionally ignored jobs become terminal and their raw payload is replaced with `{}`. Terminal failures retain their payload for diagnosis and are deleted after seven days by default.

Outbound Instagram and Twilio mutations are never automatically retried because a network error can leave delivery status ambiguous. Only safe GET/HEAD integrations use the shared HTTP retry policy.

## Promotion polling

Creating a promotion records the current media baseline and an `ig_promotion_setups` row. It does not create a thread.

The worker atomically claims due setup rows. One tick checks for a new post, then either:

- Creates the promotional post and marks the setup `found`
- Marks it `expired` after five minutes
- Releases the lease and sets `next_poll_at` 30 seconds ahead

Abandoned setup leases become claimable after five minutes. The frontend may continue polling the existing status API every five seconds.

## DM and comment processing

DM events reject echoes, receipts, incomplete records, and duplicate Meta message IDs. A valid message is stored with its contact and session. If the contact is collecting a promotion, lead extraction runs first. Otherwise the workflow retrieves account knowledge, generates a response, stores it, and sends it through Instagram.

Comment events are checked against an enabled promotional post and its active time window. Only one automation is allowed per post/contact pair.

Trigger modes:

- `keywords`: case-insensitive substring match; no classifier call
- `restaurant_intent`: structured OpenAI classification
- `keywords_or_restaurant_intent`: keywords first, classifier only if needed

The unchanged restaurant classifier recognizes positive or neutral menu, dietary, pricing, hours, location, reservation, availability, buying-interest, and experience comments. It rejects complaints, spam, tag-only, emoji-only, and unrelated comments. Results must pass the configured confidence threshold and are stored in `ig_comment_classifications`. Classifier errors fail closed.

After a match, the workflow creates the contact, promo code, and lead, then sends a public reply and private lead-capture message. Restaurant-intent copy uses retrieved knowledge with deterministic fallback copy.

## Knowledge retrieval

The backend embeds the incoming text with `text-embedding-3-small` by default and calls `match_knowledge_chunks`. The database limits results to the selected Instagram account and sorts by vector similarity. Retrieval failures are recorded without blocking a safe fallback reply.

## Promo codes and SMS

Lead extraction returns a customer name and phone from DM history. Phone normalization accepts E.164-like values and US/Canada 10- or 11-digit values. Once complete, the code is sent through Twilio.

Redemption returns one of `redeemed`, `expired`, `already_redeemed`, `void`, or `not_found`. A successful redemption updates the customer profile and creates one due follow-up row. The worker-safe SMS service changes `pending` to `sending` before delivery; another worker cannot claim it. A delivery with an ambiguous result is not retried automatically.

Inbound SMS messages are deduplicated by Twilio SID. Standard stop words (`STOP`, `STOPALL`, `UNSUBSCRIBE`, `CANCEL`, `END`, and `QUIT`) close the conversation before AI generation.

The protected follow-up endpoint and `process_due_followups.py` remain available for compatibility and use the same service functions as the worker.

To invoke the compatibility script manually:

```bash
BACKEND_URL=https://your-backend.example.com \
FOLLOWUP_CRON_SECRET=your-shared-secret \
python backend/process_due_followups.py
```

## Configuration

Copy `backend/.env.example` to `backend/.env`. It loads automatically for both the web process and worker.

Core production variables:

| Variable | Purpose |
| --- | --- |
| `APP_ENV` | `development`, `test`, or `production` |
| `META_VERIFY_TOKEN` | Meta subscription challenge secret |
| `META_APP_SECRET` | Instagram App Secret used to validate webhook POSTs; get it from Instagram → API setup with Instagram login |
| `INSTAGRAM_ACCESS_TOKEN` | Instagram Graph API token |
| `SUPABASE_URL` | Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-only database key |
| `SUPABASE_ANON_KEY` | Token verification key |
| `OPENAI_API_KEY` | AI and embedding access |
| `FRONTEND_ORIGINS` | Comma-separated exact CORS origins |
| `LOG_LEVEL` | JSON log level; default `INFO` |

`FRONTEND_ORIGIN` is still accepted when `FRONTEND_ORIGINS` is absent.

Worker variables:

| Variable | Default |
| --- | --- |
| `WORKER_POLL_SECONDS` | `2` |
| `WORKER_BATCH_SIZE` | `10` |
| `WORKER_MAX_ATTEMPTS` | `5` |
| `WEBHOOK_FAILURE_RETENTION_DAYS` | `7` |
| `WORKER_LEASE_SECONDS` | `300` |

SMS needs `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_MESSAGING_SERVICE_SID`. Keep `TWILIO_VALIDATE_SIGNATURE=true`. The compatibility endpoint also needs `FOLLOWUP_CRON_SECRET`.

Production startup fails immediately when Meta, Instagram, Supabase, or OpenAI core settings are missing.

## Local development

From the repository root:

```bash
make setup
cp backend/.env.example backend/.env
.venv/bin/python backend/app.py
```

Run the worker in another terminal:

```bash
cd backend
../.venv/bin/python -m christa_ig.worker
```

## Automated testing

```bash
.venv/bin/python -m ruff check backend embeddings
.venv/bin/python -m pytest
```

The coverage gate is 80% for the extracted backend package, which is stricter than the project's 70% minimum. Each new worker, event, security, configuration, and transport module is currently above 80%. CI also audits production dependencies and replays the full database migration chain.

Database tests:

```bash
supabase start
supabase db reset
supabase test db
```

## Migrations

- `supabase/migrations/20260907000000_initial_schema.sql`: versioned baseline for a new project
- `supabase/migrations/20260907001000_production_hardening.sql`: additive queue, lease, index, and claim-function changes
- `database/schema.sql`: synchronized full snapshot for a fresh installation

For future changes, add a new migration. Never edit a migration that has already been applied.

## Production commands

Web:

```bash
gunicorn --chdir backend --workers 2 --threads 4 --timeout 60 --access-logfile - app:app
```

Worker:

```bash
cd backend && python -m christa_ig.worker
```

`render.yaml` is the free portfolio-demo topology: Render hosts the web API and static frontend, while the worker runs locally during recording. The free web service omits the unsupported graceful-shutdown setting.

`render.production.yaml` is the optional always-on topology. It uses paid Render services for both the API and continuous worker and enables graceful shutdown. Use only one of these Blueprint files to manage the services.

## Known product limits

- The AI intent classifier remains restaurant-specific.
- Phone normalization is mainly designed for US/Canada numbers.
- Business and Instagram account onboarding is still managed outside the dashboard.
- This repository does not include a public demo mode or deployment credentials.
