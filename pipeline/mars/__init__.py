"""Overfield beyond Earth: the Mars landing-site pipeline (m01 fetch, m02 score, m03 assets, m04 worlds).

Every number in data/out/mars and data/out/worlds.json comes from a cited public dataset or from this code applied to
one. Nothing here simulates, shifts or borrows data when a source fails: the layer is missing and the scripts say so.
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "cache" / "mars"
OUT = ROOT / "data" / "out" / "mars"
CONTRACTS = ROOT / "contracts"
USER_AGENT = "Overfield-mars/1 (DubHacks 2026; public data only)"
MARS_RADIUS_KM = 3389.5
MARS_SPHERE_CRS = "+proj=longlat +a=3396190 +b=3396190 +no_defs"


def config():
    return json.loads((HERE / "config.json").read_text(encoding="utf-8"))


def to_east180(lon):
    """Any east longitude (0..360 or -180..180) to -180..180."""
    lon = float(lon) % 360.0
    return lon - 360.0 if lon > 180.0 else lon


def great_circle_km(lat1, lon1, lat2, lon2, radius_km=MARS_RADIUS_KM):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius_km * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def compass(bearing):
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return names[int((bearing + 22.5) // 45) % 8]


def write_json_atomic(path, doc, schema_path=None):
    """Validate against a JSON Schema (when given) and write through a temp file so a crash never leaves half a file."""
    path = Path(path)
    if schema_path:
        import jsonschema
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
