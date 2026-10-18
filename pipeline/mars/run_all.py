"""Run the Mars pipeline end to end: fetch, worlds, score, assets. Reruns reuse every cache and download nothing.

    python pipeline/mars/run_all.py [--skip-assets]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.mars import m01_fetch, m02_score, m03_assets, m04_worlds  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-assets", action="store_true", help="stop after sites.json")
    a = ap.parse_args()
    t0 = time.time()
    m01_fetch.main()
    m04_worlds.main()
    m02_score.main()
    if not a.skip_assets:
        m03_assets.main()
    print(f"Mars pipeline done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
