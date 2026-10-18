import { test } from "node:test";
import assert from "node:assert/strict";
import { regionLines, regionPill, HONESTY_LINE } from "../src/globe/region-card.js";

const region = {
  id: "mx-sinaloa", name: "Culiacan Valley, Sinaloa", country: "Mexico", lat: 24.8, lon: -107.4,
  crops: ["maize", "vegetables"], crops_note: "hand-labelled, not detected",
  clear_passes: 31, latest_clear_date: "2026-10-13",
  peak: { date: "2026-07-21", value: 0.84 }, current: { date: "2026-10-13", value: 0.229 },
  stage: "recently harvested or senesced",
  rain_next3_mm: 1.0, rain_flag: false, alert: null, fires_5d: 0,
  thumb: "thumbs/mx-sinaloa.jpg",
  headline: "Recently harvested or senesced: NDVI 0.23, 84 days past a 0.84 peak on Jul 21; 1 mm of rain in the next 3 days; no fires.",
};

test("the card lines name the stage, the canopy numbers, the weather, fires and the record", () => {
  const lines = regionLines(region);
  const text = lines.map((l) => `${l.label}: ${l.value}`).join("\n");
  assert.match(text, /Stage: Recently harvested or senesced/);
  assert.match(text, /Canopy: NDVI 0\.23 now, peak 0\.84 on 21 Jul/);
  assert.match(text, /Rain next 3 days: 1 mm/);
  assert.match(text, /Fires, last 5 days: none/);
  assert.match(text, /Crops: maize, vegetables \(hand-labelled\)/);
  assert.match(text, /Clear passes: 31, latest 13 Oct/);
});

test("an alert and fires show up as such", () => {
  const lines = regionLines({ ...region, alert: "Rain before harvest", rain_next3_mm: 18.4, rain_flag: true, fires_5d: 3 });
  const text = lines.map((l) => `${l.label}: ${l.value}`).join("\n");
  assert.match(text, /Alert: Rain before harvest/);
  assert.match(text, /Rain next 3 days: 18 mm, watch/);
  assert.match(text, /Fires, last 5 days: 3/);
});

test("missing numbers are left out rather than shown as NaN", () => {
  const lines = regionLines({ ...region, peak: null, current: null, rain_next3_mm: null, clear_passes: null, latest_clear_date: null });
  const text = lines.map((l) => `${l.label}: ${l.value}`).join("\n");
  assert.doesNotMatch(text, /NaN|undefined|null/);
  assert.doesNotMatch(text, /Canopy:/);
});

test("the pill says region watch with the country, and the honesty line says where the curve comes from", () => {
  assert.equal(regionPill(region), "Region watch / Mexico");
  assert.match(HONESTY_LINE, /Region watch/);
  assert.match(HONESTY_LINE, /clear pass/i);
});
