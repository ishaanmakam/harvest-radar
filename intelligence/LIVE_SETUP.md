# Live loop setup (calls and texts)

Everything below is already coded and tested with fakes; this is the checklist to make it real. Nothing in
`intelligence/` sends anything until `.env` is filled and you pass `--real`. Never commit `.env` (it is gitignored).

## 1. `.env` (copy `.env.example`; fill only what you have)

| Key | What | Where to get it |
|---|---|---|
| `ROSTER_PATH` | the roster CSV, outside git (layout in `roster.example.csv`) | you write it: one officer row, one farmer row per demo farm, `language` pa / hi / en |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM` | outbound calls (and webhook signature checks) | Twilio console; `TWILIO_FROM` is a voice-enabled Twilio number in E.164 |
| `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET` | optional: call the API with an API key instead of the master token | Twilio console, API keys |
| `PHOTON_API_KEY`, `PHOTON_PROJECT_ID`, `PHOTON_FROM` | texts through Photon 2.0 REST | app.photon.codes: project API key (`pho_sk_...`), project id (`pho_prj_...`), your SMS number |
| `PHOTON_WEBHOOK_SECRET` | verify Photon's webhook signature | returned once when you create the webhook destination |
| `XAI_API_KEY`, `XAI_MODEL` | Grok translation and the Grok Voice bridge | xAI console (the teammate doing Grok) |
| `PUBLIC_URL` | https base of `inbound_server.py` as seen from the internet | ngrok: `ngrok http 8788` |
| `PUBLIC_WS_URL` | wss base of `grok_bridge.py` | ngrok: `ngrok http 8789` (use the `wss://` form) |
| `INBOUND_TOKEN` | bearer token for `/photon/inbound` and `/tools/*` | any long random string |

Then run the preflight; it sends nothing and tells you exactly what is still missing:

```bash
python intelligence/check_providers.py
```

## 2. Services to run (two terminals, plus ngrok)

```bash
python intelligence/inbound_server.py --data data/mock --port 8788
python intelligence/grok_bridge.py --data data/mock --port 8789
```

`--data data/out` once the real season is in. Both read `plans.json` / `route.json` fresh on every request, so
`refresh.py` can swap files underneath them.

## 3. Twilio number

In the Twilio console, on the voice-enabled number, set the voice webhook (HTTP POST) to one of:

- `PUBLIC_URL/twilio/stream` → the call is connected to the Grok Voice bridge (needs `XAI_API_KEY` + `PUBLIC_WS_URL`).
- `PUBLIC_URL/twilio/voice` → text-to-speech fallback: the caller hears their plan and can ask by speech. Works with
  Twilio alone.

Outbound calls from the queue use the same choice: with `VOICE_TWIML_URL=PUBLIC_URL/twilio/stream` the call streams
to Grok; without it the call speaks the plan with text-to-speech.

## 4. Photon 2.0 (texts, and a Photon number for the voice prize)

1. Dashboard: create a project, add an **SMS number** (voice runs on SMS numbers), create a project API key.
2. Webhook destination: `POST /v1/projects/{projectId}/webhooks/destinations` with
   `{"name": "harvest-radar", "url": "PUBLIC_URL/photon/webhook", "apiVersion": "<from GET .../webhooks/api-versions>", "enabledEvents": ["message.received"]}`
   and an `Idempotency-Key` header; save the returned `signingSecret` as `PHOTON_WEBHOOK_SECRET`.
   The dashboard can do the same. Inbound texts are then answered in the same Space.
3. Voice on the Photon number (Photon's Twilio cookbook): Voice → your number → **Set up default** → SIP profile.
   In Twilio create a Credential List and a SIP Domain whose call handler is a TwiML Bin containing
   `<Response><Connect><Stream url="PUBLIC_WS_URL/media-stream"/></Connect></Response>` (or `<Redirect>` to
   `PUBLIC_URL/twilio/stream`), then enable inbound on the Photon profile pointing at that SIP domain over TLS.
   Outbound through the Photon number: enable outbound on the profile (digest MD5 as the cookbook says) and set
   `TWILIO_FROM` to the Photon number with the SIP `To` form from the cookbook; `providers.TwilioVoice` passes
   the `To`/`From` it is given, so this is a config change, not code.
4. Message text format is `plain`; if Photon answers 422 about the format, set `PHOTON_TEXT_FORMAT`.

## 5. First real run

Start with one roster row (a teammate) and watch the delivery rows:

```bash
python intelligence/morning.py --data data/mock --real --translate --only R001
```

Then the full roster without `--only`. `deliveries.json` is the checkpoint: rerunning never sends the same channel
twice on the same morning, failed rows are retried next run.

## 6. After the event

Rotate anything that was pasted in chat or shown on a screen: the Twilio auth token and API key, the Photon
project key and webhook secret.
