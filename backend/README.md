# Overfield backend: Supabase

Accounts, farm profiles, developer keys, terms acceptances and the delivery log live in one Supabase project. The site stays static; the Python layer reads the roster from here and writes the morning log back. Design: `docs/superpowers/specs/2026-10-18-product-accounts-api-design.md`.

Nothing in this folder contains a secret. The service role key goes in `.env` only; the anon key is public by design and goes in a repository variable.

## One-time setup (about ten minutes, owner only)

1. **Create the project.** supabase.com, New project, name `overfield`, region closest to the farms (us-east for Iowa), generate a database password and keep it in your password manager.
2. **Run the migration.** SQL editor, paste `backend/supabase/migrations/0001_init.sql`, Run. It is safe to run twice.
3. **Sign-in.** Authentication, Providers: Email on (magic link; "Confirm email" can stay on). Authentication, URL configuration: Site URL `https://ishaanmakam.github.io/harvest-radar/`, add the same URL under Redirect URLs (and `http://localhost:5173/` for local work). Phone sign-in is optional and needs an SMS provider in Authentication, Providers, Phone.
4. **Keys.** Project settings, API. Copy the Project URL and the `anon` key into repository variables so the Pages build sees them:

   ```bash
   gh variable set SUPABASE_URL --body "https://<ref>.supabase.co"
   ```

   ```bash
   gh variable set SUPABASE_ANON_KEY --body "<anon key>"
   ```

   ```bash
   gh variable set API_BASE --body "https://<ref>.supabase.co/functions/v1/api"
   ```

   Then append the service role key to `.env` yourself (never commit it):

   ```bash
   echo SUPABASE_URL=https://<ref>.supabase.co >> .env
   ```

   ```bash
   echo SUPABASE_SERVICE_ROLE_KEY=<service role key> >> .env
   ```

5. **Deploy the API.** Needs the Supabase CLI once (`npm i -g supabase` or `npx supabase`), then:

   ```bash
   npx supabase login
   ```

   ```bash
   npx supabase functions deploy api --project-ref <ref> --no-verify-jwt
   ```

   `--no-verify-jwt` is correct: the function checks Overfield keys itself. Optional: `npx supabase secrets set OVERFIELD_SITE=https://ishaanmakam.github.io/harvest-radar/ --project-ref <ref>`.
6. **Make yourself admin.** Sign in on the site once (so the profile row exists), then in the SQL editor:

   ```sql
   update public.profiles set role = 'admin' where email = 'you@example.com';
   ```

7. **Re-run the Pages deploy** (push to main, or Actions, Deploy site, Run workflow) so the build picks up the variables.

## What each piece does

| piece | file | reads | writes |
|---|---|---|---|
| schema and policies | `supabase/migrations/0001_init.sql` | | tables, RLS, functions, triggers |
| developer API | `supabase/functions/api/index.ts` | `api_keys` via `verify_api_key()`, the published `out/*.json` | `api_usage` |
| roster for the queue | `intelligence/roster_supabase.py` | `profiles` (service role) | `deliveries` |
| site | `web/src/supabase.js`, `views/account.js`, `views/developer.js`, `views/admin.js` | own rows under RLS | own profile, terms, keys via RPC |

## Developer API

Base: `https://<ref>.supabase.co/functions/v1/api`. Header `Authorization: Bearer ovf_...` (or `?key=`).

| endpoint | returns |
|---|---|
| `GET /v1/health` | no key needed; `as_of` and the endpoint list |
| `GET /v1/plans` | every farm's plan for the latest morning (the published `plans.json`) |
| `GET /v1/farms/{farm_id}/plan` | one farm's plan |
| `GET /v1/route` | the officer route |
| `GET /v1/landscape` | the landscape analytics block and the forecast source |
| `GET /v1/sources` | the data sources list |

Every response is `{served_at, as_of, attribution, licence, data}`. 401 for a missing or revoked key, 429 past the key's daily limit (default 1000), 503 while a file is not published.

```bash
curl -H "Authorization: Bearer ovf_..." https://<ref>.supabase.co/functions/v1/api/v1/farms/F0001/plan
```

## Checks after setup

- Site: sign in with your email, fill the account page, confirm the row in Table editor, profiles.
- Queue: `python intelligence/morning.py --data data/out` (dry run) lists the Supabase rows; `python intelligence/check_providers.py` shows the roster source.
- API: `curl .../v1/health`, then create a key on the Developers page and call `/v1/plans`.
- Policies: in the SQL editor as an `authenticated` role, `select * from profiles` returns only your own row.
