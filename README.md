# Python Webhook for Render

This is a minimal Flask app with a webhook endpoint that prints incoming requests to the console.

## Endpoints

- `GET /` returns a healthcheck response.
- `POST /webhook` prints the incoming request to the Render logs and returns a JSON success response.

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

## Deploy on Render

1. Push this folder to GitHub.
2. In Render, create a new Web Service from that repo.
3. Render should detect `render.yaml` automatically.
4. After deploy, your webhook URL will be:

```text
https://your-render-service.onrender.com/webhook
```

## View console output

Open your service in Render and check the **Logs** tab to see printed webhook payloads.
