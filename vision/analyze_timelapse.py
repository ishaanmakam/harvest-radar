"""Perceptron reads each zone's season as a time-lapse and reports when/where harvest and burning happened.

    python vision/analyze_timelapse.py --data data/out [--mode images|video] [--zones Z01,Z05]

Output: insights.json (contract: contracts/insights.schema.json), merged into the UI zone cards.

mode=images (default, robust): sends the last N dated frames, cropped to the zone, as separate images.
mode=video : stitches the cropped frames into an mp4 at 1 frame/second and asks for clip annotations;
             clip seconds map back to dates (second k = frame k).
Cost control: each call is small (a few cropped images); check data/out/perceptron/spend.json.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from perceptron_client import MODEL, ask, clean_text, parse_annotations, spent_usd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def crop_zone(img_path, bounds, zbbox, out_path):
    im = Image.open(img_path)
    W, S, E, N = bounds
    x0 = (zbbox[0] - W) / (E - W) * im.width
    x1 = (zbbox[2] - W) / (E - W) * im.width
    y0 = (N - zbbox[3]) / (N - S) * im.height
    y1 = (N - zbbox[1]) / (N - S) * im.height
    im.crop((int(x0), int(y0), int(x1), int(y1))).save(out_path, quality=90)
    return out_path


def zone_bbox(feat):
    xs = [p[0] for p in feat["geometry"]["coordinates"][0]]
    ys = [p[1] for p in feat["geometry"]["coordinates"][0]]
    return [min(xs), min(ys), max(xs), max(ys)]


def parse_counts(text):
    counts = {}
    for key in ("standing", "harvested", "burned"):
        m = re.search(rf"(\d+)\s+(?:\w+\s+){{0,2}}{key}|{key}\D{{0,15}}(\d+)", text, re.I)
        if m:
            counts[key] = int(m.group(1) or m.group(2))
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--mode", choices=["images", "video"], default="images")
    ap.add_argument("--frames", type=int, default=6, help="how many most-recent clear frames to send")
    ap.add_argument("--zones", default="")
    args = ap.parse_args()
    data = Path(args.data)
    frames = json.loads((data / "frames" / "frames.json").read_text())
    zones = json.loads((data / "zones.geojson").read_text())["features"]
    if args.zones:
        keep = set(args.zones.split(","))
        zones = [z for z in zones if z["properties"]["zone_id"] in keep]
    picks = frames["frames"][-args.frames:]
    tmp = data / "perceptron" / "crops"
    tmp.mkdir(parents=True, exist_ok=True)

    items = []
    for z in zones:
        zid, name = z["properties"]["zone_id"], z["properties"]["name"]
        crops = [crop_zone(data / f["rgb"], frames["bounds"], zone_bbox(z), tmp / f"{zid}_{f['date']}.jpg") for f in picks]
        legend = ", ".join(f"image {i} = {f['date']}" for i, f in enumerate(picks))
        base_q = ("Sentinel-2 satellite images (10 m pixels, north up) of the same ~3 km patch of rice farmland in Punjab, India. "
                  "Green = standing rice, tan/light brown = harvested, very dark/black = burned stubble. ")
        if args.mode == "video":
            import imageio.v2 as imageio
            mp4 = tmp / f"{zid}.mp4"
            imageio.mimsave(mp4, [imageio.imread(c) for c in crops], fps=1)
            text, _ = ask([mp4], base_q + f"This video shows one satellite pass per second: {legend.replace('image', 'second')}. "
                          "Return clip annotations for when large-scale harvesting happens and when burn scars appear. "
                          "Then give approximate counts of standing, harvested and burned fields in the final frame.",
                          annotation_format="clip", label=f"timelapse_{zid}")
        else:
            text, _ = ask(crops, base_q + f"The images are in date order: {legend}. "
                          "In one or two sentences, describe when and where harvesting and burning happened across these dates. "
                          "Then give approximate counts of standing, harvested and burned fields in the last image, "
                          "formatted exactly as: standing=N harvested=N burned=N",
                          label=f"timelapse_{zid}")
        clips = []
        for a in parse_annotations(text):
            if a["kind"] == "clip" and a["t"]:
                i0 = min(len(picks) - 1, max(0, int(a["t"][0])))
                i1 = min(len(picks) - 1, max(0, int(a["t"][-1])))
                clips.append({"start": picks[i0]["date"], "end": picks[i1]["date"], "label": a["mention"] or ""})
        m = re.search(r"standing\s*=\s*(\d+).*?harvested\s*=\s*(\d+).*?burned\s*=\s*(\d+)", text, re.I | re.S)
        counts = ({"standing": int(m.group(1)), "harvested": int(m.group(2)), "burned": int(m.group(3))} if m else parse_counts(text))
        summary = re.sub(r"standing\s*=.*$", "", clean_text(text), flags=re.I | re.S).strip()
        items.append({"zone_id": zid, "kind": "timelapse", "summary": summary[:400] or "(no summary)",
                      "counts": counts, "clips": clips, "raw": text})
        print(f"  {name}: {counts} | {summary[:120]}")

    out = {"model": MODEL, "spent_usd": round(spent_usd(), 4), "items": items}
    (data / "insights.json").write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {data / 'insights.json'}")


if __name__ == "__main__":
    main()
