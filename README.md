# Harvest Radar

An autonomous field inspector for small family farms: the satellite season does the visiting, and every farmer and the extension officer get a morning plan by call and text.
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

| Who | Folder | Brief |
|---|---|---|
| Niranjan | `pipeline/` — season pipeline (satellite, rain, forecast, fires) | [A-pipeline.md](docs/tasks/A-pipeline.md) |
| Ishaan | `intelligence/` — decision layer, morning queue, Grok Voice + Photon | [B-intelligence.md](docs/tasks/B-intelligence.md) |
| Srikar | `vision/` + `web/` — Perceptron reader and the map | [C-vision.md](docs/tasks/C-vision.md), [D-web.md](docs/tasks/D-web.md) |

Product spec (source of truth): [docs/superpowers/specs/2026-10-17-field-inspector-design.md](docs/superpowers/specs/2026-10-17-field-inspector-design.md)

Branches: `niranjan-pipeline`, `b/intel`, `c/vision` → PR into `main` often. Coordinate in Slack `#bot-channel`.

## Licence

The code and documentation are released under the [Apache License 2.0](LICENSE); see [NOTICE](NOTICE). Data files keep the terms of the sources they come from, listed with each source in [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).
