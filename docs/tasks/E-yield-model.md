# E — Crop yield: from a relative index to a trained, calibrated estimate (all three of us)

Paste into your agent: "Read AGENTS.md and docs/tasks/E-yield-model.md. Do the tasks marked with your name, in order,
committing after each; post DONE / FILES / CONTRACT IMPACT / NEEDS in #bot-channel."

Deadline is Sun Oct 18, 10:30 AM PT. Freeze at 9:30 AM: whatever is merged by then is the demo.

## Why this, and how it differs from the work we read

Two Omdena case studies (Senegal yield forecasting; FarmHand field boundaries and crop types in India) do a similar job
at a different scale and for a different reader: a CNN-LSTM on MODIS histograms with transfer learning from South Sudan
and Ethiopia, and a SARIMA forecast of regional NDVI feeding a yield regression, both trained against surveyed yields
and reported per department for planners. Their boundary work (ResUNet-a, FracTAL ResUNet, watershed to field instances)
is the problem Niranjan solved with segmentation.

YieldSAT (CVPR 2026, DFKI and RPTU) is a benchmark dataset, not a model: combine-harvester yield maps at 10 m aligned to
Sentinel-2 time series for 2,173 fields (corn, soybean, wheat, rapeseed; Argentina, Brazil, Uruguay, Germany; 2016-2024),
each field paired with weather (ERA5-Land), soil (SoilGrids) and terrain (SRTM), in a 24-step model-ready NetCDF per
country, with benchmark code (LSTM, Transformer, 3D-ConvLSTM, attention fusion). Access is by request form
(https://forms.office.com/e/VzgL85A3x8); the model-ready files are gigabytes per country. Their headline finding is the one
that matters for us: a model trained on one region or year loses about 20 points of R2 on a new region or year, so a
yield model needs local anchoring, uncertainty, and honest words.

What Overfield already does differently:

- **The decision is the product, not the forecast.** Every farm gets a plan every morning with eight checks and the
  officer gets a route; yield is one input to "harvest now or wait" economics, not the headline.
- **Field scale, every clear pass.** Sentinel-2 at 10 m per field, not MODIS at 500 m per department.
- **No training labels for the season model.** The harvest model learns the regional peak-to-harvest lag from the
  landscape itself (with censoring), anomalies are read against the crop-and-zone cohort, and we validate against public
  USDA Crop Progress rather than a survey we would have to run.
- **Several sources, compared, not one feed.** Eight numerical weather models folded into an ensemble that the plans
  quote; a curve-fit harvest date as a second opinion on the lag model; USDA district progress next to our own share;
  Perceptron reading the same frames as a narrator with the index rules as the fallback.
- **Delivery.** A voice call and a text in the farmer's language, sign-up with an address, a per-person morning time.

What they have that we do not yet: a yield model trained on measured yields with a reported error. Today `yield_model.py`
is a relative NDVI-integral index scaled to a county reference, and it says so. After this brief it is a calibrated
estimate with an interval and a hindcast error, and it still says what it is.

One more honest note on "multiple image sources": today the season sensor is Sentinel-2 only (NAIP is used for outlines
and visuals). Task N2 adds a second satellite (Landsat 8/9, or NASA HLS which harmonises both) so the claim is true and
the two series are compared per field.

## How YieldSAT fits (what we take now, what comes after approval)

Now, without the data:
- **Its input recipe.** Sentinel-2 bands plus daily weather, SoilGrids soil properties and SRTM terrain per field. We
  already have the first two; task I3 adds soil and elevation per field from free APIs (SoilGrids REST, Open-Meteo
  elevation), so our feature table has the same columns a YieldSAT-trained model expects.
- **Its evaluation.** Leave-one-year-out and leave-one-region-out, reported as R2 and RMSE, never only in-sample.
- **Its data layout.** Task N3 exports our fields as 24-step per-field tensors (all Sentinel-2 bands, cloud-masked by
  SCL) in the same arrangement as the preprocessed release, so a model trained on YieldSAT can be run on Story County
  without a new pipeline.
After approval (Ishaan submits the form today; approval is manual and will not arrive before the deadline):
- Train the field-level model on YieldSAT corn and soybean fields (Argentina, Brazil, Uruguay), then anchor it to Iowa
  with the NASS county yields from L1, which is exactly the transfer-and-fine-tune pattern both case studies used. Keep
  the L1 county model as the second opinion and report the two side by side.

## Design: three layers, same data we already pull

```
L1 county model (Ishaan)        USDA NASS county yields 2000-2025 (corn, soybean, all 99 Iowa counties)
                                + NASA POWER weather per county centroid (the source weather.json already uses)
                                -> ridge regression, leave-one-year-out, numpy only -> 2026 Story County bu/ac + interval
L2 satellite history (Niranjan) Sentinel-2 county NDVI features 2018-2025 (monthly medians, peak, integral, cropland-masked)
                                -> extra features for L1 on the years that have them; Landsat/HLS second source for 2026
L3 field downscale + words + UI county estimate x field index (already in yield_model) -> calibrated bu/ac per field with
   (Ishaan, Srikar)             an interval and a confidence; soil and terrain per field; Outlook line; Yield view on the map
```

Training set for L1: about 99 counties x 26 years per crop (roughly 2,500 county-seasons) from two free sources.
Features per county-season: growing degree days May to September, July and August heat-stress days (max temperature at or
above 32 C and 35 C), rainfall by month, longest dry spell June to August, first fall frost date, a year term for the
technology trend, and (where L2 exists) NDVI peak, plateau length and integral. Model: ridge regression on standardised
features with leave-one-year-out cross-validation; report RMSE and bias in bu/ac per held-out year, with 2012 (drought)
and 2019 (late planting) called out; a gradient-boosting variant only if scikit-learn installs cleanly on every machine.
The 2026 estimate uses the observed season to date plus the forecast and then climatology for the remaining days, with
the interval widened by the hindcast RMSE.

Validation and honesty rules:
- Hold out 2025 entirely and report its error first; it is the last year NASS has published.
- Compare the 2026 Story County estimate with the October USDA Crop Production state forecast for Iowa; name the gap.
- Every number carries "estimate" and an interval; the field-level figure is the county estimate distributed by the
  relative index, and the words say so. No fabricated calibration.

## Tasks

| # | Who | Task | Done when |
|---|---|---|---|
| I1 | Ishaan | `intelligence/yield_train.py`: fetch NASS county yields (QuickStats, `NASS_API_KEY`) and NASA POWER daily weather per county centroid, cache under `data/cache/yield/`, build the feature table, train ridge with leave-one-year-out, write `data/out/yield_model.json` (coefficients, hindcast table, 2026 estimate with interval) | hindcast RMSE for corn and soy printed; 2025 hold-out error reported; file validates against `contracts/yield_model.schema.json` |
| I2 | Ishaan | Calibrate `yield_model.py`: per field `bu_per_acre_calibrated` and `interval_bu_per_acre` = county estimate x index, confidence from both; Outlook line and `get_outlook` say "calibrated to the county model (hindcast error +/- N bu/ac)" | `analytics.json` carries the calibrated block; tests green |
| I3 | Ishaan | Soil and terrain per field (the YieldSAT auxiliary inputs): SoilGrids REST (organic carbon, clay, sand, pH at 0-30 cm) and Open-Meteo elevation per field centroid, cached, as features in `yield_model.py` and a line in the Outlook ("low organic carbon for the zone") | `data/out/field_env.json` written and validated; features used by I2 |
| N1 | Niranjan | `pipeline/s08_ndvi_history.py`: Sentinel-2 county-mean NDVI features for Story County and its neighbours, 2018-2025, cropland-masked, monthly medians May-Sep plus peak and integral -> `data/out/ndvi_history.json` | file validates; Ishaan joins it to L1 as features |
| N2 | Niranjan | Second imagery source for 2026 fields: Landsat 8/9 (STAC `landsat-c2-l2`) or NASA HLS per-field NDVI -> `data/out/timeseries_landsat.json` (same shape as timeseries.json) plus per-field `agreement` (correlation and bias vs Sentinel-2) | file validates; the map and the analytics can show both series and the agreement |
| N3 | Niranjan | Export per-field 24-step Sentinel-2 tensors (all bands, SCL cloud mask) in the YieldSAT preprocessed layout -> `data/cache/yieldsat_export/` (gitignored) with a small manifest in `data/out/yieldsat_manifest.json` | a YieldSAT-trained model can be run on our fields without new code |
| S1 | Srikar | Perceptron visual yield cues per zone (lodging, stand gaps, weed pressure, drowned spots) as `insights.json` zone notes, cues off by default | real `insights.json` in `data/out`; `data/out` validates fully |
| S2 | Srikar | Web: Yield view (county estimate with interval and hindcast chart, field bands on the map, soil and terrain in the farm panel, the two satellite series side by side where N2 exists) | view works on mock and real; build passes |
| All | Ishaan | Submit the YieldSAT data request form; post in #bot-channel when approved | approval email received |

Order: I1, I3, N1, N2 and N3 can start at once (I1 does not wait for N1); I2 after I1 and I3; S2 after I2 lands in
`analytics.json` (the shape is in the contract below, so S2 can start on the mock).

## Contracts (CONTRACT IMPACT, all additive; agree in #bot-channel before merging)

- New optional `contracts/yield_model.schema.json` for `data/out/yield_model.json`: `{as_of, crops: {corn|soybean: {county,
  estimate_bu_per_acre, interval_bu_per_acre: [lo, hi], hindcast: [{year, actual, predicted}], rmse_bu_per_acre, features: [...],
  coefficients: {...}, n_train}}, sources: [...]}`.
- `analytics.json` `yield` module gains per field `bu_per_acre_calibrated`, `interval_bu_per_acre`, `calibration` ("county model"
  or "reference only"), `soil` and `terrain` blocks, and per crop in `landscape` the county estimate, interval and hindcast RMSE.
- New optional `data/out/field_env.json` (I3), `data/out/ndvi_history.json` (N1), `data/out/timeseries_landsat.json` (N2, same
  schema as timeseries.json), `data/out/yieldsat_manifest.json` (N3).
- Nothing in plans, route, deliveries, fields, zones, weather or fires changes.

## Data sources (all free)

- USDA NASS QuickStats: county yields (corn grain, soybeans), Iowa, 2000-2025. Needs a key: https://quickstats.nass.usda.gov/api
  (instant by email); put it in `.env` as `NASS_API_KEY` (already in `.env.example`).
- NASA POWER daily: tmax, tmin, precipitation per county centroid, 2000-2026 (no key; one request per county).
- SoilGrids 2.0 REST (ISRIC, no key) and Open-Meteo elevation (no key) per field centroid.
- Sentinel-2 L2A via Earth Search (N1, N2, N3 as today); Landsat Collection 2 L2 via Earth Search or Planetary Computer, or
  HLS via NASA LP DAAC (Earthdata login) for N2.
- YieldSAT by request (form above); its preprocessed layout is documented in its data notebook.
- County centroids and FIPS: a small static table checked into `config/iowa_counties.json` (I1 writes it; config addition).

## NEEDS

- `NASS_API_KEY` in `.env` on the machine running I1 (Ishaan requests the key; it arrives by email within a minute).
- Niranjan: which second sensor is quickest from the existing STAC code (Landsat C2 L2 on Earth Search is the same client).
- Ishaan: the YieldSAT form (name, affiliation, intended use); approval is manual.
