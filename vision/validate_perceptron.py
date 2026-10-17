"""FIRST-HOUR TEST: can Perceptron read our satellite imagery?

    python vision/validate_perceptron.py                     # uses latest clear frame in data/out (else data/mock)
    python vision/validate_perceptron.py --image path.jpg

Runs three cheap probes on one image and saves raw + parsed output to data/out/perceptron/validation/:
  1. field boundaries as polygons      -> could replace our segmentation fallback
  2. boxes for harvested / standing / burned fields + counts
  3. (if >=4 frames exist) multi-image change question across dates

Decision rule after running:
  - polygons line up with real fields  -> Perceptron draws fields.geojson (source="perceptron")
  - polygons are junk, counts are sane -> keep segmentation for fields, use Perceptron for reading change/counts
  - both junk                           -> Perceptron only narrates the time-lapse; say so honestly in the pitch
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from perceptron_client import ask, clean_text, norm_to_lonlat, parse_annotations  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "data" / "out" / "perceptron" / "validation"


def pick_frames():
    for base in (ROOT / "data" / "out", ROOT / "data" / "mock"):
        fj = base / "frames" / "frames.json"
        if fj.exists():
            doc = json.loads(fj.read_text())
            if doc["frames"]:
                return base, doc
    sys.exit("No frames found. Run pipeline/s05_frames.py (Person A) (or scripts/make_mock_data.py) first.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    args = ap.parse_args()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    base, frames = pick_frames()
    bounds = frames["bounds"]
    latest = base / frames["frames"][-1]["rgb"]
    img = Path(args.image) if args.image else latest
    print(f"image: {img}  (data from {base.name})")
    results = {}

    # 1. field boundaries
    text, _ = ask([img], (
        "This is a true-colour Sentinel-2 satellite image (10 m pixels, north up) of farmland in Punjab, India, "
        "in mid-October during the rice harvest. Outline individual agricultural fields as polygons. "
        "Return at most 40 polygons, the clearest ones."), annotation_format="polygon", label="fields_polygon")
    polys = [a for a in parse_annotations(text) if a["kind"] == "polygon" and len(a["coords"]) >= 3]
    feats = [{"type": "Feature", "properties": {"mention": p["mention"]},
              "geometry": {"type": "Polygon", "coordinates": [[list(norm_to_lonlat(x, y, bounds)) for x, y in p["coords"] + [p["coords"][0]]]]}}
             for p in polys]
    (OUTDIR / "fields_perceptron.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    results["fields_polygon"] = {"n_polygons": len(polys), "raw": text}
    print(f"  -> {len(polys)} polygons (open fields_perceptron.geojson on geojson.io over satellite to judge)")

    # 2. state boxes + counts
    text, _ = ask([img], (
        "Satellite image of rice fields in Punjab during harvest season. Green fields are standing rice, "
        "light brown/tan fields are harvested, very dark/black patches are burned stubble. "
        "Mark each burned field and each harvested field with a box, label the mention as 'burned' or 'harvested'. "
        "Then state the approximate counts of standing, harvested and burned fields."), annotation_format="box", label="state_boxes")
    boxes = [a for a in parse_annotations(text) if a["kind"] == "point_box"]
    by = {}
    for b in boxes:
        by[b["mention"]] = by.get(b["mention"], 0) + 1
    results["state_boxes"] = {"by_mention": by, "summary": clean_text(text), "raw": text}
    print(f"  -> boxes by label: {by}\n  -> summary: {clean_text(text)[:300]}")

    # 3. multi-date change
    if len(frames["frames"]) >= 4:
        picks = frames["frames"][-4:]
        dates = ", ".join(f"image {i} = {f['date']}" for i, f in enumerate(picks))
        text, _ = ask([base / f["rgb"] for f in picks], (
            f"These are satellite images of the same farmland on different dates ({dates}). "
            "Describe how the fields change across the dates: where did green rice turn bare (harvested), "
            "and where did dark burn scars appear? Mention which part of the image (north/south/east/west) and which dates."),
            label="multi_date_change")
        results["multi_date_change"] = {"answer": clean_text(text), "raw": text}
        print(f"  -> change: {clean_text(text)[:400]}")

    (OUTDIR / "results.json").write_text(json.dumps(results, indent=1))
    print(f"\nSaved to {OUTDIR}. Post the verdict in the team chat.")


if __name__ == "__main__":
    main()
