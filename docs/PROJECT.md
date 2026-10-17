# Harvest Radar: the full story

Everything the team (and every agent) should know about what we're building, why, and why each decision was made.
Facts are sourced at the bottom. Anything marked **ASSUMPTION** is ours and must be presented as such.

---

## 1. One paragraph

Every October, Punjab's farmers have about two weeks between cutting their rice and sowing wheat. The machines that clear rice straw without fire are scarce, rented from shared hiring centres, and routinely arrive late, so farmers who can't get one in time burn the field. The smoke is a major part of North India's autumn air-pollution crisis. Today's tools either wait for farmers to book a machine (and most centres don't even use the booking apps) or use satellites to catch fires after the fact and fine people. **Harvest Radar uses free satellite imagery to see every rice field in a district, tell which ones were just harvested and which will be next, and turn that into a dispatch plan that gets machines to the right villages before farmers give up and burn.** Perceptron reads the season's satellite time-lapse to confirm when fields were cut and where burn scars appeared.

---

## 2. The story

Picture a rice farmer in central Punjab in late October (an illustrative composite, not a real person). The combine harvester has just gone through. It takes the grain and leaves the stubble standing. Wheat has to go in within about two weeks or yields drop, and the field has to be cleared first. The subsidised machine that would chop the straw into the soil is at the hiring centre in the next village, and so is everyone else's request. He calls. It's booked. He calls again two days later. Still booked. On day ten he strikes a match, because that's the only option that fits inside the window.

That isn't a hypothetical complaint. During one baler shortage, a farmer told reporters he had messaged farm officials and the district office for help, there were no balers, and he couldn't wait any longer to sow. Surveys keep finding the same thing: farmers are willing to stop burning if they have a real alternative, and the alternative fails on **timing and access**, not awareness.

Meanwhile, the satellites overhead already see everything that matters. They see which fields turned from green to bare this week, which are about to, and which have fresh burn scars. Right now that information is mainly used to count fires and penalise farmers. We use the same orbit to **prevent** the fire instead of punishing it.

---

## 3. Background: why Punjab grows so much rice in the first place

- **Rice dominates the summer season.** In the 2022–23 kharif (monsoon) season, paddy covered 3.13 million ha out of 3.59 million ha under kharif crops, over 87%. Rice and wheat together make up about 84% of Punjab's gross cropped area. This season Punjab sowed about **30 lakh hectares** (3 million ha) of paddy.
- **The economics push farmers to rice.** Nearly all paddy is procured by the government at the minimum support price (MSP). The state agriculture director has put rice income at about Rs 45,000–50,000 per acre versus Rs 30,000–35,000 for pulses or maize. Free electricity for irrigation since 1997 makes pumping groundwater nearly costless for farmers.
- **It's draining the aquifer.** Punjab extracts about 34 billion cubic metres of groundwater a year against roughly 20 BCM of recharge, and 119 of 138 blocks are classed as over-exploited. Sangrur, our demo district, is among the worst-hit areas.
- **A water law shrank the window.** The Punjab Preservation of Subsoil Water Act, 2009 pushed paddy transplanting later (by about 10 days on average versus 2002–2008) to save groundwater. Later transplanting means later harvesting, which squeezes the gap before wheat sowing, and farmers commonly sow wheat within 7–10 days of harvesting.
- **Mechanised harvesting leaves the straw.** Combine harvesters take the grain and leave large amounts of stubble standing in the field. This season Punjab expects about **18.81 million tonnes** of paddy straw.

So the structural drivers (MSP, free power, a late-shifted calendar, combine harvesting) aren't going away soon. That makes the **window** a fixed constraint, and the fight is over what happens inside it.

---

## 4. The problem in numbers

**The window is short.** CEEW reports a 15–18 day window between harvest and sowing. Uncertainty over machine availability, delivery timelines and paperwork, combined with that window, often pushes farmers to burn. (CEEW survey of about 1,500 farmers across 11 districts.)

**Why farmers say they burn.** A 2015 survey in Patiala district found 41% of farmers blamed the short window between paddy and wheat, and 48% said burning is quicker and more economical. Another multi-district study listed: too little time between harvest and sowing, labour shortages, no access to suitable machinery, and no local industry that uses straw.

**Machines exist but don't arrive in time.** Punjab has distributed about **1,48,000** crop-residue-management (CRM) machines since 2018–19, and the state is targeting 1,500 new custom hiring centres in 2025–26. CEEW found the hiring-centre model has struggled with **delays in machine availability and inefficient logistics**. A full CRM package (machine plus fuelled tractor and driver) rents for roughly Rs 2,000 per acre, and private centres own about 75% of the CRM machines deployed under the hiring-centre model.

**The harm.**
- *Air:* estimates of stubble burning's share of Delhi's PM2.5 vary widely with method and period: about 14% on average in one recent study, and nearly 40% of near-surface PM2.5 during the 2016 episode in a modelling study. The honest framing: a modest share across the year, a very large share during the October–November peak.
- *Health:* IFPRI researchers found that living in districts with intense crop burning carries a **three-fold** higher risk of acute respiratory infection, with children under five especially affected.
- *Economy:* IFPRI's current release puts the economic loss at about USD 1.5 billion over five years for Punjab, Haryana and Delhi. (Earlier press coverage quoted ~$30B a year; IFPRI has since revised its figures, so don't quote the old number.)

**It's happening right now.** The 2026 burning season has started: Punjab recorded 84 stubble-burning incidents by October 5 (CAQM), led by Amritsar.

---

## 5. What already exists, and why it isn't enough

| Tool | What it does | Why it falls short |
|---|---|---|
| **Unnat Kisan** app (Punjab) | Farmers book CRM machines from nearby hiring centres; over 1.3 lakh machines mapped; village nodal officers pre-assign machines based on farmers' preferences | Reactive: demand is only visible once farmers ask, by which point the window is already closing |
| **i-Khet** (Punjab), **CHC Farm Machinery / FARMS** (central) | Online machine rental marketplaces | Only ~15% of surveyed hiring centres had heard of i-Khet and Unnat Kisan, and fewer than half of those used them |
| **Satellite fire monitoring** (IARI, Punjab Remote Sensing Centre) | Counts active fires from orbit; used for enforcement ("red entries" in land records) | After the fact, punitive, and gamed: fire *counts* fell from 36,663 (2023) to 10,909 (2024), but satellite-measured **burned area stayed almost the same** (~19.2 lakh ha both years), consistent with farmers burning smaller patches to avoid detection. Counts fell further to about 5,100 in 2025 |
| **Research** (NeurIPS 2024 proposal, CERP study in Pakistan's Punjab) | Better burn detection from imagery | Detection, not prevention |

**The gap:** every existing tool is either reactive (waits for a request) or retrospective (detects the fire). We found nothing public that uses satellite harvest progress to **forecast machine demand by village before it arrives**. (Verify this one more time before saying "first" on stage.)

---

## 6. Our solution, and the reason behind every piece

### 6.1 Who it's for
**Primary users: hiring-centre operators and village nodal officers**, the people whose job is already to allocate machines. Not farmers.
*Why:* farmer-facing apps already exist and aren't used, and satellite-advisory research consistently finds that data alone doesn't drive uptake without someone to act on it. Officials are also under real legal and political pressure to cut burning, so the motivation exists. Farmers benefit with zero effort: a machine shows up on time.

### 6.2 Why satellites
Satellites are the only practical way to see **every field, every few days, for free**. Sentinel-2 (ESA) images at 10 m resolution roughly every 5 days; NASA's Harmonized Landsat Sentinel-2 (HLS) merges Landsat 8/9 with Sentinel-2 at 30 m for more frequent looks. October skies over Punjab are mostly clear after the monsoon, so the data is good exactly when we need it.

### 6.3 What we measure
- **NDVI** (vegetation index from red and near-infrared light): high for green rice, drops sharply when a field is harvested. The shape of a field's NDVI curve through the season tells us when it peaked and when it was cut.
- **NBR** (normalized burn ratio, near-infrared vs shortwave infrared): goes strongly negative on burned ground. A burn scar persists for days, unlike a fire that's only caught if it's burning during a satellite overpass, which is why we measure **burned area**, not just fire counts.
- **NASA FIRMS** active-fire detections as a second burn signal.
- **Weather** (NASA POWER observed, Open-Meteo forecast): rain blocks harvesting and sowing, so a forecast storm makes ready fields more urgent.

### 6.4 How we predict harvest dates
Each field's greenness peaks around flowering. We learn this season's **peak-to-harvest lag from fields already harvested** in the same district, then apply it to fields that haven't been cut yet. Neighbouring fields teach the model this year's timing, so we don't need a crop calendar or a field visit. It's simple enough to explain to a judge in one sentence, and it adapts to late or early seasons automatically.

### 6.5 From fields to demand
Fields are grouped into village-scale zones. For each zone and each of the next 14 days, we forecast how many hectares will be harvested and need a machine before their sowing deadline (harvest + window). Already-harvested fields that haven't burned or been sown are the **backlog** that needs a machine now.

### 6.6 From demand to a dispatch plan
We simulate machines moving from hiring centres to zones day by day and compare two policies:
- **Baseline (today):** a job only appears after the farmer harvests and requests (plus a booking delay), each zone is served only by its nearest centre, first come first served.
- **Ours:** jobs are known in advance from satellites, machines are pooled across centres within a service radius, and zones are served earliest sowing-deadline first.
Area whose deadline passes before a machine arrives counts as **burn risk**. The hero metric is how much more area gets served inside the window. Person B is upgrading this from a greedy heuristic to a proper optimization model.

### 6.7 Why Perceptron
Perceptron's models are built for image and video understanding, including timestamped events and spatial annotations. We stack each zone's satellite passes into a time-lapse and ask it **when and where fields were cut and burn scars appeared**, and we use it to double-check fields our index math flags as burned. That speaks directly to the gap between falling fire counts and unchanged burned area.

### 6.8 Engineering choices
- **Data contracts first, mock data for everything**, so three people build in parallel without waiting on each other.
- **Static outputs, no live API calls in the demo**, so nothing breaks on stage.
- **Simulated hiring centres, labelled as simulated**: real centre locations live in Unnat Kisan and aren't public.

---

## 7. Why Punjab (and why Sangrur)

- **It's the epicentre.** Punjab's rice-wheat system, straw volume and burning season are the core of the North India problem, and the most studied, so every claim we make can be sourced.
- **The signal is clean.** A single dominant crop (rice) with a sharp harvest wave in October, under mostly clear post-monsoon skies, is close to the ideal case for satellite monitoring.
- **It's happening this week.** The 2026 season is underway, so our time-lapse shows a real harvest wave in progress, not a historical replay.
- **Sangrur specifically** sits in the central/southern rice belt that's hit hardest by paddy-driven groundwater depletion. It's also a name judges may have read in the news. The demo area is a ~10 × 10 km box defined in `config/region.json`, so switching districts is a config change.
- **Why not the US:** large US farms mostly own their machines, so "the machine didn't arrive in time" rarely causes burning there, and US farm monitoring is a crowded market. The US is useful to us for *validation* (USDA publishes weekly harvest progress we can check our method against), not as the product.

---

## 8. How this scales

**Geographically, the same pipeline runs anywhere a satellite passes over, and Sentinel-2 covers all land every few days.**
1. **All of Punjab:** ~30 lakh ha of paddy. Imagery is free and computation is modest; the work is mostly onboarding hiring centres and nodal officers.
2. **Haryana, Uttar Pradesh, Madhya Pradesh:** the same rice-to-wheat squeeze. In 2024, Madhya Pradesh topped the national stubble-burning list, with Sheopur district alone recording 2,504 fires.
3. **Pakistan's Punjab:** home to about 110 million people, where an estimated 20% of air pollution comes from crop burning during the same narrow two-week window between rice harvest and wheat sowing.
4. **Other residue-burning systems:** the method (detect harvest progress, forecast service demand, allocate shared equipment) transfers to other crops and regions where shared machinery and tight windows collide, such as sugarcane burning in Florida or custom-harvest crews routing across the US Great Plains.

**As a product:**
- **A forecasting layer for existing booking systems.** Unnat Kisan already has the machine inventory and the nodal officers; what it lacks is knowing where and when to pre-assign. Integrating is a far easier adoption path than another app.
- **Verification for incentives.** Farmers have told researchers that direct incentives for *not* burning might work better than machine subsidies. Paying for non-burning requires verifying it field by field, which satellite burned-area monitoring can do at scale.
- **Straw supply chains.** Punjab is rapidly growing ex-situ straw use (industrial boilers, pellet plants). Knowing which fields were harvested this week tells balers and biomass buyers where to collect straw before it gets burned.
- **Who pays:** state agriculture departments (they already fund machines and monitoring), private hiring-centre operators (higher machine utilisation means more revenue), and biomass buyers (feedstock logistics). Farmers pay nothing.

---

## 9. Assumptions and honest limits

**Assumptions in `config/region.json`** (present them as adjustable scenarios, not facts):
- Machine throughput ~3.2 ha/day (~8 acres) per seeder plus tractor.
- 35% of rice area depends on rented hiring-centre machines rather than owned ones.
- Reactive booking adds ~2 days between harvest and a machine being dispatched.
- 15-day sowing window (conservative end of CEEW's 15–18).
- Hiring-centre locations and machine counts are simulated.

**Limits to say before a judge does:**
- Some burning is purely economic; better allocation helps the "no time / no machine" farmers, not the ones who simply find burning cheaper.
- Some farmers distrust the machines: in CEEW's survey, 20% of in-situ users linked lower wheat yields and pest attacks to them.
- We don't create machines; we get more use out of the ones that exist.
- 10–30 m pixels struggle with very small fields, and clouds can hide individual passes.
- Harvest-date predictions are probabilistic; we show a window, not a day.
- The narrower, defensible claim: **more machine-hours delivered inside the sowing window per machine owned.**

---

## 10. Pitch spine (2 minutes)

1. **Hook (15 s):** "Every October, Punjab farmers have about two weeks to clear their fields. When the machine doesn't come, they burn, and North India chokes."
2. **Why it persists (20 s):** machines exist but arrive late; booking apps are reactive and barely used; satellites are used to fine farmers after the fire.
3. **Insight (15 s):** the satellites already see which fields were just cut and which are next. That's a demand forecast nobody is using.
4. **Demo (60 s):** time-lapse of the real 2026 harvest wave in Sangrur → fields coloured by stage → Perceptron confirming harvest and burn timing → tomorrow's dispatch plan → optimized vs first-come-first-served.
5. **Scale + ask (10 s):** a forecasting layer for Unnat Kisan; same pipeline for Haryana, UP, MP and Pakistan's Punjab.

## 11. Likely judge questions

- **"How accurate is the harvest prediction?"** Show `evaluate.py` results on mock ground truth, plus the US validation against USDA Crop Progress if we get to it.
- **"Isn't this just a booking app?"** No: booking apps react to requests; we forecast demand before requests exist, and we work with their existing inventory.
- **"Who uses it?"** Hiring-centre operators and village nodal officers, whose job is already allocation.
- **"Why would farmers trust it?"** They don't need to interact with it; a machine just shows up on time.
- **"Where's the real data vs simulated?"** Imagery, harvest stages, fires and weather are real. Hiring-centre locations and the rented-machine share are simulated or assumed, and labelled as such.
- **"What does Perceptron add over index math?"** It reads the season as a video (when and where change happened) and verifies burn scars, which is exactly where fire-count monitoring has been failing.

---

## 12. Glossary

- **Kharif / rabi:** monsoon (summer) and winter cropping seasons. Rice is kharif, wheat is rabi.
- **Lakh / crore:** 1 lakh = 100,000; 1 crore = 10 million.
- **MSP:** minimum support price, the government's guaranteed procurement price.
- **CRM machines:** crop-residue-management machines (Happy Seeder, Super Seeder, balers, mulchers). *In-situ* = straw stays in the field; *ex-situ* = straw is removed and used elsewhere.
- **CHC:** custom hiring centre, which rents out farm machinery.
- **Nodal officer:** village-level official who coordinates machine allocation.
- **NDVI / NBR:** satellite indices for vegetation greenness and burn severity.
- **Sentinel-2 / HLS / FIRMS:** ESA's 10 m imaging satellites / NASA's harmonized Landsat + Sentinel-2 product / NASA's active-fire detection system.

---

## Sources

- CEEW, *Why Stubble Burning Continues in Punjab* (15–18 day window, ~1,500-farmer survey, 58% in-situ adoption, 20% yield concerns): https://www.ceew.in/blogs/why-punjab-farmers-burn-paddy-stubble-and-steps-to-curb-crop-burning
- CEEW, *Custom Hiring Centres for Better Crop Residue Management* (app awareness, ~Rs 2,000/acre package, private CHCs own ~75%): https://www.ceew.in/node/4668
- CEEW Explains (CHC delays and logistics, FARMS app awareness): https://www.ceew.in/node/2058
- Scroll.in (reasons for burning, willingness to stop): https://scroll.in/article/900152/punjab-farmers-want-to-stop-burning-stubble-that-causes-delhi-pollution-but-they-have-few-options
- Scroll.in (2015 Patiala survey 41% / 48%, 7–10 day sowing, 2009 Act): https://scroll.in/article/1011927/as-farmers-continue-to-burn-stubble-crores-of-rupees-in-subsidies-have-gone-up-in-smoke
- Tribune, baler shortage (farmer messaging officials): https://www.tribuneindia.com/news/punjab/baler-shortage-makes-farmers-burn-crop-residue-across-state-561504/amp
- Tribune, Unnat Kisan (1.3 lakh machines mapped, nodal officers): https://www.tribuneindia.com/news/punjab/punjab-sees-52-drop-in-farm-fire-incidents-since-2020-21/amp
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
- OpenGov Asia (CHC Farm Machinery app, 50 km): https://opengovasia.com/?p=34045
