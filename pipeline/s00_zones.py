"""Write the 3x3 zone grid. Same formula as the mock generator."""
import json

from jsonschema import Draft202012Validator

from common import CACHE, OUT, ROOT, load_region, log, write_json


def build_zones(region):
    W, S, E, N = region["bbox"]
    GX, GY = region["zones_grid"]
    features = []
    for j in range(GY):
        for i in range(GX):
            zid = f"Z{j * GX + i + 1:02d}"
            x0 = W + (E - W) * i / GX
            x1 = W + (E - W) * (i + 1) / GX
            y1 = N - (N - S) * j / GY
            y0 = N - (N - S) * (j + 1) / GY
            centroid = [round((x0 + x1) / 2, 5), round((y0 + y1) / 2, 5)]
            name = f"Zone {chr(65 + j)}{i + 1}"
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
                },
                "properties": {"zone_id": zid, "name": name, "centroid": centroid},
            })
    return {"type": "FeatureCollection", "features": features}


def main():
    region = load_region()
    doc = build_zones(region)
    schema = json.loads((ROOT / "contracts" / "zones.schema.json").read_text())
    errors = sorted(Draft202012Validator(schema).iter_errors(doc), key=lambda err: list(err.path))
    for err in errors:
        loc = "/".join(str(part) for part in err.path) or "<root>"
        log("s00", f"ERROR zones.geojson @ {loc}: {err.message}")
    if errors:
        log("s00", "ERROR staging failed validation; data/out/zones.geojson left untouched")
        raise SystemExit(1)
    staging = CACHE / "zones_staging.geojson"
    write_json(staging, doc)
    dest = OUT / "zones.geojson"
    OUT.mkdir(parents=True, exist_ok=True)
    backup = CACHE / "_zones_backup.geojson"
    if backup.exists():
        backup.unlink()
    moved = False
    if dest.exists():
        dest.rename(backup)
        moved = True
    try:
        staging.rename(dest)
    except Exception:
        if moved and not dest.exists():
            backup.rename(dest)
        raise
    if backup.exists():
        backup.unlink()
    log("s00", f"wrote {dest} ({len(doc['features'])} zones)")


if __name__ == "__main__":
    main()
