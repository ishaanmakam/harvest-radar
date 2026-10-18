"""Draw field outlines on the latest satellite frame. No API calls.

    python vision/overlay_fields.py

Reads data/out/fields.geojson and the latest frame in data/out/frames/frames.json.
Writes data/out/perceptron/validation/fields_overlay.png.
"""
import json
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw
from shapely.geometry import box as shapely_box
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ROOT / "data" / "out" / "fields.geojson"
FRAMES = ROOT / "data" / "out" / "frames" / "frames.json"
OUT = ROOT / "data" / "out" / "perceptron" / "validation" / "fields_overlay.png"
COLORS = {"corn": "#f4a259", "soybean": "#3bb273", "unknown": "#cccccc"}


def lonlat_to_px(lon, lat, bounds, width, height):
    west, south, east, north = bounds
    x = (lon - west) / (east - west) * width
    y = (north - lat) / (north - south) * height
    return (x, y)


def main():
    frames = json.loads(FRAMES.read_text())
    latest = max(frames["frames"], key=lambda f: f["date"])
    bounds = frames["bounds"]
    west, south, east, north = bounds
    frame_box = shapely_box(west, south, east, north)
    im = Image.open(ROOT / "data" / "out" / latest["rgb"]).convert("RGB")
    # Pillow strokes are integer pixels. Draw at 2x with width 3, then scale back: 3/2 = 1.5 px.
    scale = 2
    overlay = Image.new("RGBA", (im.width * scale, im.height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    features = json.loads(FIELDS.read_text())["features"]
    inside = 0
    per_zone = Counter()
    for feat in features:
        props = feat["properties"]
        per_zone[props.get("zone_id")] += 1
        geom = shape(feat["geometry"])
        if geom.covered_by(frame_box):
            inside += 1
        color = COLORS.get(props.get("crop"), COLORS["unknown"])
        rings = geom.geoms if geom.geom_type == "MultiPolygon" else (geom,)
        for poly in rings:
            for ring in (poly.exterior, *poly.interiors):
                pts = [tuple(c * scale for c in lonlat_to_px(x, y, bounds, im.width, im.height)) for x, y in ring.coords]
                if len(pts) >= 2:
                    draw.line(pts, fill=color, width=3)
    overlay = overlay.resize(im.size, Image.Resampling.LANCZOS)
    im = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    im.save(OUT)
    zone_bits = ", ".join(f"{zid}={per_zone[zid]}" for zid in sorted(per_zone))
    print(f"{len(features)} fields, {inside} fully inside frame bounds")
    print(f"per zone: {zone_bits}")
    print(f"latest frame {latest['date']} ({im.width}x{im.height})")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
