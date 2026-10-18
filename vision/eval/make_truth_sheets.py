"""Labelling sheets for Oct 10 vs Oct 17. No API calls and no NDVI.

    python vision/eval/make_truth_sheets.py

One pair per field (earlier date on the left), 12 pairs per sheet.
Also writes vision/eval/truth_template.json with null labels for every field.
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marks import bold_font, crop_bounds, margin_bbox  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ROOT / "data" / "out" / "fields.geojson"
FRAMES = ROOT / "data" / "out" / "frames" / "frames.json"
OUT_DIR = ROOT / "data" / "out" / "perceptron" / "eval" / "truth_sheets"
TEMPLATE = Path(__file__).resolve().parent / "truth_template.json"
PREV = "2026-10-10"
LATEST = "2026-10-17"
THUMB_H = 220
YELLOW = "#ffe14d"
PAD = 18
COL_GAP = 22
ROW_GAP = 18
LABEL_H = 28
PAIR_GAP = 8
COLS = 3
PER_SHEET = 12


def clip_bbox(bbox, frame):
    return [
        max(bbox[0], frame[0]),
        max(bbox[1], frame[1]),
        min(bbox[2], frame[2]),
        min(bbox[3], frame[3]),
    ]


def field_bbox(feat):
    minx, miny, maxx, maxy = shape(feat["geometry"]).bounds
    return margin_bbox([minx, miny, maxx, maxy], 0.40)


def thumbnail(frame_path, frame_bounds, feat, bbox):
    crop, window = crop_bounds(frame_path, frame_bounds, bbox)
    scale = THUMB_H / crop.height
    thumb = crop.resize((max(1, round(crop.width * scale)), THUMB_H), Image.LANCZOS)
    sx = thumb.width / crop.width
    sy = thumb.height / crop.height
    draw = ImageDraw.Draw(thumb)
    geom = shape(feat["geometry"])
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    west, south, east, north = window
    for poly in polys:
        for ring in (poly.exterior, *poly.interiors):
            pts = []
            for lon, lat in ring.coords:
                x = (lon - west) / (east - west) * crop.width * sx
                y = (north - lat) / (north - south) * crop.height * sy
                pts.append((x, y))
            if len(pts) >= 2:
                draw.line(pts, fill=YELLOW, width=2)
    return thumb


def render_sheet(pairs, dest):
    pair_widths = [left.width + PAIR_GAP + right.width for _, left, right in pairs]
    used_cols = min(COLS, len(pairs))
    col_w = [max(pair_widths[i] for i in range(c, len(pairs), COLS)) for c in range(used_cols)]
    row_h = LABEL_H + THUMB_H
    rows = (len(pairs) + COLS - 1) // COLS
    sheet_w = PAD * 2 + sum(col_w) + COL_GAP * (used_cols - 1)
    sheet = Image.new("RGB", (sheet_w, PAD * 2 + rows * row_h + (rows - 1) * ROW_GAP), "white")
    draw = ImageDraw.Draw(sheet)
    font = bold_font(18)
    x0 = []
    cursor = PAD
    for width in col_w:
        x0.append(cursor)
        cursor += width + COL_GAP
    for i, (field_id, left, right) in enumerate(pairs):
        col, row = i % COLS, i // COLS
        x = x0[col] + (col_w[col] - pair_widths[i]) // 2
        y = PAD + row * (row_h + ROW_GAP)
        ink = draw.textbbox((0, 0), field_id, font=font)
        tw = ink[2] - ink[0]
        draw.text((x + (pair_widths[i] - tw) / 2 - ink[0], y + (LABEL_H - (ink[3] - ink[1])) / 2 - ink[1]), field_id, fill="black", font=font)
        sheet.paste(left, (x, y + LABEL_H))
        sheet.paste(right, (x + left.width + PAIR_GAP, y + LABEL_H))
    sheet.save(dest, quality=85)


def main():
    frames = json.loads(FRAMES.read_text())
    by_date = {f["date"]: ROOT / "data" / "out" / f["rgb"] for f in frames["frames"]}
    features = sorted(json.loads(FIELDS.read_text())["features"], key=lambda f: f["properties"]["field_id"])
    pairs = []
    for feat in features:
        bbox = clip_bbox(field_bbox(feat), frames["bounds"])
        left = thumbnail(by_date[PREV], frames["bounds"], feat, bbox)
        right = thumbnail(by_date[LATEST], frames["bounds"], feat, bbox)
        pairs.append((feat["properties"]["field_id"], left, right))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for start in range(0, len(pairs), PER_SHEET):
        dest = OUT_DIR / f"sheet_{start // PER_SHEET + 1:02d}.jpg"
        render_sheet(pairs[start:start + PER_SHEET], dest)
        paths.append(dest)
    template = {
        "frame_prev": PREV,
        "frame_latest": LATEST,
        "fields": {field_id: {"state_oct17": None, "changed": None} for field_id, _, _ in pairs},
    }
    TEMPLATE.write_text(json.dumps(template, indent=2) + "\n")
    print(f"{len(pairs)} fields, {len(paths)} sheets")
    for path in paths:
        print(path.relative_to(ROOT))
    print(TEMPLATE.relative_to(ROOT))


if __name__ == "__main__":
    main()
