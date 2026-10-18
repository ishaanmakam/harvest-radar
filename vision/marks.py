"""Shared image helpers for field marks. No API calls.

crop_bounds is the pixel-window math analyze_timelapse.crop_zone uses.
Coordinates: window and frame bounds are [W, S, E, N].
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import shape

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def bold_font(size):
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def crop_bounds(frame_path, bounds, bbox_lonlat, pad_px=0):
    """Int-rounded pixel window of bbox_lonlat on a frame that covers bounds.

    Returns (PIL image, [W, S, E, N] of that exact window). pad_px grows the
    window in pixels before the crop. Same truncation as crop_zone.
    """
    im = Image.open(frame_path)
    west, south, east, north = bounds
    x0 = (bbox_lonlat[0] - west) / (east - west) * im.width
    x1 = (bbox_lonlat[2] - west) / (east - west) * im.width
    y0 = (north - bbox_lonlat[3]) / (north - south) * im.height
    y1 = (north - bbox_lonlat[1]) / (north - south) * im.height
    ix0, iy0 = int(x0) - pad_px, int(y0) - pad_px
    ix1, iy1 = int(x1) + pad_px, int(y1) + pad_px
    cropped = im.crop((ix0, iy0, ix1, iy1))
    cropped.load()
    window = [
        west + (ix0 / im.width) * (east - west),
        north - (iy1 / im.height) * (north - south),
        west + (ix1 / im.width) * (east - west),
        north - (iy0 / im.height) * (north - south),
    ]
    im.close()
    return cropped, window


def upscale(img, long_side=768):
    """Grow the image so its long side is long_side. Never downscales. Returns (img, scale)."""
    current = max(img.size)
    if 0 < current < long_side:
        scale = long_side / current
        img = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
            Image.LANCZOS,
        )
        return img, scale
    return img, 1.0


def lonlat_to_px(lon, lat, window, width, height):
    west, south, east, north = window
    x = (lon - west) / (east - west) * width
    y = (north - lat) / (north - south) * height
    return (x, y)


def px_to_lonlat(x, y, window, width, height):
    west, south, east, north = window
    lon = west + (x / width) * (east - west)
    lat = north - (y / height) * (north - south)
    return (lon, lat)


def margin_bbox(bbox, frac):
    """Expand [W, S, E, N] by frac of its width and height on each side."""
    west, south, east, north = bbox
    dx = (east - west) * frac or 1e-5
    dy = (north - south) * frac or 1e-5
    return [west - dx, south - dy, east + dx, north + dy]


def _as_geom(field):
    geom = field["geometry"] if isinstance(field, dict) and "geometry" in field else field
    geom = geom if hasattr(geom, "geom_type") else shape(geom)
    if not geom.is_valid:
        geom = geom.buffer(0)
    return geom


def _field_id(field, index):
    if isinstance(field, dict):
        if "field_id" in field:
            return field["field_id"]
        props = field.get("properties") or {}
        if props.get("field_id"):
            return props["field_id"]
    return index


def _polygons(geom):
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type == "MultiPolygon":
        return list(geom.geoms)
    return []


def _label_offsets(step, rings=10):
    yield 0, 0
    for ring in range(1, rings + 1):
        dist = ring * step
        for i in range(-ring, ring + 1):
            yield i * step, -dist
            yield i * step, dist
        for j in range(-ring + 1, ring):
            yield -dist, j * step
            yield dist, j * step


def _overlaps(box, placed, gap):
    x0, y0, x1, y1 = box
    for ox0, oy0, ox1, oy1 in placed:
        if x0 < ox1 + gap and x1 > ox0 - gap and y0 < oy1 + gap and y1 > oy0 - gap:
            return True
    return False


def _inside(box, width, height):
    return box[0] >= 0 and box[1] >= 0 and box[2] <= width and box[3] <= height


def draw_marks(img, window_bounds, fields, colour="#ffe14d", colours=None):
    """Outline each field (3 px) and put a 1..N label inside it. Returns (img, {n: field_id}).

    colours, when set, is {field_id: outline colour} and overrides colour per field.
    """
    canvas = img.convert("RGB") if img.mode != "RGB" else img.copy()
    draw = ImageDraw.Draw(canvas)
    width, height = canvas.size
    font = bold_font(max(14, min(28, round(min(width, height) / 28))))
    prepared = []
    for index, field in enumerate(fields, start=1):
        geom = _as_geom(field)
        fid = _field_id(field, index)
        outline = (colours or {}).get(fid, colour)
        for poly in _polygons(geom):
            for ring in (poly.exterior, *poly.interiors):
                pts = [lonlat_to_px(x, y, window_bounds, width, height) for x, y in ring.coords]
                if len(pts) >= 2:
                    draw.line(pts, fill=outline, width=3)
        point = geom.representative_point()
        prepared.append((index, fid, point.x, point.y))

    placed = []
    id_map = {}
    gap = 2
    for number, field_id, lon, lat in prepared:
        cx, cy = lonlat_to_px(lon, lat, window_bounds, width, height)
        text = str(number)
        ink = draw.textbbox((0, 0), text, font=font)
        tw, th = ink[2] - ink[0], ink[3] - ink[1]
        pad = 3
        box_w, box_h = tw + pad * 2, th + pad * 2
        step = max(box_h, 8)
        chosen = None
        fallback = None
        for dx, dy in _label_offsets(step):
            box = [cx - box_w / 2 + dx, cy - box_h / 2 + dy, cx + box_w / 2 + dx, cy + box_h / 2 + dy]
            if _overlaps(box, placed, gap):
                continue
            fallback = box
            if _inside(box, width, height):
                chosen = box
                break
        box = chosen or fallback or [cx - box_w / 2, cy - box_h / 2, cx + box_w / 2, cy + box_h / 2]
        placed.append(box)
        radius = min(4, int(box_h / 2))
        draw.rounded_rectangle(box, radius=radius, fill="black")
        draw.text((box[0] + pad - ink[0], box[1] + pad - ink[1]), text, fill="white", font=font)
        id_map[number] = field_id
    return canvas, id_map


def draw_seed(img, x, y):
    """Red dot, radius 7 px, with a 2 px white ring. Draws on img."""
    draw = ImageDraw.Draw(img)
    outer = 7 + 2
    draw.ellipse([x - outer, y - outer, x + outer, y + outer], fill="white")
    draw.ellipse([x - 7, y - 7, x + 7, y + 7], fill="red")
    return img


def _bounds_of(fields):
    boxes = [_as_geom(field).bounds for field in fields]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _split_fields(fields):
    west, south, east, north = _bounds_of(fields)
    along_x = (east - west) >= (north - south)
    mid = (west + east) / 2 if along_x else (south + north) / 2

    def coord(field):
        point = _as_geom(field).centroid
        return point.x if along_x else point.y

    left = [field for field in fields if coord(field) < mid]
    right = [field for field in fields if coord(field) >= mid]
    if left and right:
        return left, right
    ordered = sorted(fields, key=coord)
    half = max(1, len(ordered) // 2)
    return ordered[:half], ordered[half:]


def _group(fields, max_per_group):
    if len(fields) <= max_per_group:
        return [(margin_bbox(_bounds_of(fields), 0.15), list(fields))]
    left, right = _split_fields(fields)
    return _group(left, max_per_group) + _group(right, max_per_group)


def group_fields(fields, max_per_group=12):
    """Split fields into compact groups of at most max_per_group.

    Halves the current bbox along its longer side. Each item is
    ([W, S, E, N] with a 15% margin, fields).
    """
    fields = list(fields)
    if not fields:
        return []
    return _group(fields, max_per_group)
