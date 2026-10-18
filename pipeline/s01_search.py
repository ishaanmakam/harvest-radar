"""STAC search for Sentinel-2 L2A over the demo AOI. Metadata only; no imagery."""
import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timezone

from pystac import ItemCollection
from pystac_client import Client

from common import CACHE, load_region, log, write_json

STAC_URL = "https://earth-search.aws.element84.com/v1"
COLLECTIONS = ("sentinel-2-l2a", "sentinel-2-c1-l2a")
REQUIRED_ASSETS = ("red", "green", "blue", "nir", "swir22", "scl")
STEP = "s01"


def as_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_date(item):
    dt = as_utc(item.datetime)
    if dt is None:
        log(STEP, f"ERROR item {item.id} has no datetime")
        sys.exit(1)
    return dt.date()


def latest_datetime(items):
    stamps = [as_utc(item.datetime) for item in items if item.datetime is not None]
    return max(stamps) if stamps else None


def tile_id(item):
    props = item.properties
    if props.get("s2:mgrs_tile"):
        return props["s2:mgrs_tile"]
    if props.get("grid:code"):
        return props["grid:code"]
    return "unknown"


def cloud_cover(item):
    value = item.properties.get("eo:cloud_cover")
    if value is None:
        return None
    return float(value)


def scale_offset(asset):
    bands = asset.extra_fields.get("raster:bands")
    if not isinstance(bands, list) or not bands:
        return None
    band = bands[0]
    if "scale" not in band or "offset" not in band:
        return None
    return {"scale": band["scale"], "offset": band["offset"]}


def search_collection(client, collection, bbox, interval, max_cloud):
    search = client.search(
        collections=[collection],
        bbox=bbox,
        datetime=interval,
        query={"eo:cloud_cover": {"lt": max_cloud}},
    )
    return list(search.items())


def choose_collection(found):
    def rank(name):
        items = found[name]
        latest = latest_datetime(items)
        stamp = latest.timestamp() if latest is not None else float("-inf")
        return (stamp, len(items))

    chosen = max(COLLECTIONS, key=rank)
    other = next(name for name in COLLECTIONS if name != chosen)
    chosen_latest = latest_datetime(found[chosen])
    other_latest = latest_datetime(found[other])
    chosen_day = chosen_latest.date().isoformat() if chosen_latest else "none"
    other_day = other_latest.date().isoformat() if other_latest else "none"
    if chosen_latest != other_latest and (
        other_latest is None or (chosen_latest is not None and chosen_latest > other_latest)
    ):
        why = (
            f"latest item date {chosen_day} is more recent than {other} ({other_day})"
        )
    elif len(found[chosen]) > len(found[other]):
        why = (
            f"latest item date ties {other} at {chosen_day}; "
            f"{chosen} has more items ({len(found[chosen])} vs {len(found[other])})"
        )
    else:
        why = (
            f"latest item date and item count tie {other} at {chosen_day} "
            f"({len(found[chosen])} items); kept the first collection searched"
        )
    return chosen, why


def print_scale(label, item):
    example = {"id": item.id, "datetime": as_utc(item.datetime).isoformat()}
    for band in ("red", "swir22"):
        found = scale_offset(item.assets[band])
        example[band] = found
        if found is None:
            log(STEP, f"NO raster:bands scale/offset  {label} {item.id} {band}")
        else:
            log(
                STEP,
                f"{label} {item.id} {band} scale={found['scale']} offset={found['offset']}",
            )
    return example


def main():
    parser = argparse.ArgumentParser(description="Search Sentinel-2 L2A scenes for the demo season")
    parser.add_argument("--max-cloud", type=float, default=70)
    args = parser.parse_args()

    region = load_region()
    bbox = region["bbox"]
    season_start = region["season_start"]
    as_of = region["as_of"]
    as_of_d = date.fromisoformat(as_of)
    interval = f"{season_start}T00:00:00Z/{as_of}T23:59:59Z"
    log(STEP, f"STAC {STAC_URL} bbox={bbox} datetime={interval} eo:cloud_cover<{args.max_cloud}")

    client = Client.open(STAC_URL)
    found = {}
    for collection in COLLECTIONS:
        items = search_collection(client, collection, bbox, interval, args.max_cloud)
        found[collection] = items
        latest = latest_datetime(items)
        latest_s = latest.date().isoformat() if latest else "none"
        log(STEP, f"{collection}: {len(items)} items, latest item date {latest_s}")

    if all(len(items) == 0 for items in found.values()):
        log(STEP, "ERROR: both sentinel-2-l2a and sentinel-2-c1-l2a returned zero items")
        sys.exit(1)

    chosen, why = choose_collection(found)
    items = sorted(found[chosen], key=lambda item: (as_utc(item.datetime) or datetime.min.replace(tzinfo=timezone.utc), item.id))
    log(STEP, f"chose {chosen}: {why}")

    log(STEP, f"asset keys: {sorted(items[0].assets.keys())}")
    missing_any = False
    for item in items:
        missing = [key for key in REQUIRED_ASSETS if key not in item.assets]
        if missing:
            log(STEP, f"ERROR item {item.id} missing assets: {missing}")
            missing_any = True
        cover = cloud_cover(item)
        if cover is not None and cover >= args.max_cloud:
            log(STEP, f"ERROR item {item.id} eo:cloud_cover={cover} is not < {args.max_cloud}")
            missing_any = True
    if missing_any:
        sys.exit(1)

    scale_offset_example = {
        "first": print_scale("first", items[0]),
        "last": print_scale("last", items[-1]),
    }

    by_date = defaultdict(list)
    for item in items:
        by_date[utc_date(item)].append(item)

    dates = sorted(by_date)
    likely_clear = []
    log(STEP, "date        items  cloud_min  cloud_max  tiles")
    for day in dates:
        group = by_date[day]
        covers = [cloud_cover(item) for item in group]
        numeric = [value for value in covers if value is not None]
        cmin = min(numeric) if numeric else None
        cmax = max(numeric) if numeric else None
        tiles = ",".join(tile_id(item) for item in group)
        cmin_s = f"{cmin:.2f}" if cmin is not None else "missing"
        cmax_s = f"{cmax:.2f}" if cmax is not None else "missing"
        log(STEP, f"{day.isoformat()}  {len(group):<5}  {cmin_s:<9}  {cmax_s:<9}  {tiles}")
        if cmin is not None and cmin < 20:
            likely_clear.append(day.isoformat())

    oct1 = date(as_of_d.year, 10, 1)
    since_oct = [day for day in likely_clear if oct1 <= date.fromisoformat(day) <= as_of_d]
    log(
        STEP,
        f"likely-clear dates: {len(likely_clear)} overall, {len(since_oct)} from {oct1.isoformat()} to {as_of}. "
        "Tile-level estimate (min eo:cloud_cover across that date's tiles < 20). "
        "The real per-AOI count comes later from the SCL mask.",
    )
    latest_date = dates[-1].isoformat()
    days_before = (as_of_d - dates[-1]).days
    log(STEP, f"latest pass {latest_date}, {days_before} days before as_of {as_of}")

    CACHE.mkdir(parents=True, exist_ok=True)
    ItemCollection(items).save_object(str(CACHE / "stac_items.json"))
    summary = {
        "collection": chosen,
        "n_items": len(items),
        "dates": [day.isoformat() for day in dates],
        "likely_clear_dates": likely_clear,
        "latest_date": latest_date,
        "scale_offset_example": scale_offset_example,
    }
    write_json(CACHE / "s01_summary.json", summary, pretty=True)
    log(STEP, f"wrote {CACHE / 'stac_items.json'} and {CACHE / 's01_summary.json'}")


if __name__ == "__main__":
    main()
