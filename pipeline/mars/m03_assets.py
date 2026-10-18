"""M3: every image the web app shows for Mars, painted from our own data.

    python pipeline/mars/m03_assets.py

- Globe textures for three.js: mars_color_2k.jpg (MOLA hillshade, sun from the north-west, times an albedo colour ramp)
  and mars_bump_2k.png (MOLA, contrast-stretched). Credit: painted from MOLA elevation and TES albedo (NASA / USGS).
- Map images for MapLibre, pre-warped to Web Mercator between 70 S and 70 N: mars_base_merc.jpg and the score overlay
  mars_score_merc.png (status colours, alpha scaled by score, transparent where there is no data).
- Thumbnails (512 px, 1.5 x 1.5 degrees) for the ten sites and the six SpaceX candidates: THEMIS day-IR controlled
  mosaics through the USGS Astrogeology STAC API (the same STAC search Overfield runs for Sentinel-2 over Iowa), with a
  MOLA hillshade crop as the fallback; the source is recorded per thumbnail and every file must differ (hash check).
- score_map.png for the slide: score on hillshade, sites numbered, SpaceX candidates as white rings, landmarks.
"""
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.mars import CACHE, MARS_SPHERE_CRS, OUT, config  # noqa: E402

CFG = config()
STATUS_RGB = {"go": (0x43, 0xB3, 0x6B), "watch": (0xF2, 0xA9, 0x3B), "no_go": (0xD7, 0x26, 0x3D)}
RAMP = [(0x3A, 0x24, 0x19), (0xA3, 0x53, 0x2F), (0xE0, 0xB3, 0x8A)]        # dark basalt, mid, bright dust
STAC_API = "https://stac.astrogeology.usgs.gov/api"
THEMIS_COLLECTION = "mo_themis_controlled_mosaics"
THEMIS_BUDGET_S = 20 * 60


# ---------------------------------------------------------------- helpers
def resample_global(path, width, height, lat_top=90, lat_bottom=-90, resampling=Resampling.average):
    """A cached raster onto an equirectangular canvas of width x height covering -180..180 and the given latitudes."""
    cell_x, cell_y = 360.0 / width, (lat_top - lat_bottom) / height
    out = np.full((height, width), np.nan, dtype="float32")
    with rasterio.open(path) as src:
        data = src.read(1).astype("float32")
        nod = src.nodata
        if nod is not None and not math.isnan(nod):
            data[data == nod] = np.nan
        reproject(data, out, src_transform=src.transform, src_crs=src.crs, src_nodata=np.nan,
                  dst_transform=from_origin(-180, lat_top, cell_x, cell_y), dst_crs=MARS_SPHERE_CRS, dst_nodata=np.nan, resampling=resampling)
    return out


def hillshade(elev, cell_deg, azimuth=315.0, altitude=45.0, exaggeration=1.0):
    """Lambertian hillshade of an elevation grid in metres; cell sizes from latitude so the light reads right."""
    rows, cols = elev.shape
    lats = np.linspace(90 - cell_deg / 2, -90 + cell_deg / 2, rows) if rows * cell_deg > 170 else None
    dy = cell_deg * CFG["km_per_degree_latitude"] * 1000.0
    gy, gx = np.gradient(np.nan_to_num(elev, nan=np.nanmean(elev)) * exaggeration)
    if lats is not None:
        dx = dy * np.maximum(0.05, np.cos(np.radians(lats)))[:, None]
    else:
        dx = dy
    slope = np.arctan(np.hypot(gx / dx, gy / dy))
    aspect = np.arctan2(-gx / (dx if np.isscalar(dx) else dx), gy / dy)
    az, alt = math.radians(azimuth), math.radians(altitude)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(shade, 0, 1).astype("float32")


def ramp(values, lo, hi):
    t = np.clip((values - lo) / (hi - lo), 0, 1)
    t = np.nan_to_num(t, nan=0.5)
    out = np.zeros(values.shape + (3,), dtype="float32")
    for ch in range(3):
        a, b, c = RAMP[0][ch], RAMP[1][ch], RAMP[2][ch]
        out[..., ch] = np.where(t < 0.5, a + (b - a) * (t / 0.5), b + (c - b) * ((t - 0.5) / 0.5))
    return out


def paint(width, height):
    """The painted Mars: albedo colour ramp times hillshade, equirectangular, north up, -180 at the left."""
    cell = 360.0 / width
    elev = resample_global(CACHE / "mola_16ppd.tif", width, height)
    alb = resample_global(CACHE / "Mars_MGS_TES_Albedo_mosaic_global_7410m.tif", width, height)
    shade = hillshade(elev, cell, exaggeration=1.0)
    rgb = ramp(alb, 0.08, 0.30) * (0.45 + 0.55 * shade)[..., None]
    return np.clip(rgb, 0, 255).astype("uint8"), elev


def save_jpg(arr, path, quality=85):
    Image.fromarray(arr, "RGB").save(path, "JPEG", quality=quality, optimize=True)
    print(f"  wrote {path.name}  {path.stat().st_size / 1e6:.2f} MB  {arr.shape[1]}x{arr.shape[0]}")


# ---------------------------------------------------------------- globe textures
def globe_textures():
    rgb, elev = paint(2048, 1024)
    save_jpg(rgb, OUT / "mars_color_2k.jpg")
    lo, hi = np.nanpercentile(elev, 1), np.nanpercentile(elev, 99)
    bump = np.clip((np.nan_to_num(elev, nan=lo) - lo) / (hi - lo) * 255, 0, 255).astype("uint8")
    Image.fromarray(bump, "L").save(OUT / "mars_bump_2k.png", "PNG", optimize=True)
    print(f"  wrote mars_bump_2k.png  {(OUT / 'mars_bump_2k.png').stat().st_size / 1e6:.2f} MB")
    # alignment: Olympus Mons should be the brightest high ground near column for -133.8 E, row for 18.65 N
    r, c = int((90 - 18.65) / (180 / 1024)), int((-133.8 + 180) / (360 / 2048))
    print(f"  alignment: elevation at Olympus Mons pixel ({r},{c}) = {elev[r, c]:.0f} m; Hellas pixel = {elev[int((90 + 42.4) / (180 / 1024)), int((70.5 + 180) / (360 / 2048))]:.0f} m")


# ---------------------------------------------------------------- Mercator warps
def mercator_rows(height, lat_max_deg=70.0):
    y_max = math.log(math.tan(math.pi / 4 + math.radians(lat_max_deg) / 2))
    r = np.arange(height)
    y = y_max - (r + 0.5) / height * 2 * y_max
    return np.degrees(2 * np.arctan(np.exp(y)) - math.pi / 2)


def warp_to_mercator(src, width, lat_max_deg=70.0, src_lat_top=90.0, src_lat_bottom=-90.0):
    """Sample an equirectangular array (any dtype, 2-D or 3-D) onto Web Mercator rows; columns are already linear in longitude."""
    y_max = math.log(math.tan(math.pi / 4 + math.radians(lat_max_deg) / 2))
    height = int(round(width * (2 * y_max) / (2 * math.pi)))
    lats = mercator_rows(height, lat_max_deg)
    src_rows = src.shape[0]
    rows = np.clip(((src_lat_top - lats) / (src_lat_top - src_lat_bottom) * src_rows).astype(int), 0, src_rows - 1)
    cols = np.clip((np.arange(width) / width * src.shape[1]).astype(int), 0, src.shape[1] - 1)
    return src[rows][:, cols], height


def map_images(status, score):
    rgb, _ = paint(4096, 2048)
    base, height = warp_to_mercator(rgb, 4096)
    save_jpg(base, OUT / "mars_base_merc.jpg", quality=82)
    # score overlay: status colour, alpha from the score; the scoring grid covers 60 S..60 N
    rows, cols = status.shape
    rgba = np.zeros((rows, cols, 4), dtype="uint8")
    for name, i in (("go", 0), ("watch", 1), ("no_go", 2)):
        m = status == i
        rgba[m, :3] = STATUS_RGB[name]
        rgba[m, 3] = np.clip(60 + 150 * np.nan_to_num(score[m], nan=0.0), 0, 255).astype("uint8")
    full = np.zeros((int(rows * 180 / 120), cols, 4), dtype="uint8")       # pad to -90..90 so the warp shares one frame
    top = int(rows * 30 / 120)
    full[top:top + rows] = rgba
    warped, _ = warp_to_mercator(full, 4096)
    Image.fromarray(warped, "RGBA").save(OUT / "mars_score_merc.png", "PNG", optimize=True)
    print(f"  wrote mars_score_merc.png  {(OUT / 'mars_score_merc.png').stat().st_size / 1e6:.2f} MB  {warped.shape[1]}x{warped.shape[0]} (status colours, alpha by score)")
    return height


# ---------------------------------------------------------------- thumbnails
class Themis:
    def __init__(self):
        self.ok = True
        self.started = time.time()
        self.client = None
        self.hits = {}

    def over_budget(self):
        return time.time() - self.started > THEMIS_BUDGET_S

    def thumb(self, lat, lon, size, view_deg):
        if not self.ok or self.over_budget():
            return None
        try:
            if self.client is None:
                from pystac_client import Client
                self.client = Client.open(STAC_API)
            search = self.client.search(collections=[THEMIS_COLLECTION], intersects={"type": "Point", "coordinates": [lon, lat]}, max_items=3)
            items = list(search.items())
            if not items:
                return None
            item = items[0]
            asset = next((a for k, a in item.assets.items() if a.href.lower().endswith((".tif", ".tiff"))), None)
            if asset is None:
                return None
            href = asset.href
            if href.startswith("s3://"):
                bucket, key = href[5:].split("/", 1)
                href = f"https://{bucket}.s3.us-west-2.amazonaws.com/{key}"
            from pyproj import Transformer
            with rasterio.open(href) as ds:
                tr = Transformer.from_crs(MARS_SPHERE_CRS, ds.crs, always_xy=True)
                x0, y0 = tr.transform(lon - view_deg / 2, lat - view_deg / 2)
                x1, y1 = tr.transform(lon + view_deg / 2, lat + view_deg / 2)
                win = rasterio.windows.from_bounds(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), ds.transform)
                arr = ds.read(1, window=win, out_shape=(size, size), resampling=Resampling.bilinear, masked=True).astype("float32")
            if arr.count() < size * size * 0.5:
                return None
            lo, hi = np.percentile(arr.compressed(), CFG["thumbs"]["stretch_pct"])
            img = np.clip((arr.filled(lo) - lo) / max(1e-6, hi - lo) * 255, 0, 255).astype("uint8")
            return img, f"THEMIS day IR controlled mosaic ({item.id}) via USGS Astrogeology STAC"
        except Exception as e:  # any failure: say so and fall back; never fake a frame
            print(f"    THEMIS unavailable for {lat:.2f},{lon:.2f}: {str(e)[:90]}")
            self.ok = False
            return None


def hillshade_thumb(lat, lon, size, view_deg):
    with rasterio.open(CACHE / "mola_16ppd.tif") as ds:
        win = rasterio.windows.from_bounds(lon - view_deg / 2, lat - view_deg / 2, lon + view_deg / 2, lat + view_deg / 2, ds.transform)
        arr = ds.read(1, window=win, out_shape=(size, size), resampling=Resampling.bilinear).astype("float32")
    cell = view_deg / size
    shade = hillshade(arr, cell, exaggeration=3.0)
    lo, hi = np.percentile(arr, [2, 98])
    tone = np.clip((arr - lo) / max(1.0, hi - lo), 0, 1)
    rgb = (ramp(0.08 + 0.22 * tone, 0.08, 0.30) * (0.35 + 0.65 * shade)[..., None]).astype("uint8")
    return rgb, "MOLA hillshade crop, 16 pixels per degree (fallback; no THEMIS frame)"


def thumbnails(doc):
    k = CFG["thumbs"]
    (OUT / "thumbs").mkdir(parents=True, exist_ok=True)
    themis = Themis()
    hashes = {}
    places = [(s["id"], s["lat"], s["lon"]) for s in doc["sites"]] + [(c["id"], c["lat"], c["lon"]) for c in doc["spacex_candidates"] if not c.get("missing")]
    sources = {}
    cache_dir = CACHE / "thumbs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for pid, lat, lon in places:
        path = OUT / "thumbs" / f"{pid}.jpg"
        cached = cache_dir / f"{pid}_{lat:.3f}_{lon:.3f}.jpg"
        cached_src = cached.with_suffix(".txt")
        if cached.exists() and cached_src.exists():
            path.write_bytes(cached.read_bytes())
            source = cached_src.read_text(encoding="utf-8")
        else:
            got = themis.thumb(lat, lon, k["size_px"], k["view_deg"])
            if got is not None:
                img, source = got
                image = Image.fromarray(img, "L").convert("RGB")
            else:
                rgb, source = hillshade_thumb(lat, lon, k["size_px"], k["view_deg"])
                image = Image.fromarray(rgb, "RGB")
            image.save(path, "JPEG", quality=80, optimize=True)
            if "THEMIS" in source:                      # only a real frame is worth keeping; a fallback is retried next run
                cached.write_bytes(path.read_bytes())
                cached_src.write_text(source, encoding="utf-8")
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        if h in hashes:
            raise SystemExit(f"  thumbnail {pid} is identical to {hashes[h]}: refusing to ship duplicate images")
        hashes[h] = pid
        sources[pid] = source
        print(f"    {pid}: {source.split(' (')[0]}")
    return sources


# ---------------------------------------------------------------- slide
def font(size):
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def slide(doc, status, score):
    width = 2400
    height = int(width * 120 / 360)
    elev = resample_global(CACHE / "mola_16ppd.tif", width, height, lat_top=60, lat_bottom=-60)
    shade = hillshade(elev, 360 / width)
    grey = (70 + 150 * shade).astype("uint8")
    img = Image.fromarray(np.stack([grey] * 3, axis=-1), "RGB").convert("RGBA")
    rows, cols = status.shape
    rgba = np.zeros((rows, cols, 4), dtype="uint8")
    for name, i in (("go", 0), ("watch", 1), ("no_go", 2)):
        m = status == i
        rgba[m, :3] = STATUS_RGB[name]
        rgba[m, 3] = np.clip(40 + 140 * np.nan_to_num(score[m], nan=0.0), 0, 255).astype("uint8")
    overlay = Image.fromarray(rgba, "RGBA").resize((width, height), Image.BILINEAR)
    img.alpha_composite(overlay)
    draw = ImageDraw.Draw(img)
    f_small, f_big, f_cap = font(22), font(30), font(26)

    def xy(lat, lon):
        return ((lon + 180) / 360 * width, (60 - lat) / 120 * height)
    for lm in doc["landmarks"]:
        x, y = xy(lm["lat"], lm["lon"])
        draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(255, 255, 255, 200))
        draw.text((x + 8, y - 12), lm["name"], fill=(235, 235, 235, 255), font=f_small)
    for c in doc["spacex_candidates"]:
        if c.get("missing"):
            continue
        x, y = xy(c["lat"], c["lon"])
        draw.ellipse([x - 14, y - 14, x + 14, y + 14], outline=(255, 255, 255, 255), width=4)
    for s in doc["sites"]:
        x, y = xy(s["lat"], s["lon"])
        col = STATUS_RGB[s["status"]] + (255,)
        draw.ellipse([x - 20, y - 20, x + 20, y + 20], fill=col, outline=(10, 10, 10, 255), width=2)
        draw.text((x - 12 if s["rank"] < 10 else x - 17, y - 16), str(s["rank"]), fill=(10, 14, 18, 255), font=f_big)
    caption = ("Two factors from SpaceX's criteria (ice, low elevation), two of ours (plume-safe ground, science reach). "
               "Independent analysis; not affiliated with SpaceX.")
    draw.rectangle([0, height - 48, width, height], fill=(13, 18, 23, 220))
    draw.text((16, height - 40), caption, fill=(238, 242, 238, 255), font=f_cap)
    draw.text((16, 12), "Where Starship should land: Overfield's screen of Mars, 60 S to 60 N. White rings: SpaceX's 2019 HiRISE sites. Numbers: our top ten.",
              fill=(238, 242, 238, 255), font=f_cap)
    img.convert("RGB").save(OUT / "score_map.png", "PNG", optimize=True)
    print(f"  wrote score_map.png  {(OUT / 'score_map.png').stat().st_size / 1e6:.2f} MB")


def main():
    print("M3 assets")
    OUT.mkdir(parents=True, exist_ok=True)
    sites_path = OUT / "sites.json"
    if not sites_path.exists():
        sys.exit("  sites.json missing: run python pipeline/mars/m02_score.py first")
    doc = json.loads(sites_path.read_text(encoding="utf-8"))
    grid = np.load(CACHE / "grid.npz")
    status, score = grid["status"], grid["score"]
    print("  globe textures")
    globe_textures()
    print("  map images")
    map_images(status, score)
    print("  thumbnails")
    sources = thumbnails(doc)
    for s in doc["sites"]:
        s["thumb_source"] = sources.get(s["id"])
    for c in doc["spacex_candidates"]:
        c["thumb_source"] = sources.get(c["id"])
    doc["assets"]["thumb_sources"] = "THEMIS where the STAC search returned a frame, else a MOLA hillshade crop; listed per site"
    from pipeline.mars import CONTRACTS, write_json_atomic
    write_json_atomic(sites_path, doc, CONTRACTS / "mars_sites.schema.json")
    print("  slide")
    slide(doc, status, score)
    total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file()) + (OUT.parent / "worlds.json").stat().st_size
    print(f"  data/out/mars + worlds.json: {total / 1e6:.1f} MB (budget 15)")


if __name__ == "__main__":
    main()
