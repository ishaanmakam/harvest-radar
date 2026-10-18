#!/usr/bin/env python3
"""Bake a snapshot of CelesTrak orbital elements for the globe intro.

    python scripts/fetch_tles.py            # writes web/public/sats/imaging.json and starlink.json
    python scripts/fetch_tles.py --max-starlink 800

Standard library only. Two files come out, both read by web/src/globe/satellites.js at run time:

- imaging.json: Sentinel-2A/2B/2C, Landsat 8/9, Terra and Aqua (the satellites the project's
  imagery and the HLS product come from), every object kept.
- starlink.json: the Starlink constellation thinned by even sampling to at most --max-starlink
  objects (default 800) so the file stays well under 400 KB. Starlink is drawn for connectivity
  context only; it is not a data source.

Each record is the CelesTrak OMM (general perturbations) JSON trimmed to the fields satellite.js
needs for SGP4 plus the object name and id. SGP4 drifts by kilometres a day, so refresh the
snapshot now and then; the files carry `fetched_at` and the page shows it.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "web" / "public" / "sats"
BASE = "https://celestrak.org/NORAD/elements/gp.php"
USER_AGENT = "overfield-fetch-tles/1.0 (+https://github.com/ishaanmakam/harvest-radar)"

# CelesTrak NAME queries are substring matches, so LANDSAT also returns Landsat 7 and old debris
# entries; only the objects below are kept, in this order.
IMAGING = [
    ("SENTINEL-2", ["SENTINEL-2A", "SENTINEL-2B", "SENTINEL-2C"]),
    ("LANDSAT", ["LANDSAT 8", "LANDSAT 9"]),
    ("TERRA", ["TERRA"]),
    ("AQUA", ["AQUA"]),
]

# The fields satellite.js json2satrec reads, plus the name and international designator.
FIELDS = [
    "OBJECT_NAME",
    "OBJECT_ID",
    "NORAD_CAT_ID",
    "EPOCH",
    "MEAN_MOTION",
    "ECCENTRICITY",
    "INCLINATION",
    "RA_OF_ASC_NODE",
    "ARG_OF_PERICENTER",
    "MEAN_ANOMALY",
    "BSTAR",
    "MEAN_MOTION_DOT",
    "MEAN_MOTION_DDOT",
]


def fetch(query: str) -> list:
    url = f"{BASE}?{query}&FORMAT=json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as res:
        body = res.read().decode("utf-8")
    if not body.strip().startswith("["):
        # CelesTrak answers "No GP data found" as plain text, and rate limits with an HTML page.
        raise RuntimeError(f"{url}: not a JSON list: {body[:120]!r}")
    data = json.loads(body)
    print(f"fetched {len(data):5d} objects  {url}")
    return data


def trim(obj: dict) -> dict:
    missing = [key for key in FIELDS if key not in obj]
    if missing:
        raise RuntimeError(f"{obj.get('OBJECT_NAME', '?')}: missing {missing}")
    return {key: obj[key] for key in FIELDS}


def even_sample(items: list, limit: int) -> list:
    if len(items) <= limit:
        return items
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"))
    path.write_text(text + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}  {len(payload['objects'])} objects  {len(text) / 1024:.0f} KB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-starlink", type=int, default=800, help="keep at most this many Starlink objects (default 800)")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help=f"output folder (default {OUT_DIR.relative_to(ROOT)})")
    args = parser.parse_args()

    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        imaging = []
        for query, keep in IMAGING:
            by_name = {obj.get("OBJECT_NAME"): obj for obj in fetch(f"NAME={query}")}
            for name in keep:
                if name not in by_name:
                    print(f"warning: {name} not in the {query} result; skipped", file=sys.stderr)
                    continue
                imaging.append(trim(by_name[name]))
        starlink_all = fetch("GROUP=starlink")
    except (urllib.error.URLError, RuntimeError, json.JSONDecodeError) as err:
        print(f"fetch_tles: {err}", file=sys.stderr)
        return 1

    # Even sampling across the catalogue order keeps every shell and plane represented.
    starlink_all.sort(key=lambda obj: int(obj.get("NORAD_CAT_ID", 0)))
    starlink = [trim(obj) for obj in even_sample(starlink_all, args.max_starlink)]

    source = f"{BASE} (CelesTrak GP data, OMM JSON)"
    write(args.out / "imaging.json", {
        "fetched_at": fetched_at,
        "source": source,
        "queries": [f"NAME={query}" for query, _ in IMAGING],
        "objects": imaging,
    })
    write(args.out / "starlink.json", {
        "fetched_at": fetched_at,
        "source": source,
        "queries": ["GROUP=starlink"],
        "catalogue_count": len(starlink_all),
        "objects": starlink,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
