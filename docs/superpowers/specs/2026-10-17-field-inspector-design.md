# Harvest Radar field inspector

Date: 2026-10-17

Harvest Radar is an autonomous field inspector for small family farms. An extension officer and the farmer are both full users. The satellite season does the visiting. People get a morning plan, and a call or a text when something needs action.

The product is for small family farms in general. Sangrur, Punjab is the default demo landscape. It stays the landscape when the season run produces at least four clear frames, which is the minimum the Perceptron probe needs. Below that, the same pipeline runs on the smallholder area with the clearer season. The pitch stays about small family farms.

## What people receive

Every farm gets a plan every morning, including quiet days. The plan says what to do today and what the risks are. Each farm also has a flag:

- `fine` — nothing beyond the ordinary daily plan
- `watch` — keep an eye on it
- `act_now` — harvest before rain, or go look because part of the farm changed

Reasons are `harvest_before_rain`, `visible_change`, or `none`.

The farmer gets a Grok voice call every morning and a Photon SMS with the same plan. The farmer can call in and ask about their own farm. The officer gets a Grok voice call and a Photon SMS with the day’s route: which farms to visit or call, and why. The officer can ask more questions by voice or by Photon iMessage. Voice and text read the same plan files.

Every farm’s plan is on the map. The morning push fires for every row in a roster the team supplies: a real phone number per farm, plus the officer. Sends go only to numbers in that roster.

## Architecture

```
Sentinel-2 season + rainfall + forecast
        ↓
Perceptron Mk1.5 (polygons, change, counts)
        ↓
Decision layer → one plan per farm + one officer route
        ↓
files the map reads
        ↓
morning queue → Grok Voice call + Photon SMS, every roster number
officer route → Grok Voice call + Photon SMS
officer questions → Photon iMessage, same plans
farmer questions → Grok Voice, that farm's plan only
```

The map reads finished files. Panning it does not call Perceptron or Grok. The morning queue and the inbound handlers are the live part. They read those same files and place real calls and texts.

“Current” means the latest clear Sentinel-2 pass, which arrives every few days, plus today’s rainfall forecast. The morning plan is recomputed from those. A newer clear pass replaces the previous one after processing.

Generated plans are a stand-in so the call and text loop can be proven while imagery is still downloading. The first successful real season run rewrites the plan files. After that, the map, the calls, and the texts show that real run. The stand-in is not what plays on stage.

## Components

1. **Season pipeline.** Pulls the Sentinel-2 season, builds true-color frames and vegetation indices, and attaches rainfall plus the forecast.
2. **Perceptron reader.** Mk1.5 watches the season as a time-lapse. It draws field polygons, marks when and where a field changed, and counts fields that look harvest-ready, flooded, or stressed. If the polygons are weak, vegetation-index math locates the change and Perceptron still describes it and puts a time on it.
3. **Decision layer.** Writes one plan per farm and the officer route. The route lists `act_now` farms first, then `watch` farms. Quiet `fine` farms stay on the map and still get their own morning plan; they are not stops on the route. Inside `act_now`, `harvest_before_rain` comes before `visible_change`.
4. **Roster.** Farm, name, phone, language, and role. Stored outside git, same as API keys. The roster has one officer row and one farmer row for every farm in the demo landscape. The team supplies the phone numbers.
5. **Map.** Vite and MapLibre. Time-lapse, field boundaries, the flag on every farm, delivery state, and the plan on click.
6. **Morning queue.** Grok voice plus Photon SMS for every farmer row. Grok voice plus Photon SMS of the route for the officer. Records each attempt.
7. **Inbound handlers.** Farmer voice reads only that farm. Officer voice and officer iMessage read the route and any farm. All three call the same tools.

Sponsor split: Sentinel-2 and the forecast are the space data. Grok Voice API is every live call. Perceptron Mk1.5 is the vision model. Photon 2.0 SMS and iMessage are the text channels. The project is built in Cursor. Grok Imagine is unused. Grok Bot is off the critical path. Photon voice (SIP to Retell, ElevenLabs, LiveKit, or Twilio) is unused because voice stays on Grok.

## Data

A plan stores facts. The roster language is applied when the call or SMS is rendered, so both channels say the same thing.

Season outputs stay the existing contract files: frames, field polygons, per-field history, rainfall, and forecast. Perceptron adds change notes and counts.

`plans.json` — one record per farm:

- `farm_id`
- `as_of` — date of the clear pass this plan uses
- `flag` — `fine`, `watch`, or `act_now`
- `reason` — `harvest_before_rain`, `visible_change`, or `none`
- `source` — `perceptron` or `vegetation_index`
- `today` — what to do today
- `risks` — list, empty when there are none
- `where` — place on the farm, empty when it does not apply
- `change_window` — start and end dates, empty when nothing changed

`route.json` — one morning route:

- `as_of`
- `stops` — ordered list of `farm_id`, `action` (`visit` or `call`), and `why`

`deliveries.json` — one row per attempt:

- `date`, `roster_id`, `channel` (`grok_voice` or `photon_sms`)
- `status` — `pending`, `sent`, `failed`
- `detail` — short provider result

Roster columns: `roster_id`, `farm_id` (empty for the officer), `name`, `phone`, `language`, `role` (`farmer` or `officer`). Language is required on every row.

Tools shared by Grok Voice and Photon:

- `get_plan(farm_id)`
- `get_route()`
- `get_change(farm_id)`

A farmer session may call these only with that row’s `farm_id`. An officer session may call them for any farm.

**Morning run.** Join roster to the current plans and route. Enqueue a voice call and an SMS for each farmer. Enqueue a voice call and an SMS of the route for the officer. Append a delivery row before each attempt. The map shows that status on the farm.

**Inbound.** Caller ID or the iMessage handle maps to one roster row.

**Refresh.** A new clear pass reruns the season and the decision, then replaces `plans.json` and `route.json` only if both outputs validate. A send already in progress finishes on the files it started with. The next morning uses the new files.

## Error handling

A failed piece leaves the last good plans in place.

- **Cloudy or missing pass.** The latest clear pass stays up. Every plan carries that pass’s date.
- **Weak polygons or the Perceptron budget cap.** Vegetation-index math locates the change. Perceptron still describes and timestamps it when budget remains. `source` records which path wrote the plan.
- **Missing farm, missing phone, or a rejected number.** The farm keeps its plan on the map. That delivery is `failed`. The queue continues.
- **Grok call failure.** Two retries after the first attempt. The SMS still goes out. Voice and SMS have separate delivery rows.
- **Photon SMS failure.** Two retries. The voice result is left as it is. If officer iMessage is unavailable, the officer’s voice call and SMS still carry the route.
- **Unknown caller or texter.** The handler says no farm is registered for that number and stops. No other farm is mentioned.
- **Queue interruption.** `deliveries.json` is the checkpoint. Restart continues rows for that date and channel that are not already `sent`. A farm is not sent twice on the same channel on the same morning.

## Testing

The send loop is proven on generated plans first. Real frames attach after that loop is green, without changing the queue or the tools.

- **Contracts.** `plans.json`, `route.json`, and `deliveries.json` validate against schemas. A decision run that fails validation does not replace the previous files.
- **Decision.** A fixed season fixture yields a known flag, reason, and route order. Cases cover harvest-before-rain, visible change, and a quiet farm that still gets a plan.
- **Roster boundary.** A listed number can be sent. A missing number is `failed` and skipped. An unknown caller receives no farm data. A farmer tool cannot read another farm.
- **Queue.** A dry run writes delivery rows and places no calls or texts. A real run records voice and SMS separately. Restarting does not send the same channel twice on the same morning.
- **Perceptron.** The first-hour probe remains the gate: polygons, counts, and a change question on one real frame. The result chooses `perceptron` or `vegetation_index`. Plans generate on either path.
- **Live channels.** One officer number and one farmer number receive a real Grok call and a real Photon SMS from a generated plan. The officer sends one iMessage question and gets an answer from that plan. The same check then runs for the full supplied roster.
- **Map.** After a season run, the time-lapse, the flags, and a farm click match `plans.json`. Delivery status matches `deliveries.json`.

## Build order

1. Generate plans and a route from the stand-in season. Validate them.
2. Dry-run the queue, then send Grok and Photon to the full roster.
3. Process the real Sentinel-2 season and let Perceptron read it. Replace the plan files when validation passes.
4. Point the map at those files and confirm flags, the time-lapse, and delivery status.

## Out of scope

Grok Imagine, Grok Bot, Photon voice calling, machine-dispatch routing as the hero output, and charging farmers. Cooperatives, buyers, insurers, and microlenders are the later commercial story, not part of this build.
