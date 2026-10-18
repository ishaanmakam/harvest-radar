# Overfield beyond Earth: the Mars pipeline

Point the same method at Mars: read orbital data, run a fixed set of checks on every place, explain the pick in plain
words. On Mars the question is where Starship should land. Every number in `data/out/mars` and `data/out/worlds.json`
comes from a cited public dataset or from this code applied to one. Nothing is simulated, shifted, borrowed or filled in
when a source fails: the layer is missing and the script says so.

Independent analysis from public NASA, USGS and PSI data. Not affiliated with or endorsed by SpaceX.

## Run it

```bash
python pipeline/mars/run_all.py
```

The first run downloads about 150 MB into `data/cache/mars/` (gitignored) and takes a few minutes; a second run reads
the caches, downloads nothing and finishes in about two minutes (the scoring and the image warps are recomputed). The
scripts run one by one too: `m01_fetch.py`, `m04_worlds.py`, `m02_score.py`, `m03_assets.py`. Dependencies are the
ones in `pipeline/requirements.txt` plus the standard library.

| script | does | writes |
|---|---|---|
| `m01_fetch.py` | downloads and caches every layer, writes MOLA and thermal inertia as simple-cylindrical GeoTIFFs on a Mars sphere, inventories every raster, prints PASS or CHECK for the layer checks (Olympus Mons, Hellas, Arcadia ice, Tharsis dust, Syrtis Major, the six candidate elevations) | `data/cache/mars/*` |
| `m04_worlds.py` | the worlds list: Earth, Mars, three ocean moons, fifteen exoplanets from the NASA Exoplanet Archive | `data/out/worlds.json` |
| `m02_score.py` | the 0.125 degree grid between 60 S and 60 N, the four checks, the worst-check status, the weighted score, ten sites at least 300 km apart, the six SpaceX candidates graded, the trade-off table | `data/out/mars/sites.json`, `data/cache/mars/grid.npz` |
| `m03_assets.py` | the painted globe textures, the Web Mercator base and score maps, THEMIS thumbnails (MOLA hillshade fallback), the slide image | `data/out/mars/*.jpg`, `*.png`, `thumbs/*.jpg` |

All cutoffs, weights, radii and grid settings live in `config.json`; the UI shows them through `sites.json`. Both
output files are validated against `contracts/mars_sites.schema.json` and `contracts/worlds.schema.json` before they
are written.

## The checks

| check | from | dataset | go | watch | no-go |
|---|---|---|---|---|---|
| Ice within reach | SpaceX | SWIM 2025 ice consistency, 0-1 m and 1-5 m | 0-1 m >= 0.25 or 1-5 m >= 0.5 | 0-1 m >= 0 or 1-5 m >= 0.25 | otherwise |
| Low ground | SpaceX | MOLA MEGDR 16 ppd | <= -2,500 m | <= 0 m | above 0 m |
| Plume-safe ground | ours | MGS TES nighttime thermal inertia | 180 to 500 | 120 to 180 or 500 to 700 | < 120 (dust) or > 700 (rock) |
| Science reach | ours | USGS SIM 3292 geologic units within about 100 km | >= 4 units with a Noachian or Hesperian one | >= 2 units | 1 unit |

Weights: ice 0.35, low ground 0.20, plume-safe ground 0.25, science reach 0.20. Shown on every card but not scored:
regional slope, latitude, albedo (sunlight and flat rock-free ground are SpaceX's own screens; HiRISE is the tool for
landing-scale rocks). A magnetic shelter check is defined in `config.json` and absent in this run: no ready public grid
of the crustal field was fetched.

## Data and terms

| dataset | where | terms |
|---|---|---|
| MOLA MEGDR 16 ppd | https://pds-geosciences.wustl.edu/mgs/urn-nasa-pds-mgs_mola_topography_derived/meg016/ | NASA, public domain |
| SWIM 2025 ice consistency GeoTIFFs | https://swim.psi.edu/SWIM4MIMProducts.php (the page's own download form; one layer per request, the server cuts multi-layer zips short) | public SWIM products, cite the SWIM team (PSI) |
| MGS TES thermal inertia 20 ppd and mask | https://se.psi.edu/inertia/ | public map from Putzig and Mellon, cite Icarus 173 (2005) |
| MGS TES albedo mosaic | https://planetarymaps.usgs.gov/mosaic/Mars_MGS_TES_Albedo_mosaic_global_7410m.tif | USGS, public domain |
| USGS SIM 3292 geologic units | https://astrogeology.usgs.gov/pygeoapi/collections/mars/sim3292_global_geologic_map/units | USGS, public domain |
| THEMIS controlled mosaics | https://stac.astrogeology.usgs.gov/api, collection mo_themis_controlled_mosaics | NASA/USGS, public domain |
| HiRISE candidate pages | https://hirise.lpl.arizona.edu/ESP_060706_2195 and five more | public pages; NASA imagery public domain |
| NASA Exoplanet Archive pscomppars | https://exoplanetarchive.ipac.caltech.edu/TAP | public; acknowledge the archive |
| Space.com, 2017 (the SpaceX criteria) | https://www.space.com/36142-spacex-studying-mars-landing-sites.html | cited for the criteria only |

Conventions: longitudes east-positive in -180..180 (sources in 0..360 are converted; the thermal inertia map is stored
0..360 West and is flipped, with the row order chosen by the Syrtis Major against Tharsis check and printed); latitudes
planetocentric; Mars mean radius 3,389.5 km for distances.

## Caveats the scripts print

- About 15 percent of the thermal inertia map between 60 S and 60 N is the authors' interpolated fill (TES mask); those
  cells carry a note in the site detail text.
- The Hellas floor check point from the brief (42.4 S, 70.5 E) reads -6,072 m in the 16 ppd MOLA, above the -6,500 m
  the brief expected; the deepest part of the basin lies elsewhere. It is printed as CHECK, not failed.
- 7 km cells are far coarser than a landing ellipse. This is a screen that says where to point HiRISE next, not a
  mission-ready site selection.
