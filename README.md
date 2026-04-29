# Python Meta Webhook for Render

This is a Flask app for receiving Meta webhook requests on Render. It supports webhook verification, replies to inbound Instagram DMs with OpenAI-generated text, and logs incoming events to the console.

## Endpoints

- `GET /` returns a healthcheck response.
- `GET /webhook` handles Meta webhook verification.
- `POST /webhook` processes Instagram webhook events and returns a fast `200 OK`.

For inbound text `dm-related` webhook events, the app looks up the connected Instagram account in Supabase, persists the contact/session/messages, generates a reply with OpenAI from database-backed chat history, stores the assistant reply, and sends the reply back to the message sender.

## Environment variables

- `META_VERIFY_TOKEN` is the verify token you will also enter in the Meta developer dashboard.
- `INSTAGRAM_ACCESS_TOKEN` is the access token used to send Instagram DM replies through the Meta Graph API.
- `SUPABASE_URL` is your Supabase project URL.
- `SUPABASE_SERVICE_ROLE_KEY` is used by the backend webhook to insert and update tenant data. Keep this server-side only.
- `OPENAI_API_KEY` is used to authenticate with OpenAI.
- `OPENAI_MODEL` optionally overrides the default OpenAI model.
- `OPENAI_SYSTEM_PROMPT` optionally overrides the default general assistant prompt when no account-specific prompt is provided.
- `OPENAI_FALLBACK_MESSAGE` optionally overrides the fallback reply used when OpenAI fails.
- `PORT` is provided by Render automatically.

## Supabase setup

Run the schema in `database/schema.sql` in your Supabase SQL editor or with `psql`.

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

If `SUPABASE_URL` or `SUPABASE_SERVICE_ROLE_KEY` is missing, local development falls back to the old in-memory history behavior.

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
- full webhook payloads
- the Supabase IDs for the business, Instagram account, contact, session, and messages
- the OpenAI generation result for inbound DMs
- the send-message API response when a reply is attempted
