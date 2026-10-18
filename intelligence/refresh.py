"""Season refresh, per the spec: "A new clear pass reruns the season and the decision, then replaces plans.json and
route.json only if both outputs validate. A send already in progress finishes on the files it started with."

    python intelligence/refresh.py --data data/out

Everything runs in <data>/.staging on copies of the inputs; status.json, plans.json and route.json are moved into
place (os.replace, one file at a time, each atomic) only after the whole chain succeeded and validated. On any
failure the previous files stay untouched and the staging folder is removed.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import analytics
import decide
import harvest_model
from common import ROOT, read_json

INPUTS = ("fields.geojson", "zones.geojson", "timeseries.json", "weather.json", "fires.json", "insights.json",
          "truth.json", "usda_crop_progress.json")
EXTRAS = ("analytics.json",)        # additive outputs: replaced when produced, never block the plans
OUTPUTS = ("status.json", "plans.json", "route.json")
SCHEMAS = {"status.json": "status.schema.json", "plans.json": "plans.schema.json", "route.json": "route.schema.json"}


def _validate(stage):
    from jsonschema import Draft202012Validator
    errs = []
    for name, schema_name in SCHEMAS.items():
        p = stage / name
        if not p.exists():
            errs.append(f"{name}: not produced")
            continue
        schema = json.loads((ROOT / "contracts" / schema_name).read_text())
        errs += [f"{name}: {e.message}" for e in Draft202012Validator(schema).iter_errors(json.loads(p.read_text()))][:5]
    return errs


def run(data_dir):
    data_dir = Path(data_dir)
    stage = data_dir / ".staging"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    try:
        for name in INPUTS:
            if (data_dir / name).exists():
                shutil.copy(data_dir / name, stage / name)
        print("  refresh: harvest model")
        harvest_model.run(stage)
        print("  refresh: decision layer")
        decide.run(stage)                       # validates plans + route itself and raises if they fail
        errs = _validate(stage)
        if errs:
            raise ValueError("; ".join(errs))
        try:
            print("  refresh: analytics")
            analytics.run(stage)
        except Exception as e:  # additive layer: a failure here never blocks the plans
            print(f"  refresh: analytics skipped ({e})")
        for name in OUTPUTS:
            os.replace(stage / name, data_dir / name)
        for name in EXTRAS:
            if (stage / name).exists():
                os.replace(stage / name, data_dir / name)
        plans = read_json(data_dir, "plans.json")
        result = {"replaced": True, "as_of": plans.get("as_of"), "plans": len(plans["plans"]), "error": None}
        print(f"  refresh: replaced {', '.join(OUTPUTS)} (plans from pass {result['as_of']}, {result['plans']} farms)")
    except Exception as e:
        print(f"  refresh FAILED: {e}. Previous status.json / plans.json / route.json kept.")
        result = {"replaced": False, "as_of": None, "plans": 0, "error": str(e)}
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    out = run(Path(ap.parse_args().data))
    sys.exit(0 if out["replaced"] else 1)
