# Overfield product memo: what 33 real products do, and what to ship

Date: 2026-10-18. Repo state read at commit 8b8718f (branch b/intel, same as main).
Scope: 33 products researched (farm data platforms, satellite monitoring, imagery and weather APIs, advisory and helpline services, insurance and compliance programmes). Facts come from the research set and from the repo. Where something could not be found it says unknown.

## Overfield today (baseline for section 2)

What is in the repo at 8b8718f:

- Product: Sentinel-2 season plus an 8-model weather ensemble (Open-Meteo: ECMWF IFS 0.25, GFS, ICON, GEM, Meteo-France, UKMO, JMA, NCEP NBM) become a plan per farm with 8 checks, delivered by voice call (Twilio, Grok voice bridge or text-to-speech) and SMS (Photon), with email as an added channel. Officer gets a route. Languages in code: Punjabi, Hindi, English. Demo region: Story County, Iowa, 136 fields (`config/region.json`, `data/out/fields.geojson`).
- Sign-up: `intelligence/signup.py` and the `/signup` form on `inbound_server.py` turn an address and a phone or email into a CSV roster row with a send time and timezone. The form promises "Text or email STOP at any time to stop." STOP, UNSUBSCRIBE, QUIT, CANCEL and END remove the CSV row (`inbound.py`).
- Accounts backend: `backend/supabase/migrations/0001_init.sql` defines `profiles` (roles farmer, officer, admin; `sms_consent_at`; `paused`; `channels`), `terms_acceptances` (version `developer-v1`, kinds developer and farmer), `api_keys` (`ovf_` plus 40 hex, sha256 hash stored, prefix shown, scopes `{read}`, `daily_limit` 1000, at most 5 live keys, terms acceptance required before issue), `api_usage`, `deliveries`, `delete_my_account()`, a `signups_per_day` view, and row-level security on every table.
- Developer API: `backend/supabase/functions/api/index.ts` serves `GET /v1/health`, `/v1/plans`, `/v1/farms/{farm_id}/plan`, `/v1/route`, `/v1/landscape`, `/v1/sources` behind `Authorization: Bearer ovf_...` or `?key=`. 401 for a missing or revoked key, 429 past the daily limit, 503 when a file is not published. Every response carries `served_at`, `as_of`, `attribution` and `licence` (plans CC BY 4.0).
- Web: the globe, map, season, route, deliveries and yield views exist in `web/src/views`. The account, developer, admin and data views named in `docs/superpowers/specs/2026-10-18-product-accounts-api-design.md` are not in `web/src/views` on this commit. A worktree branch `web-accounts-developer-admin` exists. `web/public/data/sources.json` does not exist on this commit, so `/v1/sources` would return 503 today.
- Not present: billing, organisations or teams, invitations, a help centre, a status page, a written terms page, a privacy page, farmer data export, a per-minute rate limit, `Retry-After`, a request id, an OpenAPI file, a sandbox or test mode, a service worker, a second region.

## 1. Landscape table

Dev API: yes means a public developer path exists. Accounts and admin: yes means login plus documented roles or an org layer; partial means login but roles not documented; no means none found.

| Product | Type | Who pays | Farmer channel | Dev API | Accounts and admin | Notable |
|---|---|---|---|---|---|---|
| Climate FieldView (Bayer) | Farm data platform | Farmer, flat annual; Basic $0, Plus from $649/yr (US) | iOS/Android app (15 languages), web, push, pin share by email or phone | Yes: partner form, clientId, clientSecret, x-api-key, OAuth2 scopes | Yes: farmer owns operation, shares by email; org roles unknown | Per-partner burst, steady and monthly quota; 429 with Retry-After; request id on every response |
| Granular Insights (Corteva) | Agronomy software | Free to farmer; funded by Corteva seed business | Web, iOS, Android; weekly Directed Scouting email | No public API | Yes: farmer invites advisors; roles unknown | Weekly ranked "where to scout first" list to the inbox |
| Farmers Edge / Corvian | Farm data platform, now white-label | Grower per acre ($1.50 to $6.00/acre/yr, IPO era); insurers and retailers as channels | App in 5 languages, web, in-cab, push | No public portal; internal REST reported | Yes: grower accounts, Pro multi-grower dashboard; roles unknown | Automatic flood and hail detection passed to insurers for claims |
| John Deere Operations Center | Machine data platform | Farmer $0; partners pay per Connected Org above free thresholds | Web, mobile push, in-cab displays | Yes: App ID and secret, OAuth2, per-API access request, signed licence for production | Yes: Organization plus Team, access levels 1 to 3, Connections tab | Free API below 10 or 100 org thresholds; sandbox 5 orgs, 150,000 calls/month, 18 months |
| OneSoil | Satellite monitoring | Farmer in-app subscription; Pro per hectare; dealers buy hectare pools | iOS/Android, web, push on new clear image | Partial: B2B REST, token by request | Yes: Owner, Administrator, Editor, Viewer; managed accounts; dealer hierarchy | One ranked daily list of fields needing attention |
| EOSDA Crop Monitoring | Satellite monitoring | Farms per hectare (about $1.25 to $1.40/ha/yr on AWS); API per request | Web, iOS/Android with offline tasks, email NDVI-change alerts | Yes: x-api-key, 1,000-request trial, per request | Yes: Owner, Admin, Scout, Observer; field-scoped; unlimited free members | NDVI change threshold emails; over-allowance fields flagged, not blocked |
| Agremo | Drone analytics | Growers and trial companies; $349 to $3,450/yr | Web, email reports, alerts tab | Enterprise only, via sales | Yes: Owner, Coordinators, Members with isolation | No satellite map above 30% cloud; emergence and index-drop alerts |
| Farmonaut | Satellite monitoring and advisory | Farmers and co-ops via Stripe; $49.99 to $9,499.99/yr | Web, iOS/Android, WhatsApp bot, PDF reports | Yes: self-serve Bearer key with any plan, test mode, webhooks | Partial: account plus Agro Admin user kinds; matrix unknown | WhatsApp "Add Farm" and a pin become a boundary and report in about a minute |
| Taranis | Leaf-level imagery for advisors | Retailer or advisor per subscribed acre | App and web; advisor shares insights by SMS and email | No public API | Yes: per-user scoping to growers and fields, "View and scout" | Yield Impact turns each threat into a bu/ac loss |
| Planet (Planetary Variables) | Imagery and analysis-ready data | Business customers; Insights Flex $1,100 to $12,000/yr (unverified) | None; developer only | Yes: 20 APIs, one key per user, OAuth2 clients | Yes: one org per user, admin invites, user cap per plan | Crop Biomass: daily cloud-free value per field at 6 am local |
| Sentinel Hub / Area Monitoring | EO API and CAP compliance | Paying agencies by contract; developers EUR 30 to 1,000/month | Agency web apps; farmer geotagged photo app | Yes: OAuth client-credentials; Marker Service REST | Yes: operator, expert, admin and farmer apps | Traffic light with an explicit yellow "insufficient evidence" |
| Regrow Ag | Supply-chain MRV | Food and CPG companies; growers are paid to join | Web portal only | Yes: Monitor and Measure, docs behind login | Yes: customer org provisions users; program-scoped links | Public page naming every third-party data set with resolution and region |
| CropX | Soil sensors plus agronomy | Grower: hardware plus about $300/sensor/yr | iOS/Android, web | Custom, via sales | Yes: Owner, Dealer, Operator, Viewer | Dealer pushes recommendations inside the grower's account |
| Cropin | Enterprise agri cloud | Agribusiness, governments, lenders | Field officer app; SMS, WhatsApp and in-app to farmers | Claimed; no public docs | Yes: enterprise hierarchy down to farmer | Harvest readiness, water stress and yield on a 10 m grid |
| xFarm | Farm management | Farmer per farm per year; free Start; EUR 99 to 249 packages | App, web, push, WhatsApp agent | No public portal | Yes: one account per farm; free employees with permissions | Public per-module price list and a status page |
| Agrio | Photo diagnosis and monitoring | Farmers in-app ($4.49 to $349.99 and up); developers $100 per 1,000 credits | iOS/Android, web, push, PDF or link reports | Yes: self-serve bearer token, credit metering | Partial: teams and workgroups; roles not described | Predictive regional alerts with written preventive steps |
| Plantix | Photo diagnosis | Free for farmers; input companies pay | App, WhatsApp bot, community | Partner key after demo | Partial: optional account, phone-number sign-up | Diagnosis before registration; no commission on pesticide sales |
| Digital Green Farmer.Chat | Advisory assistant | Donors; free to farmers | Android app, web; earlier WhatsApp and Telegram | None for Farmer.Chat | Unknown | Under four cents per answer (self-reported) |
| PlantVillage Nuru | Offline diagnosis | Donors; free | Offline Android, SMS in Kenya, radio, lead farmers | None public | Partial: free web registration | Fully offline on-device diagnosis |
| Precision Development (PxD) | Government advisory services | Donors and governments; $0.47 to $0.94 per farmer per year | Weekly outbound IVR, inbound shortcode, SMS via mKisan, radio | None public | Partial: register by calling; roles unknown | Build, operate, transfer to government |
| Kisan Call Centre / Kisan Suvidha | Public helpline | Ministry; free | Toll-free voice in 22 languages, app, SMS | No; transcripts released as open data | Partial: phone number is identity; staff portal | Three-tier escalation; IVRS overflow for missed calls |
| Esoko | Messaging and data collection | NGOs and governments; B2C $0.50/month in Ghana | SMS, voice-SMS, IVR, USSD, call centre | None public | Partial: organisation SSO, agent tracking | Voice-SMS in local languages for non-readers |
| Farmerline | Farmer digitisation and advisory | B2B licences | Voice, SMS, USSD, WhatsApp, IVR 399, field agents | None public | Partial: org accounts, phone-keyed profiles | Voice messages on feature phones |
| Jiva | Bundled advice, credit, offtake | Olam; closed Aug 2025 | App, micro-collectors | None | Partial | Shut down despite about 70,000 users |
| Hello Tractor | Tractor sharing | Owners pay device plus subscription; agents earn commission | Agent app, app, call centre | Yes: least-privilege keys after review, npm SDK | Yes: farmer, agent, owner, operator, technician | Scoped keys shown once, rotated by email |
| EU CAP Area Monitoring | Public compliance monitoring | Paying agencies; farmers nothing | Portal notice plus opt-in SMS; photo apps | No public API; open-source cbm stack | Yes: farmer portal, adviser accounts, agency staff | Notify only on red; yellow means inconclusive |
| PMFBY with YES-TECH | National crop insurance | Farmer 1.5% to 5% of sum insured; Centre and States the rest | Bank, CSC, portal, app, WhatsApp bot, helpline 14447, SMS claim status | No public API | Yes: separate logins per actor | Model-based yield weight raised from 30% to 50% gradually |
| Pula | Index insurance | Governments, input companies, lenders | Agents with the Mavuno app; automatic payouts | None public | Yes: B2B2G dashboards; farmers as records | Bundle with inputs or subsidy; no claim filing |
| ACRE Africa | Micro-insurance | Farmers in cents via scratch cards; donors | USSD, scratch card, M-Pesa, SMS | None public | Partial: phone number is the account | 21-day satellite watch after planting |
| OpenWeather Agro API | Polygon weather and NDVI API | Developers; Free to GBP 200/month (indexed page) | None | Yes: appid key per account | Partial: one developer account | Free tier scoped by hectares and polygons per month |
| Sentinel Hub API (Planet, CDSE) | EO processing API | Developers; CDSE free 10,000 PU/month; Flex $1,100 and up | None | Yes: OAuth clients, secret shown once | Yes: org plans, workspaces | Per-request cost header; 429 with Retry-After in ms |
| Planet (key and terms) | Imagery APIs | Agribusiness $0.35 to $1.80/ha/yr | None | Yes: one key per user; clickwrap terms | Yes: org, workspaces, usage reports | 15-day deemed acceptance of terms changes |
| Open-Meteo | Open weather API | Free non-commercial; USD 29 and 99/month (2023) | None | Yes: keyless free; key on a separate host when paid | No accounts for free use | Published limits, CC BY 4.0, self-host |

## 2. What every real product has that Overfield lacks today

### Accounts and roles

Market: every farmer-facing commercial product has a login that owns fields or an operation and a way to let someone else in. The common shape is a workspace with Owner, Admin, Editor and Viewer (OneSoil), or Owner, Admin, Scout and Observer scoped to fields (EOSDA). Deere adds an Organization with a Team and per-category access levels. Advisors get a role that can create fields and send advice inside the farmer's account but cannot change permissions (CropX Dealer, Cropin field officer). OneSoil and Agremo let a dealer or coordinator run accounts for farmers who have no email. Phone-first services use the phone number as the identity with no password (ACRE, PMFBY, Kisan Call Centre, PxD).

Overfield: the schema has three roles (farmer, officer, admin) with row-level security, but no login page is merged on this commit. There are no organisations, no invitations, no managed accounts for farmers who only have a phone. The CSV roster is the live path.

### Admin dashboard

Market: a per-organisation dashboard is standard for whoever pays. Farmonaut Agro Admin tracks field visits as tasks with done, pending and delayed. Area Monitoring has an Admin app and an Expert Judgement app. Plantix gives B2B customers an HQ dashboard with WhatsApp alerts to field teams. EOSDA shows each member's last-active time. Planet exposes usage through a Reports API. Deere lets an org see every connected app.

Overfield: the design spec defines a roster table, sign-ups per day, deliveries by run date, keys and usage, and a CSV export, with officers read-only and admins able to change role and pause. None of it is in `web/src/views` on this commit.

### Developer API with keys and terms

Market: public developer paths exist at FieldView, Deere, Planet, Sentinel Hub, EOSDA, Farmonaut, Agrio, Hello Tractor, OpenWeather Agro and Open-Meteo. All of them show a key or secret once, publish docs, and bind terms at sign-up or first call. Most publish an OpenAPI file or a Postman collection. Several return `Retry-After` on 429 and a request id or cost header.

Overfield: a key-gated edge function exists with six read endpoints, a daily limit and a required terms acceptance. Missing against the norm: the terms text itself (not found on this commit), a docs page, an OpenAPI file, `Retry-After`, a request id, a per-minute limit, a test mode, key scopes beyond `read`, and the `sources.json` file that `/v1/sources` reads.

### Billing

Market: free tiers are standard (FieldView Basic, Deere, Granular, OneSoil, xFarm Start, Agremo trial, EOSDA sign-up). Paid units are flat per account per year (FieldView $649), per acre or hectare per year (Farmers Edge $1.50 to $6.00/acre; EOSDA about $1.25 to $1.40/ha; Planet Agriculture $0.35 to $1.80/ha), per request or credit (EOSDA, Agrio), per hectare pool (OneSoil dealers), processing units with monthly reset (Sentinel Hub), or a negotiated contract (Regrow, Area Monitoring, Cropin). Open-Meteo and Farmonaut bill through Stripe.

Overfield: none. The design spec says "No billing tonight."

### Support

Market: xFarm has a public status page, release notes and manuals. CropX, Taranis, OneSoil, EOSDA and Agremo run help centres that document roles and subscriptions. FieldView publishes a developer support email. Taranis onboards within 30 days with a webinar. Kisan Call Centre runs 6 am to 10 pm in 22 languages with three escalation tiers.

Overfield: `LIVE_SETUP.md` and `backend/README.md` are internal. No help page, status page, support address or release notes were found.

### Legal and consent

Market: Planet binds terms by signing up or using, with changes deemed accepted after 15 days. CDSE requires ticking terms and privacy before the account exists. Farmonaut binds on account creation or first API call and names every imagery provider's licence. Deere requires a signed API licence for production. FieldView and Planet publish data-ownership principles. The Irish Area Monitoring System sends SMS only to farmers who opted in on their application. Cropin lists ISO 27001, SOC 2 and B Corp on a trust page.

Overfield: `sms_consent_at` and `terms_acceptances` are in the schema and the sign-up form states the STOP promise. No terms page, privacy page, data-ownership statement or Copernicus legal notice page was found on this commit. Attribution text exists only inside the API envelope.

### Data export

Market: Deere and FieldView frame farmer data as the farmer's and let it be shared or withdrawn per connection. OneSoil exports boundaries on paid mobile plans. Agremo owners pull CSV reports. Area Monitoring publishes its whole backend stack. Kisan Call Centre and Digital Green release datasets.

Overfield: `delete_my_account()` exists. The admin CSV export is in the spec only. A farmer cannot download their own plans or delivery history yet. Plans are CC BY 4.0 through the API, which is more open than any commercial product in the set.

### Multi-region

Market: OneSoil claims 180 countries, Farmonaut and Cropin sell by country, Regrow publishes where Monitor is available and swaps data sets by region (PRISM and SSURGO in CONUS, NASA POWER and SoilGrids elsewhere), Planet and Sentinel Hub are global by construction, PxD and Esoko run per-country programmes.

Overfield: one AOI in `config/region.json` (a bounding box over part of Story County) with a `crop_system` switch (corn_soy, rice_wheat). Timezone and language are per profile. There is no region table, no per-region scheduling, and the yield calibration is county-specific (Story County NASS, 2016 to 2022).

### Offline

Market: EOSDA scouting tasks complete offline in the mobile app. Hello Tractor booking agents capture bookings offline and sync later. PlantVillage Nuru diagnoses on the phone with no connection. Farmerline and Pula agents use offline Android apps. OneSoil has offline maps.

Overfield: the officer gets the route by voice call and SMS, which work without data, and the map is a static site with a web manifest but no service worker. There is no offline route view and no offline stop status.

## 3. Where Overfield is different

- Voice and SMS first, no app. None of the four large platforms (FieldView, Granular, Farmers Edge, Deere) sends SMS or places calls as the primary channel. Among the satellite products, only Farmonaut has a no-app channel, and it is WhatsApp. Outbound voice at scale exists only in the government and nonprofit set (PxD weekly IVR, Esoko voice-SMS, Farmerline), and those services carry agronomist content, not per-field satellite checks. No product in the set combines satellite field checks with an outbound call.
- Free for the farmer. This matches the market. No product in the set charges a smallholder more than cents or a few percent of sum insured. Farmers never pay at Granular, Deere, Plantix, PxD, Kisan Call Centre, Area Monitoring, Pula, Regrow, Taranis (billed to the advisor) and Cropin.
- Open source. The pipeline, decision layer, queue, backend schema and web are public. The precedents are the JRC cbm stack (BSD-3) and Open-Meteo (AGPL, self-host). No commercial farm platform in the set publishes its backend.
- Eight named checks every morning, clear ones included. Every rival sends "something changed" alerts (EOSDA NDVI-change emails, Agremo emergence and index-drop alerts, Farmers Edge change-detection push, OneSoil new-image push). Area Monitoring notifies only on red. Nobody in the set sends a daily all-clear listing every check.
- Cross-checking several sources. Overfield's plan combines Sentinel-2 index rules, Perceptron's reading of the time-lapse, FIRMS hotspots, an 8-model forecast ensemble, and NASS county yields for calibration, and records the `source` on each plan. Rivals rest on Sentinel-2 at 10 m with Planet 3 m as a paid add-on; PxD names its forecast models publicly; Regrow names every data set. An 8-model ensemble and FIRMS are not advertised by any satellite monitoring product in the set.
- Officer route. The nearest analogues are Granular's weekly Directed Scouting list, OneSoil's ranked daily list, EOSDA scouting tasks, and Farmonaut Agro Admin visit tasks. None orders stops by a published rule (fire, flood, harvest-before-rain, new scar first) or delivers that order by phone.
- Honest limits already stated in the product: 10 m pixels, cloudy passes keep the last clear date, checks are observations not prescriptions. Area Monitoring (sub-0.5 ha parcels flagged yellow) and Planet's Crop Biomass techspec (0.2 ha minimum) show that publishing limits is the norm among the serious operators.

## 4. Product recommendation

### Next 8 hours (hackathon)

Ship the plumbing that is already designed and committed, in this order. Do not start anything that needs a new backend.

1. Provision the Supabase project and deploy the edge function (owner-only steps in `backend/README.md`, about ten minutes). Without it the account, developer and admin pages render a "backend not set up" notice.
2. Merge the account, developer, admin and data views from the `web-accounts-developer-admin` worktree. Keep the CSV fallback so the mock path and the morning queue do not change.
3. Developer tab: write the `developer-v1` terms text (short; attribution to Copernicus Sentinel-2, Open-Meteo CC BY 4.0, USDA NASS; plans CC BY 4.0; no bulk redistribution of source pixels; keys are credentials; limits may change), an "I agree" button that calls `accept_terms`, "Create key" showing the key once, a key list with prefix, created, last used and revoke, and curl examples for the six endpoints. Add `Retry-After` and an `X-Request-Id` header to the edge function. Both are small changes and match FieldView and Planet.
4. Data page: write `web/public/data/sources.json` so `/v1/sources` stops returning 503, with one row per source: provider, what it feeds, resolution, cadence, licence. The honest count is unknown until this file exists. Sources named in code today: Copernicus Sentinel-2; NASA HLS (Landsat and Sentinel-2 harmonised, via Earthdata); NASA FIRMS (VIIRS and MODIS hotspots); Open-Meteo with eight forecast models and ERA5-Land; USDA NASS county yields via the CropNet dataset (CC BY-NC 4.0, research use) and the NASS API; Fields of the World field boundaries (CC BY 4.0); Copernicus DEM 90 m and SRTM terrain; OpenStreetMap Nominatim geocoding. List the limits the way Planet and Regrow do: 10 m pixels, 3 to 5 day revisit, cloud, small parcels.
5. Account page: name, address matched to a field, language, send time, timezone, channels, an explicit SMS and call consent checkbox, last deliveries, pause, delete. Admin page: roster table with masked phones, sign-ups per day, deliveries by run date, keys and usage, CSV export; officers read-only.
6. STOP against Supabase: set `paused` when the sender is a profile row. One function in `inbound.py`.
7. Do not do tonight: billing, organisations and teams, OAuth clients, a sandbox, write scopes, a second region, offline mode, a status page.

### After the hackathon

- Organisations and teams on the Deere and OneSoil pattern: an extension service is an organisation, officers are its team, farms are connected to it, invitations by email, managed accounts for farmers with only a phone number, hand-over later.
- Officer feedback loop: log visited, called, false alarm per plan check (Area Monitoring's Expert Judgement pattern) and show the history on the farm.
- Farmer evidence path: an SMS or voice reply that asks for a photo of the flagged field (AgriSnap, AMA MFA Fotos, CROPIC, ACRE picture-based).
- Sentinel-1 radar for cloudy weeks (Planet Crop Biomass, Irish AMS) so "last clear pass" becomes a daily reading.
- A region table instead of one `region.json`, per-region schedule, per-region data sets and calibration (Regrow's availability page pattern).
- API maturity: OpenAPI file, Postman collection, per-minute limits, a test mode, webhooks or a subscription endpoint for new mornings (Planet Subscriptions, Deere webhooks, Regrow callbacks), scoped keys, usage dashboard.
- Operations: status page, release notes, support address, help pages (xFarm, CropX).
- Evidence: log cost per farm per morning (SMS, voice, compute) and plan a third-party check of whether farmers act on the plan (PxD and Digital Green win funders with these numbers).

### Tiers and who pays

Farmers never pay. Every product in the set agrees. The paying account is an organisation, and the admin dashboard is built for that buyer.

| Tier | Who | What they get | Unit observed in the market | Note |
|---|---|---|---|---|
| Farmer | Any farm | Morning call and SMS, own plan by voice, account page | Free everywhere in the set | Keep free. Phone number is enough to receive the plan; a login is only for the page. |
| Community developer | Anyone | Key, 1,000 calls/day, CC BY 4.0 plans, attribution required | Open-Meteo: keyless free tier with published limits; Deere and Planet: free thresholds | Already built. Publish the limits. |
| Extension service or agency | County or district office | Officer accounts, roster admin, delivery log, CSV export, officer route, support | Area Monitoring: agency contract; PxD: $0.47 to $0.94 per farmer per year cost; Taranis: per subscribed acre | Price per enrolled farm per season with a free threshold. The price is to be set; no precedent in the set publishes a per-farmer price for this kind of service. |
| Cooperative or agribusiness | Co-op, buyer, input dealer | Same as agency plus white-label reports and a partner logo | Agremo co-branded reports; Farmonaut partner logo; Corvian white-label | Per connected farm per season. |
| Insurer | Crop insurer or reinsurer | Event feed per insured field: flood, fire, burn scar, establishment, with dates | Farmers Edge flood and hail to insurers; Pula and ACRE index triggers; ACRE 21-day establishment watch | Per insured field per season, metered through the API. |
| API metering above the free tier | Developers and integrators | Higher daily limits, webhooks, SLA | EOSDA per request; Agro API by hectares and polygons; Sentinel Hub processing units with monthly reset; Planet 90% quota warning | Per request or per farm per season. Publish the formula. |

Reasoning: Overfield's cost base is free imagery, a free weather API and cheap messaging, so it can stay free for farmers without a subsidy. Jiva shows what happens when a free bundle depends on continued investment: it closed in August 2025 with about 70,000 users. Government and donor services win on cost per farmer and evidence of action, so those numbers should be logged from the first real morning. Insurers already pay for flood and hail detection (Farmers Edge) and for establishment windows (ACRE), which are two of Overfield's eight checks.

## 5. Developer API norms observed

Key formats and issuance:
- FieldView: free developer account after a partner form; credentials are clientId, clientSecret and x-api-key; OAuth2 with space-delimited scopes; backend-only calls.
- Deere: App ID and shared secret per application; OAuth2 scopes reported as ag1, ag2, ag3, eq1, eq2, org1, org2, files, offline_access; the grower grants organisation access on a connections page; production needs review and a signed licence.
- Planet: one active API key per user from account settings, sent as Basic-auth username, `Authorization: api-key` header or `api_key` parameter; treated as a password; OAuth2 clients being introduced and raw keys slated for deprecation.
- Sentinel Hub and CDSE: OAuth2 client-credentials created in the dashboard, secret shown once, optional never-expire with a risk acknowledgement, number of clients capped by plan.
- Farmonaut: key created self-serve once a plan is active, `Authorization: Bearer`, `"isTesting": true` returns unbilled sample data.
- EOSDA: key in an `x-api-key` header after registering on the developer portal or emailing support; trial opened within one business day.
- Hello Tractor: email the developer address with a purpose, agree a scope, receive least-privilege keys in a partner dashboard, shown once, rotated by email; `Bearer` or `Api-Key` headers.
- Agrio: bearer token with credit metering and a Balance endpoint.
- OpenWeather Agro: key under the account's API keys tab, passed as `appid`.
- Open-Meteo: no key for non-commercial use; after Stripe checkout a key and invoice are issued and used as `&apikey=` against a separate host with identical syntax.
- OneSoil: perpetual token or OAuth2 client credentials by request to the team.
- Overfield today: `ovf_` plus 40 hex, shown once, sha256 stored, `Bearer` or `?key=`, 5 live keys.

Terms acceptance:
- Planet: accepted by signing up or using; changes deemed accepted 15 days after notice; Copernicus and USGS collection terms flow through; no caching or tile-grabbing; derived products allowed, source pixels not redistributable.
- CDSE: tick terms and privacy policy at registration.
- Farmonaut: accepted by creating an account or using any API; terms v4.0 dated 7 Oct 2026; names each imagery provider; bans bulk redistribution of raw imagery.
- Deere: signed API License Agreement with a public fee table template for production; sandbox without it.
- FieldView: partner terms agreed with a representative.
- EOSDA: AWS Marketplace EULA for marketplace buyers; developer portal acceptance step not documented.
- Open-Meteo: CC BY 4.0 data with attribution; AGPLv3 code.
- Hello Tractor, OneSoil, Agremo, CropX, Cropin, Plantix: terms acceptance step not published.
- Overfield today: `accept_terms()` records version, kind and user agent; `issue_api_key()` refuses without a current acceptance.

Rate limits and metering:
- Planet Data API: activation 2 req/s, search and download 5 req/s, other 10 req/s; platform limits per plan; 429 with Retry-After; processing units with a published formula (512 by 512 px by 3 bands equals 1 PU); 90% usage notice; no rollover.
- Sentinel Hub: per-plan requests/min and PU/min (Exploration 300 PU/min; Enterprise S 1,000 PU/min and 600 req/min); 429 RATE_LIMIT_EXCEEDED with Retry-After in milliseconds; `x-processunits` header per request; batch costs one third.
- FieldView: per-partner burst limit, steady rate and monthly quota; 429 with Retry-After; `X-Http-Request-Id` on every response; numbers not public.
- Deere: sandbox 5 connected organisations, 1 test account, under 150,000 calls/month, 18 months; production limits reserved and not published.
- EOSDA: trial 1,000 requests; weather 10 requests/min; one field per statistics request up to 365 days; a request may cover up to 200 km2; imagery and zoning are async (create task, poll).
- Open-Meteo free: 600 calls/min, 5,000/hour, 10,000/day, 300,000/month; fractional call counting above 10 variables or 2 weeks per location; usage alerts at 80, 90 and 100% with no hard cutoff yet.
- OpenWeather Agro: Free under 60 calls/min, about 1,000 ha and under 10 polygons/month; Starter 600 calls/min and 4,000 ha; tile requests count as calls.
- CDSE: 10,000 PU/month, 300 PU/min, 300 requests/min free.
- Agrio: 1 credit per diagnosis.
- Overfield today: 1,000 calls per key per rolling 24 hours; 429 without Retry-After; no per-minute limit; no request id.

Docs style:
- OpenAPI files: FieldView (v4.0.11 mirrored on apis.guru plus a GitHub example repo), Farmonaut (OpenAPI 3.1 and a Postman workspace), Agrio (OpenAPI-derived artifacts).
- Reference sites: Planet (docs.planet.com, 20 APIs, Python SDK and CLI), Sentinel Hub (Requests Builder, evalscript examples, Python SDK), EOSDA (Docusaurus with curl, Postman and videos), Hello Tractor (Name, Type, Description tables with curl, JavaScript, Python and PHP tabs, npm SDK), Open-Meteo (interactive URL builder per endpoint), OneSoil (Swagger UI, ReDoc, MkDocs), Deere (per-endpoint pages, script-rendered), Regrow (behind login).
- Async patterns: EOSDA create task then poll; Regrow job_id with callback URIs and 30-day result retention; Planet Subscriptions API with continuous delivery; Deere data-subscription webhooks; Farmonaut webhooks per sensed day.

Pricing observed for APIs:
- Free thresholds: Deere (10 or 100 orgs), Planet trial (30,000 PU and 30,000 requests, no card), Open-Meteo (keyless), OpenWeather Agro (GBP 0 tier), CDSE (10,000 PU/month), EOSDA (1,000-request trial), Farmonaut (test mode).
- Per request: EOSDA (prices unpublished).
- Per credit: Agrio (USD 100 per 1,000).
- Per hectare and polygons: OpenWeather Agro (GBP 20 and 200/month).
- Processing units: Sentinel Hub (EUR 30, 100, 500, 1,000/month list; Flex $1,100, $5,500, $12,000/yr unverified).
- Flat monthly: Open-Meteo (USD 29 and 99 at the 2023 launch; current unknown), Gooey (API on the $25/month Pro tier).
- Negotiated: FieldView commercial API, Deere above thresholds (dollar amounts not printed), Planet Planetary Variables, Regrow, OneSoil, Agremo, CropX, Plantix.

## 6. Account and admin norms observed

Role sets:
- OneSoil: Owner (cannot be removed), Administrator, Editor, Viewer; invitations expire after 24 hours; every member sees all workspace fields; dealers get Master and Regular Consultant under an organisation, create managed accounts for farmers without an account, transfer them later, invite up to 100 emails at once.
- EOSDA: Owner, Admin, Scout, Observer; invite by email scoped to fields or groups; unlimited members at no cost; a user can belong to several teams; last-active time per member; over-allowance fields flagged with a Pro icon rather than removed.
- Agremo: Owner sees all; Coordinators manage members beneath them and cannot see other coordinators' data; Members see own data plus what is shared; enterprise adds hierarchy and co-branding.
- CropX: Owner, Dealer, Operator, Viewer; only the owner edits permissions; farms (not fields) move between users.
- Deere: personal user plus an Organization; Team adds Staff, Operators, Partner Organizations and Dealers by email invite with access levels 1 to 3 per category (Equipment, Organization, Locations, Work, Financial); a Connections tab lists third-party software and removing a connection stops sharing.
- Taranis: one Customer account per retailer with Permitted Users; admin assigns a title, an organisational level (Organization, Client, Farm, Field) and a permission such as "View and scout"; growers see only their own fields.
- FieldView: farmer owns an operation and shares the operation, a farm or single fields by email; API returns only the authenticated user's data.
- Granular: farmer invites team members and trusted advisors.
- xFarm: one account per farm; employees invited free with custom permissions; Multi-Farm on quote.
- Cropin: enterprise tenant with leaders, supervisors, field users and farmers; supervisor-level deviation alerts.
- Hello Tractor: farmer, booking agent, tractor owner, operator, technician, marketplace seller; partner organisations get a key dashboard.
- Planet: a user belongs to one organisation; an admin invites and reactivates; user cap per plan; MFA may be required.
- Regrow: a Customer organisation provisions users; growers register through program-specific links with terms and email verification built in.
- Area Monitoring: operator, expert, admin and farmer roles in separate apps; adviser accounts receive every notification for their clients.
- PMFBY: separate logins for farmers, banks, CSC operators, insurers, state and central administrators; intermediaries enrol farmers through AIDE.
- Phone-as-identity: ACRE (no app, no password), Kisan Call Centre (caller id), PxD (register by calling), Plantix (optional account, phone-number sign-up).

Admin dashboards:
- Farmonaut Agro Admin: operations managers, field supervisors and field staff; task assignment; outgrower visits tracked as done, pending or delayed; per-team completion dashboards.
- Area Monitoring: Admin app, Expert Judgement app where humans resolve ambiguous parcels and those decisions retrain the models; campaign scopes.
- Plantix: HQ dashboard with WhatsApp alerts to field agents.
- Pula and PMFBY: client dashboards and a public graphical dashboard.
- Esoko: remote field-agent management with location tracking.
- Deere: per-organisation connections list; Planet: Reports API for usage.

What this means for Overfield's three roles: farmer sees own farm only (already enforced in the inbound tools and in RLS); officer sees the region and every delivery (matches the Irish adviser account); admin manages the roster, roles, pauses and keys. The missing layer is the organisation that holds officers and farms, and the managed account that lets an officer enrol a farmer who has only a phone.

## 7. Consent and data ownership notes

SMS and call consent:
- Observed: the Irish Area Monitoring System sends SMS only to farmers who opted in on their BISS application. Corvian's site carries a "Sign Up for SMS" link. PMFBY sends claim status by SMS after registration with a mobile number. ACRE confirms by SMS after M-Pesa payment. PxD registers farmers who call in. Kisan Suvidha registers by mobile number, language and district.
- STOP handling: no product in the research set documents a STOP keyword flow. Unknown.
- Overfield: the sign-up form promises STOP; `inbound.py` honours STOP, UNSUBSCRIBE, QUIT, CANCEL and END against the CSV roster; `profiles.sms_consent_at` exists for the Supabase path and the spec says nothing is sent without it. STOP against Supabase is a follow-up. Carrier and country rules for outbound SMS and calls were not part of this research and should be checked before a real send outside the team's own numbers.

Data ownership:
- FieldView: "Your farm. Your data." principles; partners get no open access; growers opt in per partner app.
- Deere: the grower controls which organisations an app may read on a connections page; removing a connection stops sharing.
- Farmonaut: terms forbid bulk redistribution of raw imagery and name each satellite provider's licence.
- Planet and Sentinel Hub: derived products allowed, source imagery not redistributable, no caching or tile-grabbing, Planet copyright notice required, Copernicus Sentinel legal notice flows through; Planet bans using its data to build a competing product with machine learning.
- Open-Meteo: data CC BY 4.0 with attribution such as "Weather data by Open-Meteo.com".
- Regrow: boundaries imported from farm systems are read-only; fields masked across products to prevent double counting.
- Taranis: prohibits automated access outside the customer's own account.
- Plantix: takes no commission on pesticide sales.
- Open data: Kisan Call Centre transcripts under the Open Government Data Licence India; Digital Green datasets on Hugging Face (Apache-2.0); PxD public experiment registry; JRC cbm stack (BSD-3).

Data export:
- Observed: FieldView Data Inbox imports from 60 or more partners; OneSoil boundary export on paid mobile plans; Agremo owner CSV reports; Deere MyTransfer and USB; Area Monitoring publishes its stack.
- Overfield: plans, route, landscape and sources are available through the API under CC BY 4.0; `delete_my_account()` removes the person's rows by cascade; the admin CSV export is designed, not merged; there is no farmer-facing download of their own plans or deliveries.

Who owns field data in Overfield:
- Field boundaries come from Fields of the World (CC BY 4.0) and Sentinel-2 derived indices (Copernicus free and open data). The plan on top is published CC BY 4.0.
- The farmer's profile (name, phone, address, consent, send time) is the farmer's; row-level security limits reads to the person and to staff; the farmer can delete it.
- The NASS county yields used for calibration arrive through the CropNet dataset marked CC BY-NC 4.0, research use. That non-commercial term sits under a CC BY 4.0 output and under any paid tier. Whether the same figures can be taken directly from USDA NASS under its own terms is unknown from this research and should be checked before any commercial tier.
- Perceptron reads the frames; the plan records `source` as `perceptron` or `vegetation_index`. The licence terms of Perceptron output were not part of this research. Unknown.

## 8. Sources

Repo files read: `docs/PROJECT.md`, `docs/superpowers/specs/2026-10-17-field-inspector-design.md`, `docs/superpowers/specs/2026-10-18-product-accounts-api-design.md`, `backend/README.md`, `backend/supabase/migrations/0001_init.sql`, `backend/supabase/functions/api/index.ts`, `intelligence/signup.py`, `intelligence/inbound.py`, `intelligence/inbound_server.py`, `intelligence/roster_supabase.py`, `intelligence/queue_morning.py`, `intelligence/LIVE_SETUP.md`, `config/region.json`, `data/out/analytics.json`, `data/out/weather_ensemble.json`, `web/src/main.js`, `.env.example`.

Climate FieldView
- https://climate.com/pricing
- https://climate.com/en-ca/pricing
- https://climate.com/en-gb/pricing.html
- https://climate.com/features
- https://apps.apple.com/us/app/-/id797902820
- https://dev.fieldview.com/
- https://dev.fieldview.com/faq
- https://dev.fieldview.com/join-us
- https://dev.fieldview.com/technical-documentation
- https://apis.guru/apis/climate-com
- https://climate.com/blog/four-features-to-simplify-your-growing-season
- https://climate.com/blog/five-features-to-simplify-your-scouting-this-growing-season
- https://www.climate.com/blog/field-health-imagery-beyond-the-end-rows
- https://climate.com/blog/fieldview-rainfall-estimates
- https://climate.com/static/cms/farm-data-with-fieldview-prime/
- https://www.airbus.com/sites/g/files/jlcbta136/files/8bc14f3185e652690ed0eb3f8accd90e_EN-Climate-partnership-Press-Release-CIS-08042019.pdf
- https://www.cropscience.bayer.us/en-us/agronomy/fieldview-field-health-imagery.html
- https://climate.com/en-gb/legal/privacy-statement-faq.html

Granular Insights
- https://www.corteva.com/us/products-and-solutions/digital-solutions/granular-insights
- https://www.corteva.com/us/products-and-solutions/digital-solutions.html
- https://www.corteva.com/us/press-releases/granular-launches-free-tool-to-help-farmers-easily-measure-field-level-profitability.html
- https://www.planet.com/pulse/future-of-farming-driving-farmer-value-through-satellite-powered-directed-scouting/
- https://app.swapcard.com/event/canadas-digital-farm-show/product/UHJvZHVjdF80NDEwOA==
- https://agritechobservatory.review.fao.org/ar/granular-insights
- https://support.insights.granular.ag/hc/en-us/articles/360035212871-Granular-Insights-Support-Contact-Information
- https://play.google.com/store/apps/details?id=ag.granular.insights&hl=en
- https://providers.apievangelist.com/providers/corteva/
- https://www.country-guide.ca/?p=119043

Farmers Edge / Corvian
- https://corvian.com/
- https://coverager.com/farmers-edge-launches-corvian/
- https://www.producer.com/sponsored/farmers-edge-launches-farmcommand-pro-to-retailers-and-agronomists/
- https://futurefarming.com/smart-farming/tools-data/farmers-edge-upgrades-digital-platform-farmcommand
- https://www.businesswire.com/news/home/20200227005343/en/Farmers-Edge-Unveils-Generation-Digital-Platform-New
- https://apps.apple.com/us/app/-/id1213936503
- https://divestor.com/?p=10416
- https://www.grainews.ca/daily/minogue-precision-farming-seeks-new-converts-at-new-price-point
- https://goodmans.ca/expertise/case/farmers-edge-inc.-completes-privatization-by-fairfax-financial-holdings-limited
- https://www.businesswire.com/news/home/20231116657046/en/Farmers-Edge-Announces-Fairfax-Privatization-Offer
- https://providers.apievangelist.com/providers/farmers-edge/
- https://brixxs.com/wp-content/uploads/2022/11/Farmers-Edge-API-koppeling.pdf
- https://sti-portal.fao.org/innovations/farmcommand
- https://www.producer.com/?p=198806
- https://birkeys.com/blog/precision-farming-with-farmers-edge--21564

John Deere Operations Center
- https://www.deere.ca/en/technology-products/precision-ag-technology/operations-center/faq/
- https://www.deere.com/en/technology-products/precision-ag-technology/data-management/operations-center/
- https://koenigequipment.com/resources/technology-and-apps/Operations-Center
- https://developer.deere.com
- https://developer.deere.com/precision/get-started
- https://developer.deere.com/ApiAgreementPDF.pdf
- https://developer.deere.com/dev-docs/field-operations
- https://developer.deere.com/dev-docs/boundaries
- https://developer.deere.com/dev-docs/map-layers
- https://docs.withleaf.io/guides/tutorials/connect-john-deere
- https://glama.ai/mcp/servers/jblymd4tbu
- https://vrafy.helpjuice.com/100-basic-operation-center/106-team
- https://www.deere.com/en/news/all-news/operations-center-enhanced-experience
- https://www.planet.com/pulse/john-deere-farmshots-planet/
- https://www.rutlandherald.com/news/business/planet-labs-pbc-receives-john-deere-sustainability-award/article_543a9c32-4539-5787-af50-362abe1a1a2c.html
- https://interactive.satellitetoday.com/via/november-2022/john-deere-tasks-satellite-to-connect-new-frontiers-in-agriculture
- https://apis.io/providers/john-deere/

OneSoil
- https://onesoil.ai/en
- https://onesoil.ai/en/platform
- https://help.onesoil.ai/en/articles/5237693-what-is-onesoil
- https://help.onesoil.ai/en/articles/14885036-mobile-monitoring-subscription
- https://onesoil.ai/en/resources/onesoil-mob-app-is-moving-to-freemium
- https://intercom.help/onesoil/en/articles/6115742-getting-started
- https://api-b2b.prod-be.onesoil.ai/docs/
- https://help.onesoil.ai/en/articles/11812676-using-high-resolution-satellite-imagery-from-planet-labs-in-onesoil
- https://blog.onesoil.ai/en/how-satellite-images-are-turned-into-ndvi
- https://blog.onesoil.ai/en/news-october-2019
- https://www.softwr.com/compare/fieldmargin-vs-onesoil

EOSDA Crop Monitoring
- https://eos.com/products/crop-monitoring/
- https://eos.com/user-guide/crop-monitoring/account-and-pricing/
- https://eos.com/user-guide/crop-monitoring/team-management/
- https://eos.com/user-guide/crop-monitoring/access-through-api
- https://doc.eos.com/docs/quickstart/
- https://doc.eos.com/docs/faq/
- https://eos.com/agriculture-api/
- https://aws.amazon.com/marketplace/pp/prodview-pfwtdfwjlbtto
- https://apps.apple.com/us/app/-/id1474361532
- https://eos.com/blog/field-change-monitoring-with-regular-notifications-taking-care-of-users/
- https://eos.com/blog/risk-tags-context-help-new-in-eosda-crop-monitoring/
- https://eos.com/products/crop-monitoring/for-advisors

Agremo
- https://www.agremo.com/
- https://www.agremo.com/pricing
- https://www.agremo.com/crop-monitoring
- https://www.agremo.com/documentation/agremo-satellite-user-manual/
- https://www.agremo.com/documentation/invite-new-members/
- https://www.agremo.com/documentation/enterprises-and-multiple-user-accounts/
- https://www.agremo.com/documentation/agremo-results-report/
- https://www.agremo.com/doc_support/app-features/
- https://www.agremo.com/usecases/agremos-new-import-export-features-strengthen-connection-with-john-deere-operations-center/
- https://www.agremo.com/solutions/insurance-processor/

Farmonaut
- https://farmonaut.com/
- https://sat.farmonaut.com/pricing
- https://farmonaut.com/farmonaut-satellite-weather-api-developer-docs
- https://sat.farmonaut.com/api
- https://farmonaut.com/home/terms-of-service
- https://farmonaut.com/remote-sensing/whatsapp-your-farm-to-space-farmonauts-2-minute-revolution-in-satellite-farm-mapping
- https://farmonaut.com/agro-admin-app
- https://farmonaut.com/case-study/satellite-crop-advisory-mustard-manipur-case-study
- https://farmonaut.com/asia/revolutionizing-indian-agriculture-farmonaut-and-suminter-partner-for-sustainable-farming-with-ai-and-satellite-technology-in-maharashtra-and-tamil-nadu
- https://agentaya.com/ai-review/farmonaut/
- https://www.techjockey.com/detail/farmonaut-satellite

Taranis
- https://www.taranis.com/
- https://www.taranis.com/service-plans/
- https://www.taranis.com/?p=3680
- https://www.taranis.com/terms-of-service/
- https://knowledge.taranis.ag/portal/en/kb/getting-started
- https://knowledge.taranis.ag/portal/en/kb/articles/what-is-the-myjohndeere-integration
- https://knowledge.taranis.ag/portal/en/kb/articles/invite-new-user-to-the-taranis-platform
- https://www.taranis.com/newsroom/introducing-taranis-yield-impact/
- https://www.taranis.com/newsroom/ag-assistant/
- https://us.fitgap.com/products/056845/taranis
- https://www.farmprogress.com/aerial-imagery/how-better-ai-helps-farmers-make-better-decisions
- https://www.farmprogress.com/farm-progress-show/taranis-ag-assistant-helps-you-make-sense-of-data
- https://thespoon.tech/?p=7759
- https://www.precisionag.com/market-watch/taranis-to-deliver-high-resolution-aerial-imagery-data-to-farmers-through-climate-fieldview/
- https://yespress.io/taranis

Planet (Planetary Variables, Insights Platform, Crop Biomass)
- https://www.planet.com/products/planetary-variables/
- https://docs.planet.com/data/planetary-variables/crop-biomass/techspec/
- https://www.planet.com/pulse/?p=148786
- https://www.businesswire.com/news/home/20240313954189/en/Planet-Upgrades-Crop-Biomass-Planetary-Variable-to-Include-Daily-PlanetScope-Data
- https://www.planet.com/pulse/planet-enables-self-service-purchasing-for-small-customers-on-planet-insights-platform/
- https://pricingsaas.com/companies/planet/diffs/2026W37
- https://docs.planet.com/guides/create-a-trial-account/
- https://www.planet.com/sign-up/agriculture
- https://docs.planet.com/develop/authentication/
- https://docs.planet.com/develop/rate-limiting/
- https://docs.planet.com/develop/apis/data/
- https://docs.planet.com/develop/apis/
- https://www.planet.com/terms-of-use/
- https://support.planet.com/hc/en-us/articles/36956184650909
- https://support.planet.com/hc/en-us/articles/38144064507293

Sentinel Hub / Sinergise Area Monitoring
- https://sinergise.com/en/node/287
- https://www.planet.com/industries/government/agriculture-monitoring/cap
- https://docs.planet.com/data/area-monitoring/
- https://docs.planet.com/data/area-monitoring/markers/
- https://docs.planet.com/data/area-monitoring/traffic-light-system/
- https://docs.planet.com/data/area-monitoring/marker-service-api/
- https://docs.planet.com/data/area-monitoring/applications/
- https://docs.planet.com/data/area-monitoring/references/
- https://docs.sentinel-hub.com/api/latest/api/overview/authentication/
- https://docs.sentinel-hub.com/api/latest/api/overview/rate-limiting/
- https://www.sentinel-hub.com/faq/how-can-i-know-how-many-processing-units-i-need/
- https://creodias.eu/pricing/sh-pricing/
- https://www.sentinel-hub.com/pricing/
- https://www.sentinel-hub.com/tou/
- https://www.sentinel-hub.com/faq/
- https://docs.planet.com/guides/create-a-trial-account/

Regrow Ag
- https://www.regrow.ag/
- https://regrow.ag/platform/mrv
- https://regrow.ag/platform/science
- https://regrow.ag/terms
- https://help.regrow.ag/
- https://help.regrow.ag/monitor-api
- https://help.regrow.ag/getting-started-with-monitor-api
- https://help.regrow.ag/guide-how-to-integrate-with-monitor-api
- https://help.regrow.ag/where-is-monitor-available
- https://help.regrow.ag/monitors-practice-detection-accuracy
- https://help.regrow.ag/crop-detection-methodology
- https://help.regrow.ag/3rd-party-data-sets
- https://help.regrow.ag/account-creation/regsitration-for-mrv-participation
- https://help.regrow.ag/importing-field-boundaries-through-farm-management-systems-fms
- https://help.regrow.ag/integration-of-regrow-mrv-and-mapi-with-sustainability-insights
- https://help.regrow.ag/mrv
- https://help.regrow.ag/sustainability-insights
- https://cloud.google.com/customers/regrow-ag
- https://developers.regrow.ag/docs/monitor-v2/1

CropX
- https://cropx.com/
- https://cropx.com/cropx-platform/farmlook/
- https://cropx.com/cropx-platform/farm-data-connectivity/
- https://help.cropx.com/portal/en/kb/articles/roles-and-permissions-guide-and-setup
- https://help.cropx.com/portal/en/kb/software/subscriptions
- https://www.rfp.wiki/specialty-industries/cropx
- https://us.fitgap.com/products/021310/cropx
- https://www.instrumentchoice.com.au/products/cropx-vertex-all-in-one-vertex-soil-sensor-sv4
- https://providers.apievangelist.com/providers/cropx/
- https://www.calcalistech.com/ctech/articles/0,7340,L-3901381,00.html

Cropin
- https://www.cropin.com/
- https://www.cropin.com/products/
- https://www.cropin.com/intelligent-agriculture-cloud-cropin-apps/
- https://www.cropin.com/intelligent-agriculture-cloud-cropin-data-hub/
- https://www.cropin.com/ai-agents/
- https://cropin.com/cropin-grow-smartfarm-plus
- https://www.cropin.com/press-release/cropin-launches-sage-worlds-first-real-time-gen-ai-powered-agri-intelligence-platform/
- https://inc42.com/?p=468044
- https://community.nasscom.in/communities/digital-transformation/agritech/agritech-case-study-series-smartfarm-by-cropin.html
- https://directory.growasia.org/cropin/
- https://www.adpc.net/icare/wp-content/uploads/2024/03/1.-CropIn-Technology-Solutions-Private-Limited-Brief.pdf
- https://pypi.org/project/cropin-plotrisk-insight-client/0.0.4

xFarm Technologies
- https://xfarm.ag/en/
- https://xfarm.ag/en/versions-and-prices
- https://xfarm.ag/en/xfarm-agent
- https://xfarm.ag/en/precision-agriculture
- https://xfarm.ag/en/xfarm-connect
- https://xfarm.ag/en/documentation
- https://www.xfarm.ag/en/blog-posts/indici-di-vegetazione-cosa-come-e-quando
- https://www.futurefarming.com/smart-farming/tools-data/xfarm-technologies-partners-with-fieldview-to-help-farmers-make-beter-use-of-farm-data/
- https://www.getapp.com/industries-software/a/xfarm/

Agrio
- https://agrio.app/
- https://agrio.app/Agriculture-API/
- https://agrio.app/?p=3873
- https://agrio.app/Collaborative-crop-scouting-tools-for-farm-management/
- https://apps.apple.com/us/app/agrio-plant-diagnosis-app/id1239193220
- https://agritechobservatory.review.fao.org/en/agrio
- https://apis.io/providers/agrio/
- https://providers.apievangelist.com/providers/agrio/

Plantix
- https://plantix.net/en/
- https://plantix.net/en/plantix-intelligence/
- https://plantix.net/en/plantix-intelligence/api-toolkit/
- https://plantix.net/en/plantix-intelligence/crop-insights/
- https://plantix.net/en/press-media
- https://plantix.net/en/blog/plant-disease-detection-whatsapp/
- https://plantix.net/en/imprint/privacy-policy/
- https://plantix.net/en/imprint/terms-and-conditions/
- https://agritechobservatory.review.fao.org/en/plantix-crop-doctor
- https://futurefarming.com/smart-farming/plantix-more-than-a-crop-doctor-driven-by-ai

Digital Green Farmer.Chat
- https://farmer.chat/
- https://digitalgreen.org/farmerchat/
- https://help.gooey.ai/farmerchat
- https://gooey.ai/pricing
- https://gooey.ai/api/
- https://theagencyfund.substack.com/p/bridging-data-divides-at-scale-through
- https://eng.ruralvoice.in/digital-green-ai-farming-assistant-crosses-10-lakh-users
- https://openai.com/index/digital-green
- https://digitalgreen.org/wp-content/uploads/2025/07/Digital-Green-FY25-Annual-Report.pdf
- https://huggingface.co/datasets/DigiGreen/Kenya_Agri_queries/blob/main/20001-30000.csv

PlantVillage Nuru
- https://plantvillage.psu.edu/
- https://science.psu.edu/news/new-ai-app-predicts-climate-change-stress-farmers-in-africa
- https://bigdata.cgiar.org/divi_overlay/plantvillage-nuru/
- https://www.engineeringforchange.org/solutions/product/plantvillage/
- https://iita.org/news-item/african-farmers-get-new-help-against-cassava-diseases-nuru-their-artificially-intelligent-assistant/
- https://www.frontiersin.org/articles/10.3389/fsufs.2021.514785/text
- https://plantvillageplus.coop/
- https://www.foodbusinessmea.com/ai-powered-app-empowers-kenyan-farmers-to-combat-crop-diseases/

Precision Development (PxD)
- https://precisiondev.org/
- https://precisiondev.org/project/ama-krushi/
- https://precisiondev.org/project/scaling-farmers-access-to-accurate-timely-and-relevant-weather-forecasts/
- https://precisiondev.org/project/accelerating-smallholder-access-to-ai-powered-agricultural-advice/
- https://precisiondev.org/wp-content/uploads/2025/05/2024-PxD-Annual-Report_270525.pdf
- https://precisiondev.org/wp-content/uploads/2024/11/2023-PxD-Annual-Report_300824.pdf
- https://agri.odisha.gov.in/en/node/35202
- https://precisiondev.org/wp-content/uploads/2024/12/PAD_AK_A4_Flyer_PhotoVersion.pdf
- https://registry.precisiondev.org/

Kisan Call Centre and Kisan Suvidha
- https://mkisan.gov.in/Home/AboutKCC
- https://mkisan.gov.in/Home/KCCFeature
- https://agritech.tnau.ac.in/kisan/kisan_operationalmechanism.html
- https://budgam.nic.in/service/kisan-suvidha-a-smart-mobile-app-for-farmers/
- https://en.vikaspedia.in/viewcontent/agriculture/ict-applications-in-agriculture/kisan-call-center-app
- https://www.data.gov.in/resource/kisan-call-centre-kcc-transcripts-farmers-queries-answers
- https://aikosh.indiaai.gov.in/home/datasets/details/kisan_call_centre_kcc_transcripts_of_farmers_queries_and_answers.html
- https://dackkms.gov.in/
- https://epubs.icar.org.in/index.php/IJEE/article/view/146208

Esoko
- https://esoko.com/
- https://esoko.com/solutions
- https://www.engineeringforchange.org/solutions/product/esoko/
- https://www.gsma.com/mobilefordevelopment/resources/esoko/
- https://www.scidev.net/sub-saharan-africa/?p=29063
- https://esoko.com/?p=2144
- https://app.dealroom.co/companies/esoko

Farmerline
- https://farmerline.co/
- https://farmerline.co/intelligence/
- https://mergdata.com/
- https://darli.ai/
- https://bigdata.cgiar.org/digital-intervention/mergdata-data-driven-platform-for-last-mile-workers/
- https://www.gsma.com/solutions-and-impact/connectivity-for-good/mobile-for-development/programme/agritech/agronomic-advisory-enhanced-by-ai-insights-from-farmerline/
- https://time.com/7094874/farmerline-darli-ai/
- https://cdrdv2-public.intel.com/870971/Farmerline_121025.pdf
- https://www.xpay.sh/agent-ready-index/mergdata/

Jiva
- https://www.olamgroup.com/news/all-news/press-release/olam-group-to-closedown-jiva-ag-as-part-of-updated-2025-re-organisation-plan.html
- https://agfundernews.com/olam-shutters-digital-farmer-services-platform-jiva-citing-challenging-times-606-employees-impacted
- https://media.dealstreetasia.com/stories/olam-to-shut-down-jiva-ag-454459
- https://blog.google/around-the-globe/google-asia/jiva-25/
- https://digital.growasia.org/solutions/jiva

Hello Tractor
- https://hellotractor.com/
- https://developers.hellotractor.com/docs
- https://developers.hellotractor.com/quickstart
- https://developers.hellotractor.com/partners
- https://developers.hellotractor.com/authentication
- https://developers.hellotractor.com/sdks
- https://developers.hellotractor.com/tractors
- https://beta.hellotractor.com/help/
- https://www.engineeringforchange.org/solutions/product/hello-tractor/

EU CAP Area Monitoring System
- https://www.gov.ie/en/department-of-agriculture-food-and-the-marine/publications/area-monitoring-system-ams/
- https://op.europa.eu/webpub/eca/special-reports/new-tech-in-agri-monitoring-4-2020/en/index.html
- https://www.farmersjournal.ie/focus/nitrates/what-is-the-eye-in-the-sky-doing-in-2025-871564
- https://philab.esa.int/wp-content/uploads/sites/3/2024/12/20241127_ESA_EO_Comm_Austrias_area_monitoring_for_CAP.pdf
- https://agriculture.ec.europa.eu/media/news/commission-continues-simplify-common-agricultural-policy-eu-farmers-2024-07-11_en
- https://github.com/ec-jrc/cbm
- https://www.planet.com/industries/government/agriculture-monitoring/cap

PMFBY with YES-TECH
- https://pmfby.gov.in/
- https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/aug/doc_9735_20260829_18493301.pdf
- https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/feb/doc2025217504301.pdf
- https://www.drishtiias.com/daily-updates/daily-news-analysis/pradhan-mantri-fasal-bima-yojana-11/print_manually
- https://www.impriindia.com/insights/national-crop-insurance-portal-ncip-2025/
- https://www.kshema.co/blogs/pmfby-crop-insurance-india-2026/

Pula
- https://www.pula-advisors.com/
- https://www.pula-advisors.com/productsandservices
- https://www.pula-advisors.com/about
- https://www.pula-advisors.com/post/zambia-s-largest-crop-index-insurance-payout-marks-historic-milestone-for-smallholder-farmers
- https://techcrunch.com/2024/04/14/pula-raises-20m-series-b/
- https://www.bii.co.uk/en/our-impact/investment/pula-advisors-ag-investment-01/
- https://cgap.org/node/1055

ACRE Africa
- https://acreafrica.com/
- https://acreafrica.com/crop-insurance/picture-based-insurance/
- https://acreafrica.com/driving-smallholder-farmers-sustainability-in-africa-through-digital-solutions/
- https://www.worldbank.org/en/news/feature/2022/06/15/disruptive-innovations-boost-uptake-of-agriculture-insurance-solutions-in-kenya
- https://www.globalinclusiveinsuranceforum.org/node/765
- https://unsgsa.org/stories/tanzania-low-cost-digital-insurance-protects-farmers

OpenWeather Agro API
- https://agromonitoring.com/api
- https://agromonitoring.com/api/get
- https://agromonitoring.com/faq
- https://agromonitoring.com/price
- https://agromonitoring.com/api/current-soil
- https://openweathermap.org/price

Sentinel Hub API (Planet Insights Platform, Copernicus Data Space Ecosystem)
- https://docs.sentinel-hub.com/api/latest/api/overview/authentication/
- https://docs.sentinel-hub.com/api/latest/api/overview/rate-limiting/
- https://documentation.dataspace.copernicus.eu/Quotas.html
- https://documentation.dataspace.copernicus.eu/Registration.html
- https://www.sentinel-hub.com/faq/how-can-i-know-how-many-processing-units-i-need/
- https://www.planet.com/pricing/insights-platform/
- https://docs.planet.com/guides/create-a-trial-account/
- https://support.planet.com/hc/en-us/articles/38620583146653-Processing-Units-PUs-What-They-Are-How-They-re-Consumed-and-How-They-re-Allocated
- https://www.earthsystemdatalab.net/wp-content/uploads/2023/11/DeepESDL-PriceList.pdf

Planet (API key and terms model)
- https://docs.planet.com/develop/authentication/
- https://docs.planet.com/develop/apis/
- https://www.planet.com/terms-of-use/
- https://www.planet.com/pricing/agriculture/
- https://www.planet.com/pricing/insights-platform/
- https://docs.planet.com/guides/create-a-trial-account/
- https://www.planet.com/pulse/planet-enables-self-service-purchasing-for-small-customers-on-planet-insights-platform/

Open-Meteo
- https://open-meteo.com/en/terms
- https://open-meteo.com/en/pricing
- https://open-meteo.com/en/features
- https://github.com/open-meteo/open-meteo
- https://openmeteo.substack.com/p/api-subscriptions-for-commercial
