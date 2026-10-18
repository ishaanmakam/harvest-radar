"""Shared loaders for the field tests. No API calls."""
import json
import math
import subprocess
import sys
from pathlib import Path

from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[2]
TRUTH_PATH = ROOT / "vision" / "eval" / "truth.json"
TRUTH_COMMIT = "ccf4be1"
DEV_ZONES = ("Z01", "Z07")
HELD_ZONES = ("Z02", "Z03", "Z04", "Z05", "Z06", "Z08", "Z09")
SPEND_CAP = 3.00
M_PER_DEG = 111320.0


def load_json(path):
    return json.loads(Path(path).read_text())


def fields_by_id(path):
    doc = load_json(path)
    return {f["properties"]["field_id"]: f for f in doc["features"]}


def check_truth_geometry(fields_path):
    """Stop if a labelled field disappeared or its polygon changed. Extra fields are returned."""
    truth = load_json(TRUTH_PATH)["fields"]
    if len(truth) != 129:
        sys.exit(f"STOP truth.json has {len(truth)} fields, expected 129")
    current = fields_by_id(fields_path)
    missing = [fid for fid in truth if fid not in current]
    if missing:
        sys.exit("STOP field ids missing from fields.geojson: " + ", ".join(missing))
    raw = subprocess.check_output(["git", "show", f"{TRUTH_COMMIT}:data/out/fields.geojson"], cwd=ROOT)
    labelled = {f["properties"]["field_id"]: f["geometry"] for f in json.loads(raw)["features"]}
    changed = [fid for fid in truth if fid not in labelled or labelled[fid] != current[fid]["geometry"]]
    if changed:
        sys.exit("STOP geometry changed for labelled fields: " + ", ".join(changed))
    extra = sorted(fid for fid in current if fid not in truth)
    return extra


def area_ha(geom):
    if geom.is_empty:
        return 0.0
    lat = geom.centroid.y
    metres2 = geom.area * (M_PER_DEG ** 2) * math.cos(math.radians(lat))
    return metres2 / 10000.0


def zone_of(lon, lat, zones):
    point = Point(lon, lat)
    nearest, best = None, None
    for zone in zones:
        geom = shape(zone["geometry"])
        if geom.covers(point):
            return zone["properties"]["zone_id"]
        dist = geom.distance(point)
        if best is None or dist < best:
            best, nearest = dist, zone["properties"]["zone_id"]
    return nearest


def frame_path(frames_doc, date, data_root):
    for frame in frames_doc["frames"]:
        if frame["date"] == date:
            return Path(data_root) / frame["rgb"]
    sys.exit(f"STOP no frame for {date}")
