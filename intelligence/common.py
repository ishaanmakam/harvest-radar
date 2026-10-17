"""Shared helpers for the intelligence layer. Owner: Person B."""
import json
import math
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_region():
    return json.loads((ROOT / "config" / "region.json").read_text())


def load_chcs():
    return json.loads((ROOT / "config" / "chcs.json").read_text())["chcs"]


def read_json(data_dir: Path, name: str, default=None):
    p = Path(data_dir) / name
    if not p.exists():
        if default is not None:
            return default
        raise FileNotFoundError(f"{p} not found. Run the pipeline (or scripts/make_mock_data.py) first.")
    return json.loads(p.read_text())


def write_json(data_dir: Path, name: str, obj):
    p = Path(data_dir) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, separators=(",", ":")))
    print(f"  wrote {p}")


def d(s: str) -> date:
    return date.fromisoformat(s)


def s(x: date) -> str:
    return x.isoformat()


def add_days(x: date, n: float) -> date:
    return x + timedelta(days=round(n))


def haversine_km(lon1, lat1, lon2, lat2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
