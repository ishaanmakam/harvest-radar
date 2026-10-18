"""Next-door browning comparison. Surname only; never a phone number."""
import json
import re
from datetime import date
from pathlib import Path

GAP_DEG = 0.0005          # ~40 m at 42N; mock fields sit 0.0002° apart
MARGIN = 0.05             # this field must drop this much more than the neighbour
MAX_GAP_DAYS = 12


def surname(name):
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", name or "")
    skip = {"farmer", "officer", "name"}
    tokens = [t for t in tokens if t.lower() not in skip]
    return tokens[-1] if tokens else ""


def _bbox(feat):
    ring = feat["geometry"]["coordinates"][0]
    xs, ys = [c[0] for c in ring], [c[1] for c in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _adjacent(a, b, gap=GAP_DEG):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return not (ax2 + gap < bx1 or bx2 + gap < ax1 or ay2 + gap < by1 or by2 + gap < ay1)


def _drop(series, dates):
    clear = [(dates[i], v) for i, v in enumerate(series or []) if v is not None and i < len(dates)]
    if len(clear) < 2:
        return None
    (d1, v1), (d2, v2) = clear[-2], clear[-1]
    if (date.fromisoformat(str(d2)[:10]) - date.fromisoformat(str(d1)[:10])).days > MAX_GAP_DAYS:
        return None
    return v1 - v2


def _farmer_name(farm_id, roster):
    for row in roster or []:
        if row.get("role") == "farmer" and row.get("farm_id") == farm_id:
            return row.get("name") or ""
    return ""


def sentence(farm_id, data_dir, roster):
    """'Your field is browning faster than Singh's next door.' or None. Never a phone."""
    root = Path(data_dir)
    fields_path, ts_path = root / "fields.geojson", root / "timeseries.json"
    if not fields_path.is_file() or not ts_path.is_file() or not farm_id:
        return None
    try:
        fields = json.loads(fields_path.read_text(encoding="utf-8"))
        ts = json.loads(ts_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    by_id = {f["properties"]["field_id"]: f for f in fields.get("features") or [] if f.get("properties", {}).get("field_id")}
    mine = by_id.get(farm_id)
    if mine is None:
        return None
    dates = [str(x)[:10] for x in ts.get("dates") or []]
    mine_drop = _drop((ts.get("fields") or {}).get(farm_id, {}).get("ndvi"), dates)
    if mine_drop is None:
        return None
    my_box = _bbox(mine)
    best = None
    for fid, feat in by_id.items():
        if fid == farm_id or not _adjacent(my_box, _bbox(feat)):
            continue
        other = _drop((ts.get("fields") or {}).get(fid, {}).get("ndvi"), dates)
        if other is None:
            continue
        if mine_drop - other >= MARGIN and (best is None or mine_drop - other > best[0]):
            best = (mine_drop - other, fid)
    if best is None:
        return None
    who = surname(_farmer_name(best[1], roster))
    label = f"{who}'s" if who else "the field"
    return f"Your field is browning faster than {label} next door."
