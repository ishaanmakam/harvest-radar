# Harvest Radar: the full story

Background, rationale and pitch material for the team and every agent. **The product definition lives in the spec:
`docs/superpowers/specs/2026-10-17-field-inspector-design.md`.** This document explains why it matters and how to talk
about it. Facts are sourced at the bottom; anything marked **ASSUMPTION** is ours and must be presented as such.

---

## 1. One paragraph

Small family farms make their biggest decisions with almost no information: when to harvest, whether rain is coming,
whether a corner of the field is failing, and the extension officer who could help covers far more farms than they can
ever visit. **Harvest Radar is an autonomous field inspector. Every few days the satellite season "visits" every farm.
Each morning, every farmer gets a phone call and a text with their farm's plan: eight checks (harvest readiness, harvest
versus rain, establishment, burn scar, fire, flood, stress, visible change), including the ones that came back fine.
The extension officer gets a route: which farms to visit or call today, and why.** Perceptron reads the season's
satellite time-lapse to date and place what changed, and a map shows every farm's flag, plan and delivery status.

---

## 2. The story

Picture a rice farmer in central Punjab in mid-October (an illustrative composite, not a real person). The crop looks
about ready. Heavy rain might be coming, or might not; harvesting wet grain risks spoilage, and waiting too long squeezes
the short window before wheat has to go in. The extension officer for the area is responsible for hundreds of farms and
can't come by. So the farmer guesses.

Overhead, satellites already see the answer to most of those questions: which fields peaked and are browning toward harvest,
which corner has gone pale while the rest stayed green, which field shows a fresh burn scar, which one has standing water.
Weather models know whether 15 mm of rain is coming this week. None of that reaches the farmer, and almost none of it
reaches the officer in a usable form.

Harvest Radar closes that gap. The farmer's phone rings in the morning: "Your rice looks ready, and heavy rain is forecast
on Thursday. Harvest by Wednesday. Everything else checked out: no fire, no flooding, no stress." The officer's phone rings
too, with the three farms that actually need a visit today and why. Quiet farms still hear that everything was checked,
which is how trust gets built for the day something does go wrong.

---

## 3. Background: why Punjab is the demo landscape

The product is for small family farms in general; Sangrur, Punjab is the default demo landscape because it is one of the
best-documented, most intense harvest seasons on Earth, happening right now.

- **Rice dominates the season.** In 2022–23, paddy covered 3.13 million ha of Punjab's 3.59 million ha of kharif crops,
  and this season Punjab sowed about **30 lakh hectares** (3 million ha) of paddy.
- **The economics lock it in.** Nearly all paddy is procured at the minimum support price; rice pays roughly
  Rs 45,000–50,000/acre versus Rs 30,000–35,000 for pulses or maize; free farm electricity since 1997 makes groundwater cheap.
- **The aquifer is paying for it.** About 34 BCM extracted per year against ~20 BCM recharge; 119 of 138 blocks over-exploited.
  Sangrur is among the worst-hit areas.
- **The calendar is tight.** The 2009 subsoil-water law pushed transplanting about 10 days later; farmers commonly sow wheat
  within 7–10 days of harvest, and CEEW reports a 15–18 day harvest-to-sowing window.
- **Fire is part of the season.** Combines leave standing stubble; when time runs out, some farmers burn it. Punjab expects
  **18.81 million tonnes** of straw this season, and 84 burning incidents were recorded by Oct 5, 2026.

Why it suits a satellite product: one dominant crop with a sharp harvest wave, mostly clear post-monsoon skies in October,
and real stakes on every check (harvest timing, rain, fire, burn scars).

---

## 4. The problem in numbers

**Information doesn't reach farmers in a usable form.** Across smallholder surveys, weather is the risk farmers name most
(about 64% in CGAP's Mozambique survey), and research on satellite advisories finds that raw data alone doesn't drive
uptake: farmers need an interpreter such as an agronomist, producer group or extension worker. Our answer is a plain-language
plan delivered by call and text, plus a route that puts the officer where they're needed.

**The harvest window is unforgiving.** Uncertainty about timing, machines and paperwork inside a 15–18 day window pushes
farmers toward bad options (CEEW, ~1,500 farmers across 11 districts). A 2015 Patiala survey found 41% of burners blamed the
short window.

**The harms are real.**
- *Air:* stubble burning's share of Delhi's PM2.5 is modest averaged over the year (~14% in one study) but very large in
  peak episodes (nearly 40% of near-surface PM2.5 in a 2016 modelling study).
- *Health:* living in districts with intense crop burning carries a **three-fold** higher risk of acute respiratory infection
  (IFPRI), especially for children under five.
- *Economy:* IFPRI's current release puts losses at about USD 1.5 billion over five years for Punjab, Haryana and Delhi
  (older "$30B a year" coverage has been revised; don't quote it).

**Satellites are already watching, but only to punish.** Satellite fire counts fell from 36,663 (2023) to 10,909 (2024) to
about 5,100 (2025), yet the burned area measured from orbit in 2024 was almost unchanged from 2023 (~19.2 lakh ha), consistent
with farmers burning smaller patches to avoid detection. Monitoring today is retrospective and punitive; nobody turns the same
imagery into a daily, farm-level plan for the farmer and the officer.

---

## 5. What exists, and the gap

| Today | What it does | Gap |
|---|---|---|
| Satellite fire monitoring (IARI, Punjab Remote Sensing Centre) | Counts fires from orbit; used for enforcement | After the fact, punitive, and gamed by small burns |
| Machine-booking apps (Unnat Kisan, i-Khet, CHC Farm Machinery) | Farmers request residue machines | Reactive; few hiring centres even use them (~15% had heard of the apps) |
| Satellite advisory platforms | Dashboards and maps | Built for people who read dashboards; data alone doesn't get used |
| Extension officers | Human advice and visits | Far more farms than visits; no daily triage |

**The gap:** a daily, farm-level, plain-language plan that reaches the farmer by phone (no app, no literacy needed) and a
route that tells the officer where to go, both generated automatically from free satellite data.

---

## 6. How it works, and why each piece

The spec has the full rules; this is the reasoning to give judges.

- **Satellites as the inspector.** Sentinel-2 images every field at 10 m every few days for free (NASA's HLS adds Landsat for
  more clear looks). "Current" means the latest clear pass plus today's forecast.
- **Indices that read the crop.** NDVI (greenness) peaks around flowering and collapses at harvest; NBR goes strongly negative
  on burned ground, and a scar persists for days, unlike a fire that's only caught during an overpass. NASA FIRMS adds active
  fire points; rainfall and the forecast drive the harvest-versus-rain check (act now when the crop looks ready and any of the
  next 3 days forecasts ≥ 15 mm).
- **Perceptron reads the season like a video.** Mk1.5 watches the time-lapse, draws field polygons, and dates and places what
  changed: flooding, stress, visible change. If its polygons are weak, index math locates the change and Perceptron still
  describes and timestamps it; each plan records which path (`source`) produced it.
- **Eight checks, every morning, including the clear ones.** A quiet farm still hears that harvest, rain, fire, flood and
  stress were looked at. The farm's flag is its worst check (`fine` / `watch` / `act_now`).
- **A route for the officer.** Fires, floods, harvest-before-rain and fresh burn scars first; then stress, visible change,
  readiness, establishment and older scars. Fine farms aren't stops but still get their plan.
- **Delivered where people already are.** A Grok Voice call and a Photon SMS every morning, in the roster's language; the
  officer can ask follow-ups by voice or iMessage; a farmer can call in about their own farm only. Sends go only to numbers in
  the roster, and every attempt is logged in `deliveries.json`.
- **Safe failure.** A cloudy pass keeps the last good plan up with its date; a failed call still sends the SMS; a restarted
  queue never sends the same channel twice in one morning.

---

## 7. Why it scales

- **The data is global and free.** Sentinel-2 covers all land every few days; the pipeline and checks don't depend on Punjab.
  The spec already allows switching to a clearer smallholder landscape if Sangrur's season has too few clear frames.
- **Delivery needs only a basic phone.** Voice calls and SMS work without smartphones, apps or literacy, which is what lets
  this reach small family farms rather than tech-forward ones.
- **One officer, many farms.** The route turns an officer's day from guesswork into triage across every farm in their area.
- **Next regions with the same squeeze:** Haryana, Uttar Pradesh and Madhya Pradesh (MP topped the 2024 burning list; Sheopur
  alone recorded 2,504 fires) and Pakistan's Punjab (~110 million people, ~20% of air pollution from crop burning in the same
  two-week window).
- **Who pays later** (out of scope for this build, but the commercial story): agriculture departments and extension services
  first; cooperatives, buyers, insurers and microlenders later. Farmers never pay.

---

## 8. Honest limits

- 10–30 m pixels struggle with very small or intercropped plots; clouds can hide passes (the plan always carries its pass date).
- Checks are observations and timing, not agronomic prescriptions: "this area changed", "ready", "rain coming".
- Some burning is purely economic; the plan can warn and inform, not force a choice.
- Phone delivery depends on the roster: numbers the team supplies, sends only to those numbers.
- **ASSUMPTION:** the 15 mm / 3-day rain threshold and 5-day fire window come from the spec and should be presented as tunable.

---

## 9. Pitch spine (2 minutes)

1. **Hook (15 s):** "A small farmer's biggest decisions, when to harvest, whether rain is coming, whether something's wrong,
   get made blind. The officer who could help covers hundreds of farms."
2. **Insight (15 s):** satellites already see every field every few days. Nobody turns that into a phone call.
3. **Demo (60 s):** the real Sangrur season time-lapse → every farm's flag on the map → click a farm, eight checks → a real
   phone rings with that plan → the officer's route.
4. **Why it's trustworthy (15 s):** every check every morning, clear ones included; Perceptron dates and places changes; sends
   only to the roster; nothing invented.
5. **Scale (15 s):** free global imagery, basic phones, one officer covering many farms; same pipeline for Haryana, UP, MP,
   Pakistan's Punjab.

## 10. Likely judge questions

- **"How do you know a field is ready / burned / flooded?"** Index rules plus Perceptron on the time-lapse; each plan records its source.
- **"What if it's cloudy?"** The last clear pass stays up and every plan states its date.
- **"Why calls and texts instead of an app?"** Small farmers have basic phones; voice needs no literacy.
- **"What's real vs simulated?"** Imagery, indices, fires and weather are real; the roster is team-supplied numbers.
- **"Why Perceptron over plain index math?"** It reads the season as a video, dating and placing changes (flood, stress,
  visible change) that a single index snapshot can't explain.

---

## 11. Glossary

- **Kharif / rabi:** monsoon and winter seasons (rice is kharif, wheat is rabi). **Lakh:** 100,000.
- **MSP:** minimum support price. **Extension officer:** government agronomist who advises and visits farmers.
- **NDVI / NBR:** satellite indices for greenness and burn severity. **FIRMS:** NASA's active-fire detection.
- **Sentinel-2 / HLS:** ESA's 10 m imaging satellites / NASA's harmonized Landsat + Sentinel-2 product.

---

## Sources

- CEEW, *Why Stubble Burning Continues in Punjab* (15–18 day window, ~1,500-farmer survey, 58% in-situ adoption, 20% yield concerns): https://www.ceew.in/blogs/why-punjab-farmers-burn-paddy-stubble-and-steps-to-curb-crop-burning
- CEEW, *Custom Hiring Centres for Better Crop Residue Management* (app awareness, ~Rs 2,000/acre package, private CHCs own ~75%): https://www.ceew.in/node/4668
- CEEW Explains (CHC delays and logistics, FARMS app awareness): https://www.ceew.in/node/2058
- Scroll.in (reasons for burning, willingness to stop): https://scroll.in/article/900152/punjab-farmers-want-to-stop-burning-stubble-that-causes-delhi-pollution-but-they-have-few-options
- Scroll.in (2015 Patiala survey 41% / 48%, 7–10 day sowing, 2009 Act): https://scroll.in/article/1011927/as-farmers-continue-to-burn-stubble-crores-of-rupees-in-subsidies-have-gone-up-in-smoke
- Tribune, baler shortage (farmer messaging officials): https://www.tribuneindia.com/news/punjab/baler-shortage-makes-farmers-burn-crop-residue-across-state-561504/amp
- Tribune, 2025 season (5,085 cases vs 10,443; 1,48,000 CRM machines; 1,500 CHC target; straw use): https://www.tribuneindia.com/news/punjab/farm-fires-down-to-50-as-season-nears-end
- News On AIR, Sept 2, 2026 (30 lakh ha paddy, 18.81 Mt straw, fire counts 2023–2025): https://newsonair.gov.in/punjab-govt-calls-for-intensified-awareness-campaign-to-reduce-paddy-straw-burning/
- New Kerala / CAQM, Oct 6, 2026 (84 incidents this season): https://www.newkerala.com/news/a/punjab-records-84-stubble-burning-fires-amritsar-accounts-49-203.htm
- Vajiram & Ravi (fire counts vs unchanged burned area, PRSC/PPCB data): https://vajiramandravi.com/current-affairs/stubble-burning-in-punjab/
- Hindustan Times 2019 (farmers burning smaller patches to avoid satellites): https://www.pressreader.com/india/hindustan-times-amritsar/20191002/281728386256605
- Mongabay India (MSP, free power, groundwater, rice area, over-exploited blocks): http://india.mongabay.com/2023/01/punjabs-paddy-dilemma-amid-depleting-groundwater/
- Drishti IAS (2009 Act delayed sowing ~10 days; combines leave stubble; ~40% PM2.5 in 2016 episode): https://www.drishtiias.com/daily-updates/daily-news-analysis/stubble-burning-and-pollution
- Rau's IAS (~14% average PM2.5 share study): https://compass.rauias.com/current-affairs/stubble-burning-contributes-14-pm-2-5-delhi/
- IFPRI (3x ARI risk; ~USD 1.5B over five years, revised): https://www.ifpri.org/news-release/new-study-air-pollution-indias-stubble-burning-leads-usd-15-billion-economic-losses
- ETV Bharat (MP topped 2024 list; Sheopur 2,504 fires; IARI satellite monitoring): https://www.etvbharat.com/en/!bharat/special-force-to-monitor-sheopur-stubble-burning-using-satellite-images-enn25100502890
- CERP (Pakistan's Punjab: ~110M people, ~20% of air pollution from crop burning, two-week window): https://www.cerp.org.pk/?p=38603
- Climate Change AI / NeurIPS 2024 (burn detection with geospatial foundation model): https://www.climatechange.ai/papers/neurips2024/97
- CGAP Smallholder Household Survey, Mozambique (weather as top risk): https://microdata.fao.org/index.php/catalog/1476/variable/F3/V830?name=A60
- Satellite pasture biomass pilot (data alone doesn't drive uptake): https://core.ac.uk/works/38561630


## Beyond Earth: Mars

Overfield reads orbital data, runs a fixed set of checks on every place, and explains its pick in plain words. On
Earth that is a morning plan for 136 farms. On Mars the question is where Starship should land. SpaceX has been asking
it too: in 2019 HiRISE photographed six sites titled "Candidate Landing Site for SpaceX Starship" (five in Arcadia
Planitia and Erebus Montes, one at Phlegra Montes). Independent analysis from public NASA, USGS and PSI data. Not
affiliated with or endorsed by SpaceX.

**The line:** SpaceX scouted six sites in 2019. We graded them on what SpaceX doesn't publicly screen for, then
searched the whole ice belt for alternatives.

### 30-second demo

1. On the opening globe Mars sits beside Earth. Click it, its tag or the Mars pill (or press the right arrow): the
   camera eases over, the legend switches to "Watching Mars" and the copy reads Mars, open. Drag with either mouse
   button to orbit the planet in focus, CAD-style.
2. "Open Mars map". The whole planet between 60 S and 60 N with the score overlay, our ten sites numbered, SpaceX's six
   sites as white rings.
3. Click M01 (52 N, 167 E): the THEMIS frame, then the four checks as go, watch, no-go rows, the same visual language as
   a farm's eight checks.
4. Scroll the dock to "SpaceX's 2019 sites", click ESP_060811_2205: no-go on our science-reach check with everything
   SpaceX screens for passing, and "best within 200 km" with a Fly there button.
5. End on the footer line: "Independent analysis from public NASA, USGS and PSI data. Not affiliated with or endorsed
   by SpaceX."

### The four checks

| check | from | dataset | why |
|---|---|---|---|
| Ice within reach | SpaceX | SWIM 2025 ice consistency, 0-1 m and 1-5 m (PSI) | Water for propellant, air and crops. |
| Low ground | SpaceX | MOLA MEGDR 16 ppd (NASA PDS) | More atmosphere to slow the ship. |
| Plume-safe ground | ours | MGS TES nighttime thermal inertia (Putzig and Mellon, PSI) | Firm soil the engines will not dig out: very low inertia is loose dust, very high is rock. |
| Science reach | ours | USGS SIM 3292 geologic units within about 100 km | More kinds of rock within a rover's range, and rock from the wet early eras. |

Weights 0.35 / 0.20 / 0.25 / 0.20. A cell's status is its worst check, the same rule as a farm's flag. Shown on every
card but not scored: regional slope, latitude, albedo. Every cutoff lives in `pipeline/mars/config.json` and is drawn
on the map's "How we rank" block from `sites.json`.

### What the data said (run of 2026-10-18)

- 2,764,800 cells scored between 60 S and 60 N; 1.4 percent go, 24.2 percent watch, 74.4 percent no-go. Ice is the
  binding factor: the go cells sit in the two ice belts, poleward of about 40 degrees.
- Top three: M01 at 52.3 N, 167.4 E (go, 0.955: firm ground; few rock types within reach); M02 at 49.4 S, 86.1 E (go, 0.945: thick air to brake in; few rock types within reach); M03 at 52.1 S, 78.3 E (go, 0.942: thick air to brake in; few rock types within reach).
- SpaceX's six: one lands in the go band (ESP_060707_2160, Phlegra Montes), four in watch, one in no-go
  (ESP_060811_2205, a single geologic unit within reach). All six pass the ice check, the low-ground check and our plume
  check; the watch and no-go verdicts come entirely from science reach. Arcadia holds up on what SpaceX screens for; our
  factors add the reason to prefer the Phlegra site or to look 50 to 200 km away.

### Blind spots

No sunlight or power factor (SpaceX screens for it; we leave it to them). No rock-hazard factor at landing scale
(HiRISE work). 7 km cells are far coarser than a landing ellipse. SWIM covers 60 S to 60 N only. Thermal inertia is a
regional average; local boulders are invisible at this scale, and about 15 percent of the map is the authors'
interpolated fill.

### Likely judge questions

- *Why only two of SpaceX's factors?* We wanted an independent second opinion, not a copy of their screen. Their other
  two factors, sunlight and flat rock-free ground, are shown on every card as context, labelled not scored.
- *How sure is the ice?* SWIM combines neutron, radar, thermal and landform evidence into a consistency value from -1 to
  +1. It is not a measured depth or an amount; a go means the evidence agrees, not that a drill has hit ice.
- *Why thermal inertia for landing safety?* It separates loose dust from firm ground and rock at regional scale, which is
  what a plume digs into. Boulders at landing scale need HiRISE, which is exactly what SpaceX used.
- *Is any of this mission-ready?* No. 7 km cells; it is a screen that says where to point HiRISE next.
- *Where's the business?* The same engine: read orbital data, run fixed checks, explain the pick. Earth farms today,
  landing sites tomorrow, and the same honesty rules for both.

Slide: `data/out/mars/score_map.png`. Pipeline and terms: `pipeline/mars/README.md`.
