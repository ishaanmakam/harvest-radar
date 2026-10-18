"""Field-pattern previews for the 24 world regions in data/global/regions.json.

Each region gets a 512 px square PNG over its own 5.5 km box: a plausible field mosaic for that kind of farmland
(quarter sections on the Plains, long paddocks in Western Australia, small plots in Punjab and Kano, paddies in the
deltas), coloured on an NDVI ramp from the region's own current canopy value and stage. These are drawn patterns
for the demo map, not satellite reads; the region card says so. Output: data/global/overlays/<id>.png and
data/global/overlays.json with the box of each image.

    python scripts/make_region_overlays.py
"""
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ROOT / "data/global/regions.json"
OUT = ROOT / "data/global/overlays"
PX = 512

# field geometry by country: (min block, max block) in metres, jitter, water fraction for paddies
STYLE = {
    "USA": (380, 820, 0.05, 0.0), "Canada": (500, 900, 0.05, 0.0), "Australia": (800, 1600, 0.08, 0.0),
    "Kazakhstan": (700, 1400, 0.06, 0.0), "Russia": (600, 1200, 0.06, 0.0), "Ukraine": (500, 1100, 0.06, 0.0),
    "Argentina": (400, 800, 0.08, 0.0), "Brazil": (500, 1100, 0.10, 0.0), "South Africa": (300, 700, 0.10, 0.0),
    "France": (180, 450, 0.12, 0.0), "Mexico": (150, 400, 0.15, 0.05), "China": (90, 260, 0.18, 0.0),
    "India": (60, 170, 0.22, 0.10), "Pakistan": (70, 190, 0.22, 0.05), "Nigeria": (50, 150, 0.30, 0.0),
    "Kenya": (80, 220, 0.25, 0.0), "Egypt": (60, 160, 0.20, 0.12), "Vietnam": (50, 140, 0.15, 0.25),
    "Indonesia": (40, 120, 0.25, 0.25),
}
RAMP = [(0.05, (0x8c, 0x51, 0x0a)), (0.22, (0xd8, 0xb3, 0x65)), (0.33, (0xe8, 0xe2, 0xb0)), (0.48, (0xa6, 0xd9, 0x6a)),
        (0.68, (0x1a, 0x98, 0x50)), (0.88, (0x00, 0x68, 0x37))]
WATER = (0x2b, 0x4f, 0x6e)
ROAD = (0x3a, 0x3a, 0x3a)


def ramp(ndvi):
    """NDVI array -> RGB array on the ramp above."""
    out = np.zeros(ndvi.shape + (3,), dtype=np.float32)
    xs = np.array([k for k, _ in RAMP])
    for c in range(3):
        ys = np.array([v[c] for _, v in RAMP], dtype=np.float32)
        out[..., c] = np.interp(ndvi, xs, ys)
    return out


def partition(total_m, lo, hi, rng):
    """Cut a length into field widths between lo and hi metres."""
    cuts, pos = [0.0], 0.0
    while pos < total_m:
        pos += rng.uniform(lo, hi)
        cuts.append(min(pos, total_m))
    return np.array(cuts)


def field_mosaic(region, rng):
    lo, hi, jitter, water = STYLE.get(region["country"], (150, 400, 0.15, 0.0))
    box_m = 5500.0
    m_per_px = box_m / PX
    cols = partition(box_m, lo, hi, rng) / m_per_px
    rows = partition(box_m, lo * 0.8, hi * 1.2, rng) / m_per_px
    base = float((region.get("current") or {}).get("value") or 0.3)
    peak = float((region.get("peak") or {}).get("value") or 0.8)
    stage = (region.get("stage") or "").lower()
    ndvi = np.zeros((PX, PX), dtype=np.float32)
    kind = np.zeros((PX, PX), dtype=np.uint8)  # 0 field, 1 water, 2 road
    # how the blocks spread around the region value: a harvested region is mostly bare with a few late fields
    if "harvest" in stage or "senesc" in stage or "off-season" in stage:
        mix = [(0.55, (0.12, 0.22)), (0.35, (base - 0.05, base + 0.08)), (0.10, (0.45, min(0.75, peak)))]
    elif "growing" in stage or "peak" in stage:
        mix = [(0.70, (max(0.45, base - 0.12), min(0.92, base + 0.08))), (0.20, (0.3, 0.5)), (0.10, (0.12, 0.22))]
    elif "drying" in stage:
        mix = [(0.50, (base - 0.1, base + 0.1)), (0.30, (0.25, 0.4)), (0.20, (0.5, 0.7))]
    else:
        mix = [(0.60, (base - 0.1, base + 0.1)), (0.25, (0.12, 0.25)), (0.15, (0.4, 0.65))]
    weights = np.array([w for w, _ in mix]); weights /= weights.sum()
    for i in range(len(rows) - 1):
        for j in range(len(cols) - 1):
            r0, r1 = int(rows[i]), int(rows[i + 1])
            c0, c1 = int(cols[j]), int(cols[j + 1])
            if r1 <= r0 or c1 <= c0:
                continue
            k = rng.choice(len(mix), p=weights)
            lo_v, hi_v = mix[k][1]
            v = float(np.clip(rng.uniform(lo_v, hi_v), 0.05, 0.95))
            # a slight shear so the grid is not perfectly square
            dr = int(rng.uniform(-jitter, jitter) * (r1 - r0))
            rr0, rr1 = max(0, r0 + dr), min(PX, r1 + dr)
            ndvi[rr0:rr1, c0:c1] = v
            if water and rng.random() < water:
                kind[rr0:rr1, c0:c1] = 1
    # within-field texture: smooth noise plus row striping on the big fields
    noise = rng.normal(0, 1, (PX // 8, PX // 8)).astype(np.float32)
    noise = np.array(Image.fromarray(noise).resize((PX, PX), Image.BILINEAR))
    ndvi += 0.05 * noise
    yy = np.arange(PX)[:, None]
    ndvi += 0.015 * np.sin(yy / (2.0 + rng.uniform(0, 1.5)))
    # field roads along the grid lines for the block regions, a river for the delta ones
    if lo >= 150:
        for c in cols[1:-1]:
            c = int(c); kind[:, max(0, c - 1):c + 1] = 2
        for r in rows[1:-1]:
            r = int(r); kind[max(0, r - 1):r + 1, :] = 2
    if water >= 0.1 or region["id"] in ("eg-nile-delta", "vn-mekong", "ke-trans-nzoia"):
        x = rng.uniform(PX * 0.2, PX * 0.8)
        for y in range(PX):
            x += rng.normal(0, 1.6)
            x = float(np.clip(x, 2, PX - 3))
            kind[y, int(x) - 2:int(x) + 3] = 1
    return np.clip(ndvi, 0.02, 0.98), kind


def render(region):
    seed = int(hashlib.sha256(region["id"].encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    ndvi, kind = field_mosaic(region, rng)
    rgb = ramp(ndvi)
    rgb[kind == 1] = WATER
    rgb[kind == 2] = ROAD
    img = Image.fromarray(rgb.astype(np.uint8), "RGB").filter(ImageFilter.GaussianBlur(0.6))
    rgba = img.convert("RGBA")
    a = np.full((PX, PX), 235, dtype=np.uint8)
    rgba.putalpha(Image.fromarray(a))
    return rgba


def main():
    doc = json.loads(REGIONS.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    index = {"note": "Drawn field-pattern previews coloured from each region's canopy value and stage; not satellite reads.",
             "size_px": PX, "box_km": 5.5, "overlays": {}}
    for region in doc["regions"]:
        img = render(region)
        path = OUT / f"{region['id']}.png"
        img.save(path, "PNG", optimize=True)
        index["overlays"][region["id"]] = {"file": f"overlays/{region['id']}.png", "bbox": region["bbox"]}
        print(f"  {region['id']:18s} {path.stat().st_size / 1024:6.0f} KB  stage: {region.get('stage')}")
    (ROOT / "data/global/overlays.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(index['overlays'])} overlays to {OUT}")


if __name__ == "__main__":
    main()
