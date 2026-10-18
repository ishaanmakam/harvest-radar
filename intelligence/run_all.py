"""Run the whole intelligence layer: status -> demand -> routing -> plans + route (+ evaluation when truth.json exists).

    python intelligence/run_all.py --data data/mock   # works right now on mock data
    python intelligence/run_all.py --data data/out    # once the pipeline has produced real data
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analytics  # noqa: E402
import decide  # noqa: E402
import demand  # noqa: E402
import evaluate  # noqa: E402
import harvest_model  # noqa: E402
import routing  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    data_dir = Path(ap.parse_args().data)
    print("[1/5] harvest model")
    harvest_model.run(data_dir)
    print("[2/5] demand forecast")
    demand.run(data_dir)
    print("[3/5] machine routing")
    routing.run(data_dir)
    print("[4/5] decision layer: one plan per farm + the officer's route")
    decide.run(data_dir)
    print("[5/5] analytics: yield, phenology, water, anomalies, economics, workability, events, coverage, landscape")
    analytics.run(data_dir)
    if (data_dir / "truth.json").exists():
        print("[eval] scoring the model against truth.json (mock only)")
        evaluate.run(data_dir)
