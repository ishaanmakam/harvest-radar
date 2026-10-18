# Overfield accounts: our own backend

Sign-in, farm profiles, developer keys, the terms acceptances, the morning delivery log and the admin pages all live in
one SQLite file served by the Python inbound server, the same process that answers the Photon and Twilio webhooks and
the sign-up page. No third-party account, no email provider, no rate limits we do not set.

| piece | file |
|---|---|
| the store: passwords (PBKDF2-HMAC-SHA256, salted), sessions, profiles, keys, terms, deliveries, admin counts | `intelligence/accounts.py` |
| the HTTP routes, CORS for the static site, the key-gated `/v1` API | `intelligence/accounts_api.py` |
| the server that mounts them next to the webhooks | `intelligence/inbound_server.py` |
| the browser client the site uses | `web/src/backend.js` |
| tests | `intelligence/tests/test_accounts*.py` |

## Run it (demo day)

1. **Start the server** from the repo root. It creates `data/accounts.sqlite` (gitignored) on first run:

   ```bash
   python intelligence/inbound_server.py --data data/out
   ```

   The CSV in `ROSTER_PATH` (the officer and demo farms) and the people who sign up on the site are one roster: the
   morning queue, the scheduler and the inbound handlers read both.
2. **Expose it** with a free Cloudflare quick tunnel (no account; the address changes each time you start it):

   ```bash
   cloudflared tunnel --url http://localhost:8788
   ```

   `winget install Cloudflare.cloudflared` installs it. ngrok works the same way (`ngrok http 8788`).
3. **Tell the site** where the server is. This sets two repository variables and rebuilds the site (two minutes):

   ```bash
   python scripts/publish_backend_url.py https://<the address the tunnel printed>
   ```

4. **Make yourself admin** after creating your account on the site:

   ```bash
   python intelligence/accounts.py promote you@example.com admin
   ```

   `python intelligence/accounts.py list` shows everyone and who is in the roster.

Local work needs none of this: on `localhost` the site talks to `http://localhost:8788` by default.

## What a person gets

- **Account page** (`#account`): create an account with email and password, or sign in. Then the farm address (matched to a watched field, or the nearest one within 5 km), phone with country code, language, morning time, timezone, channels, the SMS and call consent box, a pause switch, the last deliveries, and delete my data.
- **Developers** (`#developers`): the API docs, the terms (`developer-v1`), I agree, create a key (shown once, only its hash is stored), the keys table with usage, revoke.
- **Admin** (`#admin`, officers and admins): count cards, the roster with search and filters, inline role and pause edits (admins), deliveries by morning, keys and usage, a CSV export in the `ROSTER_PATH` shape.
- **STOP** by text pauses the account as well as removing a CSV row.

## The API

Base: the server address. `Authorization: Bearer ovf_...` (or `?key=`).

| endpoint | returns |
|---|---|
| `GET /v1/health` | no key; `as_of` and the endpoint list |
| `GET /v1/plans` | every farm's plan for the latest morning |
| `GET /v1/farms/{farm_id}/plan` | one farm |
| `GET /v1/route` | the officer route |
| `GET /v1/landscape` | the landscape analytics block and the forecast source |
| `GET /v1/sources` | the data sources list |

Answers are `{served_at, as_of, attribution, licence, data}`; 401 without a live key, 429 past the key's daily limit
(1000), 503 while a file is not published. Every call with a live key is logged against it.

## Routes behind the site

`POST /auth/signup`, `POST /auth/login`, `GET /auth/me`, `POST /auth/logout`; `PATCH /me`, `GET /me/deliveries`,
`POST /me/delete`; `GET /terms`, `POST /terms/accept`, `GET /keys`, `POST /keys`, `DELETE /keys/{id}`;
`GET /admin/overview`, `GET /admin/accounts`, `PATCH /admin/accounts/{id}`, `GET /admin/deliveries`, `GET /admin/keys`,
`GET /admin/export.csv`. Sessions are bearer tokens (`ovs_...`) that live 30 days; ten wrong passwords lock an email
for fifteen minutes; thirty sign-ups per address per server run.

## Not done yet

Password reset by email (there is no email provider; an admin can set a new password with a one-line script if
needed), phone sign-in, and running the server somewhere other than the laptop and a tunnel.
