# Reference notes: the SpaceX web system, measured

Phase 0 of the flight-grade overhaul. Values below were read from computed styles in a browser on 18 Oct 2026, then cross-checked against the third-party write-up at design.hagicode.com. Where the two disagree, the live site is recorded first.

## spacex.com, home, 1280 x 800

| Property | Live value | Note |
|---|---|---|
| Page background | #000000 | body |
| Text colour | #f0f0fa (rgb 240 240 250) | body |
| Body typeface | D-DIN, fallbacks Arial, Verdana | `document.fonts` also loads D-DIN-Bold and Roboto Mono |
| Nav bar height | 73 px | `.header`, position fixed, transparent, top scrim `linear-gradient(rgba(0,0,0,.6) 0%, rgba(0,0,0,0) 95%)` |
| Nav links | D-DIN 700, 13.0 px, letter-spacing 1.17 px (0.09 em), uppercase, colour rgba(240,240,250,.9) | matches `.t-label` in Section 3 |
| Hero headline (scene 1) | D-DIN-Bold, 60 px, line-height 54 px (0.9), letter-spacing -1 px, uppercase | tracking is negative at this size |
| Scene 2 headline | D-DIN-Bold, 80 px, line-height 76 px, letter-spacing 1.6 px (0.02 em), uppercase | |
| Scene 3 headline | D-DIN-Bold, 48 px, line-height 60 px, letter-spacing 0.96 px (0.02 em), uppercase | |
| Ghost button (`.spx-button.primary`) | height 49 px, padding 0 20 px, border 0.67 px solid rgba(240,240,250,.35), radius 4 px, background rgba(0,0,0,.5), text D-DIN 400 16 px, content uppercase | hover not measured |
| Button labels seen | WATCH, EXPLORE, LEARN MORE | the live site uses LEARN MORE; Section 4.11 bans it for us |
| Scene structure | full-viewport photographic sections, text block lower-left, button under the text | |
| Menu | hamburger at the right end of the nav; no `aria-label="menu"` element found by the probe | drawer behaviour not measured |

## spacex.com, home, 541 px wide (the pane width)

| Property | Live value |
|---|---|
| Body font size | 10 px |
| Nav links | D-DIN 400, 12 px, uppercase, letter-spacing normal |
| Hero headline | 60 px, letter-spacing -1 px, uppercase |
| Ghost button | 49 px tall, 10 px text, border and radius as above |

## Third-party write-up (design.hagicode.com), for cross-checking only

| Property | Value given |
|---|---|
| Display typeface | D-DIN-Bold; body D-DIN, fallbacks Arial, Verdana |
| Weights | 700 and 400 only |
| Hero | 48 px, 700, line-height 1.0, letter-spacing 0.96 px |
| Body | 16 px, 400, line-height 1.5 to 1.7 |
| Nav link bold | 13 px, 700, letter-spacing 1.17 px |
| Nav link | 12 px, 400 |
| Micro label | 10 px, 400, letter-spacing 1 px |
| Text colour | #f0f0fa |
| Button | background rgba(240,240,250,.1), border 1 px solid rgba(240,240,250,.35), radius 32 px, padding 18 px |
| Overlay gradient | rgba(0,0,0,.5) |
| Logo | 147 x 19 px |
| Spacing | base 8 px; 3, 5, 12, 15, 18, 20, 24, 30 px |
| Breakpoints | <600, 600 to 960, 960 to 1280, 1280 to 1350, 1350 to 1500, >1500 |
| Shadows | none |

The write-up's button (radius 32 px, light fill) does not match the live site (radius 4 px, dark fill). The live site wins.

## Not measured in Phase 0

- spacex.com/vehicles/falcon-9 (spec sheet, METRIC / IMPERIAL toggle), spacex.com/launches, spacex.com/updates, starlink.com and starlink.com/map, iss-sim.spacex.com HUD hues. Each needs its own browser pass; the probes above took the time available before the 18 Oct freeze.
- Button hover direction and timing.
- Menu drawer open and close behaviour.
- Mobbin: no MCP server connected, skipped.

## Where Section 3 disagrees with the live site

1. Ghost button: Section 3 says radius 0, 2 px border, transparent fill, 52 px tall, 32 px side padding. Live: radius 4 px, about 1 px border at 35% white, 50% black fill, 49 px tall, 20 px side padding. Section 3's own rule says the live site wins, so the button keeps the 4 px radius and the thin border.
2. Hero tracking: Section 3 fixes 0.02 em positive. Live uses -1 px at 60 px and +0.02 em at 48 and 80 px. Proposed: 0.02 em everywhere except the single largest hero size, which gets 0.
3. Monospace: Section 3 bans any monospace face; the live site loads Roboto Mono. We follow Section 3 (no monospace) because our numbers can use tabular figures in D-DIN.
4. Nav height: Section 3's 72 px matches the measured 73 px; keep 72.
5. Label style: Section 3's `.t-label` (13 px, 700, 0.09 em, uppercase) matches the measured nav links exactly.
