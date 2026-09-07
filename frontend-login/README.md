# Frontend technical guide

This folder contains the React and TypeScript staff dashboard. It preserves the existing sign-in, account selection, promotion, knowledge, and redemption screens.

## Technology

- React 19 and React Router
- TypeScript with strict checking
- Vite
- Supabase JavaScript client
- Vitest, Testing Library, and MSW
- ESLint and Prettier

## Source structure

```text
src/
├── components/
│   ├── AppShell.tsx
│   ├── ErrorBoundary.tsx
│   └── ProtectedRoute.tsx
├── lib/
│   ├── accountContext.tsx
│   ├── auth.tsx
│   ├── authEvents.ts
│   ├── backend.ts
│   ├── database.types.ts
│   ├── env.ts
│   └── supabase.ts
├── pages/
│   ├── AddPromotion.tsx
│   ├── Knowledge.tsx
│   ├── Redeem.tsx
│   ├── SignIn.tsx
│   └── SignUp.tsx
├── test/
├── App.tsx
├── main.tsx
├── styles.css
└── types.ts
```

`ErrorBoundary` is mounted above the router. Unexpected render errors show a stable recovery screen instead of a blank page.

## Configuration

Copy the tracked placeholder file:

```bash
cp .env.example .env.local
```

Set:

```env
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your-public-anon-key
VITE_BACKEND_URL=http://127.0.0.1:5000
```

`env.ts` trims URLs, accepts only HTTP(S), and reports missing Supabase values. Protected pages show the configuration errors rather than attempting to run with partial settings.

Only browser-safe values may use the `VITE_` prefix. Never put a service-role key, OpenAI key, Meta secret, or Twilio credential in this folder.

## Authentication and account selection

`AuthProvider` loads the current Supabase session and listens for authentication changes. Protected routes wait for session initialization and redirect signed-out users to `/signin`.

After sign-in, `AccountProvider` reads the user's visible businesses. Selecting a business loads its Instagram accounts. The browser's anon key is constrained by Supabase row-level security; the frontend does not decide tenant access itself.

A new sign-up creates the Auth user and profile only. Business membership and Instagram account onboarding still happen outside this UI.

## Data access

The browser talks directly to Supabase for authentication, profile/audit records, businesses, and Instagram account selection. `database.types.ts` supplies generated-shape database types to `createClient<Database>` so direct queries are checked by TypeScript.

Regenerate this file after a database change with a linked project:

```bash
supabase gen types typescript --linked > src/lib/database.types.ts
```

For local Supabase, use:

```bash
supabase gen types typescript --local > src/lib/database.types.ts
```

Review generated changes before committing them.

Promotion, redemption, and knowledge operations go through Flask. `backend.ts` adds the Supabase bearer token, applies a 10-second timeout, and turns server errors into typed `ApiError` objects containing status, code, and request ID.

Safe GET requests retry transient 408, 429, 502, 503, and 504 responses twice. POST mutations never retry automatically, preventing duplicate promotions, knowledge rows, or redemptions when delivery is uncertain.

## Pages

- `/signin` and `/signup`: public Supabase email/password authentication
- `/redeem`: submit a code and show redeemed, expired, already-redeemed, void, or not-found results
- `/add-promotion`: create a setup and poll its status every five seconds while active
- `/knowledge`: add, filter, and page through account knowledge ten rows at a time

Unknown routes redirect to `/redeem`.

## Local development

Use Node.js 22.13 or newer:

```bash
npm ci
npm run dev
```

Vite serves the app at `http://localhost:5173` by default.

## Automated testing and checks

```bash
npm run format:check
npm run lint
npm run typecheck
npm test
npm run build
```

Or run everything:

```bash
npm run check
```

The tests mock both Flask and Supabase boundaries. They cover configuration, typed API errors, safe GET retries, mutation retry protection, authentication guards, account selection, promotion polling, knowledge pagination, and redemption results.

Use `npm run format` to apply Prettier.

## Production build and deployment

```bash
npm ci
npm run build
```

The output is `dist/`. Render builds this folder as a static site, rewrites unknown paths to `index.html` for React Router, and adds basic response security headers. Set all three `VITE_` values in Render before the build.

`VITE_BACKEND_URL` must be the public Gunicorn service URL. Add the exact frontend origin to the backend's `FRONTEND_ORIGINS` value.

To inspect a production build locally:

```bash
npm run preview
```

## Current limits

- Account selection is not saved across browser sessions.
- Business onboarding is not part of the dashboard.
- The dashboard does not show full DM, comment, lead, or SMS history.
- There is no public demo-data mode.
