# Python Meta Webhook for Render

This is a Flask app for receiving Meta webhook requests on Render. It supports webhook verification, replies to inbound Instagram DMs with OpenAI-generated text, and logs incoming events to the console.

## Endpoints

- `GET /` returns a healthcheck response.
- `GET /webhook` handles Meta webhook verification.
- `POST /webhook` processes Instagram webhook events and returns a fast `200 OK`.

For inbound text `dm-related` webhook events, the app generates a reply with OpenAI, keeps in-memory chat history per sender, and sends the reply back to the message sender.

## Environment variables

- `META_VERIFY_TOKEN` is the verify token you will also enter in the Meta developer dashboard.
- `INSTAGRAM_ACCESS_TOKEN` is the access token used to send Instagram DM replies through the Meta Graph API.
- `OPENAI_API_KEY` is used to authenticate with OpenAI.
- `OPENAI_MODEL` optionally overrides the default OpenAI model.
- `OPENAI_SYSTEM_PROMPT` optionally overrides the default general assistant prompt.
- `OPENAI_FALLBACK_MESSAGE` optionally overrides the fallback reply used when OpenAI fails.
- `PORT` is provided by Render automatically.

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Then send a test request:

```bash
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{"message":"hello from webhook"}'
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
- processing results such as `replied`, `fallback_sent`, `skipped_echo`, or `skipped_read_receipt`
- full webhook payloads
- the OpenAI generation result for inbound DMs
- the send-message API response when a reply is attempted
