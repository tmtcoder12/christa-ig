# Contributing

Use Python 3.12 and Node.js 22.13 or newer.

1. Create a branch from `main`.
2. Run `make setup` once.
3. Make a focused change and add tests for changed behavior.
4. Run `make check` before opening a pull request.
5. Explain any database or environment changes in the pull request.

Database changes must be backward compatible. Add a new file under `supabase/migrations`; do not edit an existing migration. Then update `backend/database/schema.sql` so it remains the fresh-install snapshot.

Never commit credentials, customer messages, phone numbers, webhook payloads, or production database exports.
