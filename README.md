# Harvest Radar

Forecasting where crop-residue machines are needed next in Punjab, from orbit, so farmers don't have to burn stubble.
DubHacks 2026. Full context for humans and agents: [AGENTS.md](AGENTS.md).

## Quickstart (5 min)
```bash
git clone https://github.com/ishaanmakam/harvest-radar && cd harvest-radar
cp .env.example .env        # add keys
pip install -r intelligence/requirements.txt
python scripts/make_mock_data.py
python intelligence/run_all.py --data data/mock
python scripts/validate_contracts.py data/mock
```
Then open the repo in Cursor and point it at your task brief.

| Person | Folder | Brief |
|---|---|---|
| A | `pipeline/` — satellite data (needs Mac or Intel/AMD machine) | [A-pipeline.md](docs/tasks/A-pipeline.md) |
| B | `intelligence/` — models + optimization | [B-intelligence.md](docs/tasks/B-intelligence.md) |
| C | `vision/` — Perceptron | [C-vision.md](docs/tasks/C-vision.md) |
| All | `web/` — from ~10 PM | [D-web.md](docs/tasks/D-web.md) |

Branches: `a/pipeline`, `b/intel`, `c/vision` → PR into `main` often.
