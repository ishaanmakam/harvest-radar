"""Run the whole intelligence layer: status -> demand -> routing.

    python intelligence/run_all.py --data data/mock   # works right now on mock data
    python intelligence/run_all.py --data data/out    # once the pipeline has produced real data
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import demand  # noqa: E402
import harvest_model  # noqa: E402
import routing  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    data_dir = Path(ap.parse_args().data)
    print("[1/3] harvest model")
    harvest_model.run(data_dir)
    print("[2/3] demand forecast")
    demand.run(data_dir)
    print("[3/3] machine routing")
    routing.run(data_dir)
