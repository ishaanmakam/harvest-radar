# Overfield as a product: accounts, admin, developer API — design

> **Revision, later on 2026-10-18.** Supabase is out. Sign-in, profiles, developer keys, terms, the delivery log and the
> admin pages now live in Overfield's own backend: `intelligence/accounts.py` (SQLite, PBKDF2 passwords, bearer sessions)
> and `intelligence/accounts_api.py` (the routes), served by `intelligence/inbound_server.py` next to the webhooks and
> exposed with a tunnel; the site talks to it through `web/src/backend.js`. Email-link sign-in became email and password,
> because the email provider's rate limits and redirect rules stood between a farmer and their account. The data model,
> roles, key format, limits and the `/v1` endpoints below are unchanged in meaning; read "Supabase" as "the accounts
> database" and `backend/README.md` for how to run it.

Date: 2026-10-18. Status: building. Companion to `2026-10-17-field-inspector-design.md` (the product itself), which still wins on what a plan is and how it is delivered.

## Why

The season pipeline, the decision layer, the morning queue and the map work. What a real product adds on top is the plumbing every company in this space has: a farmer signs in and owns their profile, an officer or admin sees everyone and every delivery, developers get a key and agree to terms before they pull our data, and all of it runs without a laptop on someone's desk. This spec adds exactly that, nothing more.

## Decisions

1. **Supabase holds the people; the files stay the product.** Accounts, farm profiles, send preferences, developer keys, terms acceptances and the delivery log live in one Supabase project (Postgres, row-level security, built-in email-link and phone-code sign-in, free tier). Plans, routes, analytics and yield stay JSON files produced by the Python layer and published with the site. The site stays static on GitHub Pages and talks to Supabase from the browser with the anon key, which is public by design; row-level security is the fence.
2. **The CSV roster stays as the fallback.** `tools.load_roster()` reads from Supabase when `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are set and no explicit path is given, and from `ROSTER_PATH` otherwise. The queue, scheduler, inbound handlers and tests do not change. Rule 5 in AGENTS.md still holds: sends go only to rows in the roster, dry-run first.
3. **The developer API is a thin gate in front of public files.** The JSON files on the site are public already. A key buys a stable, versioned, documented endpoint, usage counts, a daily limit, and a recorded terms acceptance. One Supabase Edge Function (`api`) validates the key, serves the files, and logs use. No second backend.
4. **No billing tonight.** Tiers and who pays are in the product memo (`docs/PRODUCT.md`); nothing in this spec charges anyone.

## Architecture

```
browser (GitHub Pages, Vite)  --anon key-->  Supabase Auth + Postgres (RLS)
                              --Bearer ovf_...-->  Edge Function `api`  --fetch-->  site/out/*.json
python intelligence/           --service role-->  profiles (read as roster), deliveries (write log)
```

## Data model (`backend/supabase/migrations/0001_init.sql`)

| table | holds | who reads | who writes |
|---|---|---|---|
| `profiles` | one row per signed-in person: `roster_id` (R0001...), name, phone (E.164), email, language, role (`farmer`, `officer`, `admin`), `farm_id`, address, lon/lat, `send_time`, `timezone`, `channels`, `sms_consent_at`, `paused` | own row; officers and admins read all | own row (role and roster_id protected); admins update any; a trigger creates the row at sign-up |
| `terms_acceptances` | version, kind (`developer`, `farmer`), accepted_at, user agent | own; admins all | own, via `accept_terms()` |
| `api_keys` | name, `key_prefix`, `key_hash` (sha256; the key is never stored), scopes, `daily_limit`, created, last used, revoked | own (never the hash); admins all | via `issue_api_key()` (needs a current terms acceptance; at most 5 active keys), `revoke_api_key()` |
| `api_usage` | one row per API call: key, path, status, at | own keys; admins all | the edge function (service role) |
| `deliveries` | the morning queue's rows: run_date, roster_id, farm_id, role, channel, status, detail, sent_at, dry_run | own roster_id; officers and admins all | the Python queue (service role) |

Helpers: `is_staff()`, `is_admin()`, `current_terms_version()` (`developer-v1`), `verify_api_key(key)` (returns key id, owner, limit, calls today), `log_api_use()`, `delete_my_account()`, `handle_new_user()` trigger, `protect_profile_fields()` trigger, view `signups_per_day`.

The roster columns the queue needs map one to one: `roster_id, farm_id, name, phone, language, role` plus the optional `email, send_time, timezone, address, lon, lat, created`. A profile is in the roster when it is not paused, has a farm_id, and has a phone or an email.

## Sign-in and the account page

- Sign-in with an email link (Supabase Auth, no password) or a phone code when the project has an SMS provider configured. Sign-up and sign-in are the same screen.
- First sign-in creates the profile row. The account page then asks for name, farm address (geocoded in the browser with Nominatim, matched to a field in `fields.geojson` by point-in-polygon or nearest within 5 km, the same rule as `signup.py`), language, morning time and timezone, channels, and an explicit SMS and call consent checkbox (`sms_consent_at`). Nothing is sent to a number without that consent.
- The account page shows the last deliveries for the person's farm, a pause switch, and "delete my data" (`delete_my_account()`; removes the auth user and, by cascade, every row).
- Replying STOP by text still works and still removes the CSV row; for Supabase rows the inbound handler sets `paused` (follow-up, not tonight).

## Developer tab

- Docs for the endpoints below with curl examples, the terms text (version `developer-v1`), an "I agree" button that records the acceptance, then "Create key". The key is shown once (`ovf_` + 40 hex characters). The list shows prefix, created, last used, usage in the last 7 days, revoke.
- Endpoints (base: `https://<project>.supabase.co/functions/v1/api`), `Authorization: Bearer ovf_...` or `?key=`:
  - `GET /v1/health` (no key)
  - `GET /v1/plans` — every farm's plan for the latest morning
  - `GET /v1/farms/{farm_id}/plan` — one farm
  - `GET /v1/route` — the officer route
  - `GET /v1/landscape` — the landscape analytics block
  - `GET /v1/sources` — the data sources list
- Every response carries `served_at`, `as_of`, `attribution` and `licence`. Limits: `daily_limit` per key (default 1000), HTTP 429 past it, 401 for a missing or revoked key.

## Admin dashboard (officers and admins)

Roster table (name, farm, masked phone, language, channels, send time, consent, paused, role) with search and filters; sign-ups per day; deliveries by run date with counts by channel and status; API keys and usage in the last 7 days; export the roster as CSV in the `ROSTER_PATH` format. Admins can change role and pause; officers read only. Nothing on this page sends a message: sends stay with the queue and the scheduler, dry-run first.

## Web changes (`web/`)

- `@supabase/supabase-js` pinned; `src/supabase.js` creates the client from `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` and exports `configured`. When not configured, the account, developer and admin views render a plain notice that the backend is not set up and point at the CSV and CLI path; everything else works as today.
- New views: `views/account.js`, `views/developer.js`, `views/admin.js`, `views/data.js` (the sources page, from `public/data/sources.json`). Header gets a sign-in button that becomes the person's name, and links Developers and Data. Routes `#account`, `#developers`, `#admin`, `#data` outside the two aspects.
- `.github/workflows/pages.yml` passes `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_API_BASE` and `VITE_SIGNUP_URL` from repository variables.

## Python changes (`intelligence/`)

- `roster_supabase.py`: `fetch_roster(url, service_key, http=None)` reads `profiles` over PostgREST and returns rows in the roster shape; `push_deliveries(url, service_key, doc, http=None)` writes a morning run's rows to `deliveries`. Pure functions over an injected `http(method, url, headers, body, timeout)` so tests need no network.
- `tools.load_roster(path=None)`: when `path` is empty and both Supabase variables are set, rows come from Supabase; validation is unchanged and shared.
- `morning.run()` pushes the run's deliveries to Supabase when configured (dry runs too, flagged), after writing `deliveries.json`.
- `.env.example`: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`. The service role key is a secret and never leaves `.env`.

## What only the owner can do

1. Create the Supabase project. 2. Run the migration in the SQL editor. 3. Enable Email sign-in and set the site URL. 4. Put the project URL and anon key in repository variables, the service role key in `.env`. 5. Deploy the edge function with the Supabase CLI. 6. Make their own account an admin with one SQL update. Steps with commands: `backend/README.md`.

## Out of scope tonight

Billing, organisations and teams, phone OTP without an SMS provider, push notifications, STOP handling against Supabase, API keys with write scopes, multi-region data.

## Risks

- Anon key in the built site: expected and safe only because every table has RLS; the migration enables RLS on every table and the tests of the policies are the SQL comments plus a manual check in the dashboard.
- Nominatim in the browser: one request per sign-up, with the product name as the referer; acceptable volume.
- The edge function fetches the published site; if Pages is down the API is down with it. Acceptable for now.
