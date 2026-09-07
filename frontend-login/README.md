# Frontend technical guide

The frontend is a React and TypeScript staff dashboard built with Vite. It handles authentication, business/account selection, promotion setup, promo-code redemption, and knowledge management.

## Technology

- React 19
- TypeScript
- Vite
- React Router
- Supabase JavaScript client
- Plain CSS in `src/styles.css`

## Source structure

```text
src/
├── components/
│   ├── AppShell.tsx          Shared header, selectors, and navigation
│   └── ProtectedRoute.tsx    Authentication guard
├── lib/
│   ├── accountContext.tsx    Business and Instagram account state
│   ├── auth.tsx              Supabase session and auth actions
│   ├── authEvents.ts         Login/logout audit records
│   ├── backend.ts            Typed backend API calls
│   └── supabase.ts           Browser-safe Supabase client
├── pages/
│   ├── SignIn.tsx
│   ├── SignUp.tsx
│   ├── AddPromotion.tsx
│   ├── Redeem.tsx
│   └── Knowledge.tsx
├── App.tsx                   Route definitions
├── main.tsx                  React entry point
├── styles.css                Application styling
└── types.ts                  Shared API and database types
```

## Routes

| Route | Access | Purpose |
| --- | --- | --- |
| `/signin` | Public | Sign in with email and password |
| `/signup` | Public | Create a Supabase Auth account |
| `/redeem` | Protected | Redeem a promotion code and record staff notes |
| `/add-promotion` | Protected | Configure and watch a new promotion setup |
| `/knowledge` | Protected | Add, browse, and filter knowledge chunks |

Unknown routes redirect to `/redeem`. Protected routes redirect signed-out users to `/signin`.

## Authentication

`AuthProvider` initializes the current Supabase session and listens for auth changes. Sign-in and sign-up use Supabase email/password authentication.

After authentication, the frontend upserts the user's `profiles` row. Login and logout actions are also written to `user_auth_events` when possible.

Signing up does not create a business. A new user must still be connected to:

1. A `businesses` row
2. A `business_users` row
3. At least one `instagram_accounts` row

Without these records, the shell displays an empty-state message.

## Account context

`AccountProvider` loads accessible businesses directly from Supabase. After a business is selected, it loads that business's Instagram accounts.

It exposes the selected business and Instagram account to all protected pages. The first available record is selected automatically. Supabase row-level security decides which records the signed-in user can read.

## Data access boundaries

The frontend talks to two services:

### Direct Supabase access

The browser uses the public anon key for:

- Authentication
- Profile creation/update
- Login/logout audit events
- Reading accessible businesses
- Reading accessible Instagram accounts

These operations rely on the policies in `backend/database/schema.sql`.

### Flask backend access

Promotion, redemption, and knowledge operations go through the Flask backend. `src/lib/backend.ts` adds the Supabase access token to each request:

```text
Authorization: Bearer <supabase-access-token>
```

The backend verifies the token and checks business membership before accessing account data with the service-role key.

## Page behavior

### Add Promotion

The page collects:

- Comment trigger mode
- Trigger keywords
- Optional automation start and end times
- Optional promo-code lifetime
- Public reply text
- Optional private-DM instructions
- Optional promo-code prefix

It posts the form to `/api/promotions`. The backend then watches for a newly published Instagram post. While the setup is `pending` or `polling`, the frontend requests `/api/promotions/<id>` every five seconds and displays the current status.

The UI allows one of three trigger modes: keywords, restaurant intent, or both. Keywords are required unless restaurant intent is the only mode.

### Redeem

The page normalizes entered codes to uppercase and posts them with the selected Instagram account and optional staff notes.

It displays whether the code was redeemed, expired, already redeemed, void, or not found. A successful response can also show the scheduled follow-up time and customer-profile redemption count.

### Knowledge

The page lists ten chunks at a time for the selected Instagram account. Staff can filter by type and category and can add one new chunk with optional metadata.

New text is sent to the backend, which creates the OpenAI embedding before storing it. The service-role key never reaches the browser.

## Environment configuration

Create `.env.local`:

```env
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your-public-anon-key
VITE_BACKEND_URL=http://127.0.0.1:5000
```

Only browser-safe values may use the `VITE_` prefix. Never place the Supabase service-role key, OpenAI key, Meta token, or Twilio credentials here.

If the two Supabase values are missing, `ProtectedRoute` displays a configuration message instead of rendering the app.

## Run locally

```bash
cd frontend-login
npm ci
npm run dev
```

The development server runs at `http://localhost:5173` by default. The backend allows this origin automatically.

## Build

```bash
npm run build
```

The script runs TypeScript project compilation and then produces the static site in `dist/`.

To inspect the production build locally:

```bash
npm run preview
```

## Deployment

Deploy `frontend-login/` to a static host with:

- Build command: `npm ci && npm run build`
- Output directory: `dist`
- `VITE_BACKEND_URL` set to the public Flask URL
- Supabase URL and anon key set at build time

Configure the static host to send unknown paths to `index.html`; otherwise refreshing `/redeem` or another client-side route may return 404.

Set the frontend's exact public origin as `FRONTEND_ORIGIN` on the backend. Do not include a trailing slash.

## Current limitations

- Business and Instagram-account onboarding must be completed outside the UI.
- Account selection is not saved across browser sessions.
- There is no public demo mode or sample-data mode.
- There are no automated component or end-to-end tests.
- The frontend does not currently show DM, comment, lead, or SMS history.
