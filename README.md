# Christa IG

Christa IG helps a business turn Instagram conversations into useful customer interactions.

It can answer direct messages using the business's own information, react to comments on promotional posts, collect customer details, send promo codes by text message, and help staff redeem those codes.

The project was first built for restaurants, but most of it can also support businesses such as:

- Gyms answering membership questions and promoting trial passes
- Online shops answering product questions and sending discount codes
- Salons collecting leads for seasonal offers
- Local services answering common questions and following up with customers
- Events and venues promoting tickets or special packages

The current AI comment classifier is written for restaurant conversations. Other businesses can use keyword-based promotions immediately. The classifier prompt can also be changed for another industry.

## What it can do

- Reply to Instagram direct messages with AI
- Use business-specific facts, policies, products, menus, or FAQs when writing replies
- Start a promotion when a comment contains a chosen keyword
- Recognize restaurant-related questions and buying interest with AI
- Reply publicly and send a private Instagram message
- Collect a customer's name and phone number through direct messages
- Create one unique promo code for each customer and promotion
- Send promo codes and follow-up messages through Twilio SMS
- Let staff create promotions, manage knowledge, and redeem codes in a web dashboard
- Keep each business's data separate with Supabase authentication and access rules

## How it works

The project has three parts:

- `frontend-login/`: the React staff dashboard
- `backend/`: the Flask API and webhook service
- `embeddings/`: a tool for loading larger amounts of business knowledge

Instagram and Twilio send new events to the backend. The backend stores them in Supabase, asks OpenAI for help when needed, and sends the response back through Instagram or SMS.

For more detail, see:

- [Backend guide](backend/README.md)
- [Frontend guide](frontend-login/README.md)
- [Embedding guide](embeddings/README-embeddings.md)

## What you need

- Python 3.10 or newer
- Node.js 20.19+ or 22.12+
- A Supabase project
- An OpenAI API key
- A Meta app and a connected professional Instagram account
- A Twilio account if you want SMS features
- A public HTTPS address for receiving webhooks in production

## Setup

### 1. Create the database

Create a Supabase project. Open its SQL editor and run:

```text
backend/database/schema.sql
```

This creates the tables, access rules, and vector search function used by the app.

### 2. Create your first account records

Create a user in Supabase Authentication and note its user ID. Then use the Supabase SQL editor to create:

1. A matching row in `profiles`
2. A row in `businesses`
3. A row in `business_users` that links your Supabase user ID to that business with the `owner` role
4. A row in `instagram_accounts` that links the business to its Instagram account

You can also create the user with the frontend sign-up page after completing its setup below; that path creates the `profiles` row for you. The dashboard does not create the remaining business records yet. The `instagram_user_id` must be the ID Meta sends as `entry.id`, not the username.

### 3. Configure the backend

Create `backend/.env`:

```env
META_VERIFY_TOKEN=choose-a-private-verification-token
INSTAGRAM_ACCESS_TOKEN=your-instagram-access-token

SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_ANON_KEY=your-anon-key

OPENAI_API_KEY=your-openai-api-key

TWILIO_ACCOUNT_SID=your-twilio-account-sid
TWILIO_AUTH_TOKEN=your-twilio-auth-token
TWILIO_MESSAGING_SERVICE_SID=your-messaging-service-sid

FOLLOWUP_CRON_SECRET=choose-another-private-token
FRONTEND_ORIGIN=http://localhost:5173
```

Twilio values are only required for SMS features. Never put the service-role key or other private keys in the frontend.

Create and run the backend:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt

set -a
source backend/.env
set +a

python backend/app.py
```

Check that it is running:

```bash
curl http://127.0.0.1:5000/
```

You should receive `{"status":"ok"}`.

### 4. Configure the frontend

Create `frontend-login/.env.local` and fill in:

```env
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your-anon-key
VITE_BACKEND_URL=http://127.0.0.1:5000
```

Then run:

```bash
cd frontend-login
npm ci
npm run dev
```

Open `http://localhost:5173`.

### 5. Add business knowledge

Sign in and open the **Knowledge** page to add individual facts. For a large JSONL file, use the tool described in [the embedding guide](embeddings/README-embeddings.md).

### 6. Connect webhooks

Deploy the backend to a public HTTPS address. The included `render.yaml` can create the Flask service and follow-up cron job on Render.

Configure these incoming webhook addresses:

```text
Meta callback:   https://YOUR_BACKEND/webhook
Twilio SMS:      https://YOUR_BACKEND/api/twilio/sms-webhook
```

Use the same Meta verification token in Meta and `META_VERIFY_TOKEN`. Give the deployed frontend URL to `FRONTEND_ORIGIN`, and give the cron service the same `FOLLOWUP_CRON_SECRET` as the backend.

The frontend is not deployed by `render.yaml`; deploy `frontend-login/` separately to a static web host.

## A simple test scenario

1. Add a few facts about a fictional business on the Knowledge page.
2. Create a keyword promotion from the Add Promotion page.
3. Publish a new Instagram post while the setup is polling.
4. Comment with the chosen keyword from another account.
5. Reply to the private message with a name and phone number.
6. Confirm that the promo code arrives by SMS.
7. Redeem the code in the dashboard and add a staff note.
8. Run the follow-up processor and confirm that the customer receives a follow-up SMS.

## Security

- Keep `.env` files private.
- Never expose `SUPABASE_SERVICE_ROLE_KEY`, OpenAI keys, Meta tokens, or Twilio credentials in browser code.
- Keep Twilio signature validation enabled in production.
- Use test accounts and test phone numbers when recording a public demo.
- Rotate old credentials before publishing or redeploying the project.
