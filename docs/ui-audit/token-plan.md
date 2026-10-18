# Token plan: Overfield on the SpaceX system

Phase 0 output. Nothing here is built yet. Measured values are in `reference-notes.md`.

## Stack found

- Vite 5, plain JavaScript modules, no framework, no router library. Screens are hash pages (`#account`, `#developers`, `#admin`, `#data`) plus a state store with two aspects (health: fields, timelapse, route, deliveries; yield: yield, season) and the opening globe.
- Styling: one hand-written stylesheet, `web/src/style.css` (about 3,200 lines), custom properties in `:root`. No Tailwind, no component library.
- Maps: MapLibre GL 4.7 (season map and the Mars map). Globe: three.js 0.169 with OrbitControls, satellite.js for orbits. Charts: hand-drawn SVG in `web/src/charts.js`.
- Consequence: every TSX component in the brief has to be translated to the `h()` DOM builder in `web/src/dom.js` and to CSS classes. Tailwind-specific bans (`rounded-xl`, palette classes) do not apply; the equivalent CSS bans do.

## Protected globe paths

```
web/src/globe/globe.js         renderer, camera, controls, lights, markers, fly-in
web/src/globe/atmosphere.js    atmosphere shader
web/src/globe/satellites.js    SGP4 propagation, satellite points, ground tracks, the satellite panel DOM
web/src/globe/worlds.js        Mars in the scene, focus tween, copy swap
web/src/globe/worlds-front.js  Mars placement maths
web/src/globe/geo.js           lat/lon to vector maths
web/src/globe/no-wasm.js       satellite.js alias
web/src/assets/earth/*         textures
web/public/sats/*.json         orbital elements (scripts/fetch_tles.py writes them)
web/src/places.js              marker data
```

Not protected: `web/src/globe/region-card.js` (DOM card only), `web/index.html`, `web/src/style.css`, `web/src/main.js`.

Open question for the globe: the stars behind it are drawn inside `globe.js` (protected), and Section 4.1 bans a decorative starfield. Leaving it untouched follows constraint 0.1; removing it needs a decision from you. The satellite panel is also DOM built inside `satellites.js`, so restyling it through CSS is allowed but changing its markup is not.

## Final tokens

Colours (chrome): `--void #000000`, `--fg #f0f0fa`, `--fg-dim rgba(240,240,250,.70)`, `--fg-faint rgba(240,240,250,.50)`, `--rule rgba(240,240,250,.16)`, `--rule-strong rgba(240,240,250,.35)`, `--ghost rgba(240,240,250,.10)`.

HUD states: `--hud-correction #5bd75b`, `--hud-rate #4fb3ff`, `--hold #ffb020`, `--abort #ff4d3d` (hues still to be checked against iss-sim.spacex.com).

Data ramps: NDVI BrBG, moisture Blues, stress 0 to 3, dNBR classes, all as listed in Section 3. The existing farm flags map to HUD states: `act_now` = abort, `watch` = hold, `fine` = white.

Type (D-DIN 400 and 700 only): hero `clamp(2.25rem, 1.4rem + 3.2vw, 4rem)` 700 uppercase 0.02em (0 at the largest size, per the live site); h2 `clamp(1.75rem, 1.2rem + 1.8vw, 2.75rem)`; h3 1.25rem 0.06em; stat `clamp(2.5rem, 1.6rem + 3vw, 4.5rem)`; readout 1.75rem; label 0.8125rem 700 0.09em uppercase (matches the measured nav links); micro 0.6875rem 400 0.1em uppercase; body 1rem/1.6 sentence case; numbers `font-variant-numeric: tabular-nums`.

Spacing: 4, 8, 12, 16, 24, 32, 48, 64, 96, 128. Gutter `clamp(24px, 8vw, 128px)`. Nav 72px.

Radius 0 everywhere except the ghost button at 4px (measured live). Shadows none. Blur none. Gradients only the scene scrim, the nav scrim and data legends.

Ghost button (live-measured): 49px tall, padding 0 20px, 1px border at `--rule-strong`, background rgba(0,0,0,.5), label style, fill sweep on hover to be confirmed against the live hover.

## What the current interface breaks

Counts from `rg` over `web/src` on 18 Oct: `border-radius` 77, `backdrop-filter` 20, `box-shadow` 9, middle dots in strings 31, one ellipsis, one em dash, `--font: Inter` with a `--mono` stack used for numbers, pill buttons everywhere, translucent panels on the globe, coloured status chips on every flag. All of this goes in Phases 1 to 5.

## ASCII wireframes

Landing (the opening globe is the only landing; there is no separate marketing page):

```
+------------------------------------------------------------------------------+
| OVERFIELD              FIELDS   MARS   DATA   ACCOUNT                   MENU |
|                                                                              |
|                                 [ GLOBE, UNCHANGED, FILLS THE VIEWPORT ]     |
|                                                                              |
|                                                                              |
| EVERY FIELD, EVERY PASS                                                      |
| {MISSION_LINE}                                                               |
| STORY COUNTY, IOWA / LATEST CLEAR PASS 17 OCT / 136 FIELDS / 0 ACT NOW       |
| [ ENTER THE SEASON ]   OPEN A REGION                       EARTH | MARS      |
| LAT LON READOUT                                   SATELLITE PANEL (restyled) |
+------------------------------------------------------------------------------+
| SOURCE STRIP: Sentinel-2 L2A / FIRMS / NASA POWER / Open-Meteo / CelesTrak   |
+------------------------------------------------------------------------------+
```

Main app screen (season, health aspect):

```
+------------------------------------------------------------------------------+
| OVERFIELD   FIELDS  TIMELAPSE  ROUTE  DELIVERIES   |  YIELD  SEASON     MENU |
|                                                           +------------------+
|                                                           | FIELD MANIFEST   |
|        [ SEASON MAP, MAPLIBRE, FILLS THE VIEWPORT ]       | F0001  maize  GO |
|                                                           | F0002  soy  HOLD |
|                                                           | ...              |
|                                                           | 8 CHECKS / FIELD |
| LAT 42.02500 N                                            |  as GO / HOLD /  |
| LON 93.47000 W                                            |  NO-GO rows      |
| NDVI 0.712            PASS 17 OCT S2A 4% CLOUD            |                  |
| [ TRUE COLOR | NDVI | NBR ]   NDVI legend with probe      | DELIVERY LOG     |
+-----------------------------------------------------------+------------------+
| SOURCE STRIP                                                                 |
+------------------------------------------------------------------------------+
```

## Conflicts to decide before Phase 1

1. Timing: the hackathon freeze is 09:30 PT on 18 Oct. Phases 1 to 6 cannot land before it; Phase 0 is the only phase that fits.
2. Stack: no React, no Tailwind. The component code is reference only; the build is DOM builders plus CSS.
3. Globe: the starfield and the satellite panel markup live in protected files.
4. Ghost button: live site has a 4px radius and a 1px border; Section 3 says 0 and 2px. Live wins.
5. Hero tracking: live is negative at 60px. Proposed 0 at the largest size, 0.02em otherwise.
6. Agent surfaces: the product has no chat UI. The agent is the morning queue (SMS, voice, email) and the inbound handlers in `intelligence/`. Components 29 to 31 have no screen to land on; the output gate (Section 4.13) applies to `intelligence/` message templates instead.
7. Data without a component yet: soil moisture (no source), wind (check `weather.json`), dNBR classes (the pipeline has NBR series and a burn-scar check, not the seven-class map), a 5-year NDVI median (one season only).
8. D-DIN: to self-host from Datto under the SIL OFL; licence file to be added and confirmed.
