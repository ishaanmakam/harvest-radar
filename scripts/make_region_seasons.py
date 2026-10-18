"""A stand-in season for every world region, built with the same generator and decision layer as the Iowa mock.

For each region in data/global/regions.json: a region file like config/region.json (its own box, its crop system),
the mock generator for fields, zones, series, weather and fires, then intelligence/run_all.py for status, plans,
route and analytics. Output: data/global/seasons/<id>/ with the files the site reads. Every value is made up for
the demo; the files say "source": "mock".

    python scripts/make_region_seasons.py [--only id ...]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGIONS = ROOT / "data/global/regions.json"
SEASONS = ROOT / "data/global/seasons"
BASE_CFG = json.loads((ROOT / "config/region.json").read_text(encoding="utf-8"))
GEN_SRC = (ROOT / "scripts/make_mock_data.py").read_text(encoding="utf-8")
KEEP = {"fields.geojson", "zones.geojson", "plans.json", "status.json", "route.json", "routing.json", "demand.json",
        "analytics.json", "timeseries.json", "weather.json", "fires.json", "deliveries.json", "insights.json",
        "yield_model.json", "field_env.json", "run.json", "season.json"}
CORN_SOY = ("corn", "maize", "soybean", "soy", "sorghum")


def region_config(r):
    cfg = json.loads(json.dumps(BASE_CFG))
    cfg["name"] = f"{r['name']}, {r['country']}"
    crops = " ".join(r.get("crops") or []).lower()
    cfg["crop_system"] = "corn_soy" if any(c in crops for c in CORN_SOY) else "rice_wheat"
    lat, lon = float(r["lat"]), float(r["lon"])
    # small-plot systems make many more fields per degree, so their box is smaller to keep the files light
    half = (0.035, 0.03) if cfg["crop_system"] == "corn_soy" else (0.014, 0.012)
    cfg["bbox"] = [round(lon - half[0], 4), round(lat - half[1], 4), round(lon + half[0], 4), round(lat + half[1], 4)]
    cfg["region_id"] = r["id"]
    return cfg


def generate(r, out):
    out.mkdir(parents=True, exist_ok=True)
    cfg_path = out / "season.json"
    cfg = region_config(r)
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    src = GEN_SRC.replace('OUT = ROOT / "data" / "mock"', 'OUT = Path(os.environ["MOCK_OUT"])') \
                 .replace('REGION = json.loads((ROOT / "config" / "region.json").read_text())',
                          'REGION = json.loads(Path(os.environ["MOCK_REGION"]).read_text())')
    assert 'os.environ["MOCK_OUT"]' in src and 'os.environ["MOCK_REGION"]' in src
    env = {**os.environ, "MOCK_OUT": str(out), "MOCK_REGION": str(cfg_path)}
    gen = out / "_gen.py"
    gen.write_text("import os\n" + src, encoding="utf-8")
    subprocess.run([sys.executable, str(gen)], check=True, env=env, cwd=str(ROOT), capture_output=True, text=True)
    gen.unlink()
    subprocess.run([sys.executable, str(ROOT / "intelligence/run_all.py"), "--data", str(out)], check=True,
                   cwd=str(ROOT), capture_output=True, text=True)
    for p in list(out.iterdir()):
        if p.is_dir():
            shutil.rmtree(p)
        elif p.name not in KEEP:
            p.unlink()
    plans = json.loads((out / "plans.json").read_text(encoding="utf-8"))
    return len(plans.get("plans", [])), sum(f.stat().st_size for f in out.iterdir())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args()
    doc = json.loads(REGIONS.read_text(encoding="utf-8"))
    index = {"note": "Stand-in seasons for the demo: fields, flags and checks are generated, not observed.", "seasons": {}}
    for r in doc["regions"]:
        if a.only and r["id"] not in a.only:
            continue
        if r["id"] == "us-ia-story":
            continue  # the real season covers Story County
        out = SEASONS / r["id"]
        try:
            n, size = generate(r, out)
            index["seasons"][r["id"]] = {"path": f"global/seasons/{r['id']}/", "farms": n, "name": r["name"], "country": r["country"]}
            print(f"  {r['id']:18s} {n:4d} farms  {size / 1e6:4.1f} MB", flush=True)
        except subprocess.CalledProcessError as e:
            print(f"  {r['id']:18s} FAILED: {(e.stderr or e.stdout or '')[-300:]}", flush=True)
    (SEASONS / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(index['seasons'])} seasons")


if __name__ == "__main__":
    main()
