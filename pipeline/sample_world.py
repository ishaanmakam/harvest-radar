"""Random land sample for the world watch.

Draws about 18 points across six continents, drops ocean and the US Midwest,
then runs pipeline/land_watch.py (Sentinel-2, WorldCover, Open-Meteo, FIRMS)
on that sample. pipeline/global_watch.py and data/global/regions.json are left
untouched. Output stays under data/cache/global/random/.
"""
import argparse
import json
import math
import os
import random
import sys
from pathlib import Path

import requests
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

PIPELINE = Path(__file__).resolve().parent
ROOT = PIPELINE.parent
COUNTRIES_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
    "ne_110m_admin_0_countries.geojson"
)
COUNTRIES_PATH = ROOT / "data" / "cache" / "global" / "ne_110m_admin_0_countries.geojson"
CONTINENTS = ("North America", "South America", "Europe", "Africa", "Asia", "Oceania")
CODES = {
    "North America": "na",
    "South America": "sa",
    "Europe": "eu",
    "Africa": "af",
    "Asia": "as",
    "Oceania": "oc",
}
# Equal-area draws stay inside this band so Antarctica and the high Arctic are not the sample.
LAT_SOUTH = -55.0
LAT_NORTH = 70.0
# Iowa and the rest of the US Midwest. One box, so the sample cannot pile up there.
MIDWEST = (37.0, 49.0, -104.0, -82.0)  # south, north, west, east
US_NAMES = {"United States of America", "United States"}
HOME_KM = 2000.0
CATALOGUE_KM = 250.0


def km_between(lat1, lon1, lat2, lon2):
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def random_lat(rng):
    lo = math.sin(math.radians(LAT_SOUTH))
    hi = math.sin(math.radians(LAT_NORTH))
    return math.degrees(math.asin(lo + rng.random() * (hi - lo)))


def in_midwest(lat, lon):
    south, north, west, east = MIDWEST
    return south <= lat <= north and west <= lon <= east


def coarse_continent(lat, lon):
    """Used only for pieces of a country that sit far from its main polygon."""
    if lat < -60:
        return None
    if -56 <= lat <= 13 and -82 <= lon <= -34 and not (lat > 8 and lon < -77):
        return "South America"
    if 15 <= lat <= 30 and -162 <= lon <= -150:
        return "Oceania"
    if lat > 5 and lon < -50:
        return "North America"
    if lat > 55 and -75 <= lon <= -10:
        return "North America"
    if -50 <= lat <= 25 and (lon >= 112 or lon <= -130):
        if 0 < lon < 141 and lat > -10:
            return "Asia"
        return "Oceania"
    if -36 <= lat < 37.5 and -20 <= lon <= 51:
        return "Africa"
    if lat >= 35 and -25 <= lon < 60:
        return "Europe"
    return "Asia"


def assign_continent(country, lon, lat, home_lat, home_lon):
    continent = country
    if country == "Europe" and lon >= 60:
        # Natural Earth tags all of Russia as Europe. East of the Urals is Asia.
        return "Asia"
    if country == "Europe" and lon <= -160:
        return "Asia"
    if home_lat is None:
        return continent if continent in CODES else None
    if km_between(lat, lon, home_lat, home_lon) < HOME_KM:
        return continent if continent in CODES else None
    moved = coarse_continent(lat, lon)
    return moved if moved in CODES else None


def iso_of(props):
    for key in ("ISO_A3", "ADM0_A3", "SOV_A3"):
        code = str(props.get(key) or "")
        if len(code) == 3 and code.isalpha():
            return code.lower()
    return "xxx"


def load_countries():
    if not COUNTRIES_PATH.exists() or COUNTRIES_PATH.stat().st_size < 1000:
        COUNTRIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(COUNTRIES_URL, timeout=60)
        response.raise_for_status()
        COUNTRIES_PATH.write_bytes(response.content)
    document = json.loads(COUNTRIES_PATH.read_text())
    geoms = []
    meta = []
    for feature in document["features"]:
        props = feature["properties"]
        continent = props.get("CONTINENT")
        if continent not in CODES:
            continue
        geometry = shape(feature["geometry"])
        if geometry.is_empty:
            continue
        parts = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
        parts = [part for part in parts if not part.is_empty]
        if not parts:
            continue
        home = max(parts, key=lambda part: part.area).representative_point()
        record = {
            "country": props.get("ADMIN") or props.get("NAME") or "unknown",
            "iso": iso_of(props),
            "continent": continent,
            "home_lat": home.y,
            "home_lon": home.x,
        }
        for part in parts:
            geoms.append(part)
            meta.append(record)
    tree = STRtree(geoms)
    return tree, geoms, meta


def locate(tree, geoms, meta, lat, lon):
    point = Point(lon, lat)
    # STRtree's predicate filter drops covers() hits on these polygons, so test the candidates.
    hits = [int(index) for index in tree.query(point) if geoms[int(index)].covers(point)]
    if not hits:
        return None
    record = meta[hits[0]]
    continent = assign_continent(record["continent"], lon, lat, record["home_lat"], record["home_lon"])
    if continent is None:
        return None
    return {
        "country": record["country"],
        "iso": record["iso"],
        "continent": continent,
        "lat": round(lat, 4),
        "lon": round(lon, 4),
    }


def catalogue_points():
    path = PIPELINE / "global_regions.json"
    if not path.exists():
        return []
    rows = json.loads(path.read_text())
    return [(row["lat"], row["lon"]) for row in rows]


def too_close(lat, lon, places, minimum_km):
    for place in places:
        if km_between(lat, lon, place["lat"], place["lon"]) < minimum_km:
            return True
    return False


def sample_places(rng, tree, geoms, meta, per_continent, minimum_km):
    target = {name: per_continent for name in CONTINENTS}
    counts = {name: 0 for name in CONTINENTS}
    places = []
    seen_countries = set()
    seen_us = False
    blocked = catalogue_points()
    for _ in range(30000):
        if all(counts[name] >= target[name] for name in CONTINENTS):
            break
        lat = random_lat(rng)
        lon = rng.uniform(-180.0, 180.0)
        found = locate(tree, geoms, meta, lat, lon)
        if found is None:
            continue
        continent = found["continent"]
        if counts[continent] >= target[continent]:
            continue
        if in_midwest(found["lat"], found["lon"]):
            continue
        if found["country"] in US_NAMES:
            if seen_us:
                continue
        elif found["country"] in seen_countries:
            continue
        if too_close(found["lat"], found["lon"], places, minimum_km):
            continue
        if any(km_between(found["lat"], found["lon"], old_lat, old_lon) < CATALOGUE_KM for old_lat, old_lon in blocked):
            continue
        found["id"] = f"{CODES[continent]}-{found['iso']}"
        found["name"] = f"{found['country']}, {continent}"
        places.append(found)
        counts[continent] += 1
        seen_countries.add(found["country"])
        if found["country"] in US_NAMES:
            seen_us = True
    order = {name: index for index, name in enumerate(CONTINENTS)}
    places.sort(key=lambda place: (order[place["continent"]], place["country"]))
    return places


def assert_sample(places, per_continent, minimum_km):
    counts = {}
    for place in places:
        counts[place["continent"]] = counts.get(place["continent"], 0) + 1
        if in_midwest(place["lat"], place["lon"]):
            raise SystemExit(f"sample landed in the US Midwest: {place}")
    missing = [name for name in CONTINENTS if counts.get(name, 0) < per_continent]
    if missing:
        raise SystemExit(f"could not fill {missing}; got {counts}")
    us = [place for place in places if place["country"] in US_NAMES]
    if len(us) > 1:
        raise SystemExit(f"more than one US point: {us}")
    for index, left in enumerate(places):
        for right in places[index + 1:]:
            distance = km_between(left["lat"], left["lon"], right["lat"], right["lon"])
            if distance < minimum_km:
                raise SystemExit(f"{left['id']} and {right['id']} are only {distance:.0f} km apart")


def self_check(tree, geoms, meta):
    cases = [
        (48.86, 2.35, "France", "Europe"),
        (42.03, -93.47, "United States of America", "North America"),
        (19.43, -99.13, "Mexico", "North America"),
        (-23.55, -46.63, "Brazil", "South America"),
        (3.9, -53.0, "France", "South America"),
        (6.45, 3.40, "Nigeria", "Africa"),
        (-33.92, 18.42, "South Africa", "Africa"),
        (36.8, 10.18, "Tunisia", "Africa"),
        (28.61, 77.21, "India", "Asia"),
        (55.0, 83.0, "Russia", "Asia"),
        (55.75, 37.62, "Russia", "Europe"),
        (35.68, 139.69, "Japan", "Asia"),
        (-33.87, 151.21, "Australia", "Oceania"),
        (-36.85, 174.76, "New Zealand", "Oceania"),
        (21.31, -157.86, "United States of America", "Oceania"),
        (41.90, 12.50, "Italy", "Europe"),
        (40.42, -3.70, "Spain", "Europe"),
        (-12.05, -77.04, "Peru", "South America"),
        (30.04, 31.24, "Egypt", "Africa"),
        (64.84, -147.72, "United States of America", "North America"),
    ]
    oceans = [(0.0, -30.0), (0.0, -160.0), (35.0, -40.0)]
    for lat, lon, country, continent in cases:
        found = locate(tree, geoms, meta, lat, lon)
        if found is None or found["country"] != country or found["continent"] != continent:
            raise SystemExit(f"land check failed for {lat},{lon}: expected {country}/{continent}, got {found}")
    for lat, lon in oceans:
        if locate(tree, geoms, meta, lat, lon) is not None:
            raise SystemExit(f"ocean point was accepted: {lat},{lon}")


def watch_region(region):
    """One global_watch region dict. Crops are unknown at a random coordinate."""
    return {
        "id": region["id"],
        "name": region["name"],
        "country": region["country"],
        "lat": region["lat"],
        "lon": region["lon"],
        "crops": ["not labelled"],
        "continent": region["continent"],
    }


def load_watch():
    sys.path.insert(0, str(PIPELINE))
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
    os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
    try:
        import land_watch
    except ImportError as exc:
        raise SystemExit("pipeline/land_watch.py is required; this script samples and calls that watch") from exc
    return land_watch, land_watch.OUT_DIR


def document_of(watch, entries, seed):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    note = (
        " This file is a random land sample across six continents, not the fixed region catalogue. "
        f"Seed {seed}. Ocean is rejected with Natural Earth 110m country polygons. "
        "The US Midwest, including Iowa, is left out. Crops are not labelled."
    )
    # continent is not in the watch schema, so it stays in the place name only.
    public = []
    for entry in entries:
        row = dict(entry)
        row.pop("continent", None)
        public.append(row)
    return {
        "as_of": now.date().isoformat(),
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": watch.METHOD + note,
        "sources": list(watch.SOURCES_TEXT) + ["Natural Earth 110m admin-0 countries (land check)"],
        "regions": public,
    }


def main():
    parser = argparse.ArgumentParser(description="Sample random land and run the world watch")
    parser.add_argument("--per-continent", type=int, default=3, help="points on each of the six continents (2-4)")
    parser.add_argument("--seed", type=int, default=20261018)
    parser.add_argument("--min-km", type=float, default=900.0, help="minimum distance between sample points")
    parser.add_argument("--places-only", action="store_true", help="print the sample and do not call the watch")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.per_continent < 2 or args.per_continent > 4:
        raise SystemExit("--per-continent must be 2, 3, or 4 so the sample stays between 12 and 24")
    tree, geoms, meta = load_countries()
    self_check(tree, geoms, meta)
    minimum_km = args.min_km
    places = []
    for minimum_km in (args.min_km, 600.0, 350.0):
        places = sample_places(random.Random(args.seed), tree, geoms, meta, args.per_continent, minimum_km)
        if len(places) == args.per_continent * len(CONTINENTS):
            break
    assert_sample(places, args.per_continent, minimum_km)
    print(f"sample seed={args.seed} points={len(places)} min_km={minimum_km:.0f}")
    for place in places:
        print(f"{place['id']:12} {place['continent']:16} {place['country']:28} {place['lat']:8.3f} {place['lon']:8.3f}")
    if args.places_only:
        return
    watch, out = load_watch()
    manifest = out / "places.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"seed": args.seed, "min_km": minimum_km, "places": places}, indent=2) + "\n")
    entries = []
    for place in places:
        entry = watch.run_region(watch_region(place), args.force, False)
        entry.pop("continent", None)
        entries.append(entry)
        headline = entry.get("headline") or entry.get("status")
        watch.say(f"RESULT {place['id']} {place['country']} {place['continent']} {place['lat']} {place['lon']} :: {headline}")
        doc = document_of(watch, entries, args.seed)
        try:
            watch.validate(doc)
        except ValueError as exc:
            watch.say(f"schema check failed after {place['id']}: {exc}")
            raise
        watch.write_atomic(out / "regions.json", doc)
    watch.build_preview(entries)
    watch.say(f"wrote {out / 'regions.json'} ({len(entries)} places)")


if __name__ == "__main__":
    main()
