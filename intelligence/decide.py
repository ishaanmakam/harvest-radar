"""Decision layer: one morning plan per farm with all eight checks, plus the officer's route.
Spec: docs/superpowers/specs/2026-10-17-field-inspector-design.md ("What people receive", "Data").

Inputs   status.json (harvest model), timeseries.json, weather.json (forecast), fires.json, fields.geojson,
         insights.json (optional per-field Perceptron cues, see below)
Outputs  plans.json, route.json  (contracts/plans.schema.json, contracts/route.schema.json)
         Both are validated before anything is written; if either fails, the previous files stay in place.

    python intelligence/decide.py --data data/mock

Checks, in table order, and the rule each applies (numbers from the spec; index cues are ours):
  harvest_ready        watch    the harvest model calls the crop ready (harvest expected within ~3 days)
  harvest_before_rain  act_now  crop ready and any forecast day in the next 3 days has >= 15 mm
  establishment        watch    peak NDVI never reached crop level (or too few clear passes to tell)
  burn_scar            act_now  NBR scar first seen in the latest clear pass; watch for an older scar; clear if none
  fire                 act_now  a FIRMS hotspot inside the field in the last 5 days
  flood                act_now  Perceptron water cue dated the latest pass, else NDVI <= flood_ndvi_max in that pass
  stress               watch    Perceptron stress cue, else NDVI fell >= stress_ndvi_drop_min since the previous
                                pass while the crop is still standing (a harvest collapse is not stress)
  visible_change       watch    Perceptron change dated since the previous pass, else a harvest, a new scar or an
                                NDVI swing >= change_ndvi_min between the previous and the latest pass

Per-field Perceptron cues come from insights.json `field_cues` (the vision workstream's additive schema change):
  {field_id, kind: flood | stress | visible_change | burn_scar, observed: bool, date, where, detail, confidence}
Vision reports what it observed; the status rules stay here. A field with no cue uses the index fallback; a field
with any cue has source = "perceptron", also when observed is false (Perceptron looked and saw nothing).
"""
import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path

import weather_model as wm
from common import ROOT, d, load_region, read_json, s, write_json

KINDS = ["harvest_ready", "harvest_before_rain", "establishment", "burn_scar", "fire", "flood", "stress", "visible_change"]
ROUTE_ORDER = [("act_now", "fire"), ("act_now", "flood"), ("act_now", "harvest_before_rain"), ("act_now", "burn_scar"),
               ("watch", "stress"), ("watch", "visible_change"), ("watch", "harvest_ready"), ("watch", "establishment"),
               ("watch", "burn_scar")]
RANK = {"clear": 0, "watch": 1, "act_now": 2}
FLAG = {0: "fine", 1: "watch", 2: "act_now"}
DEFAULTS = {
    "rain_act_now_mm": 15.0,       # spec: harvest-before-rain threshold
    "rain_lookahead_days": 3,      # spec: "any forecast day in the next 3 days"
    "frost_tmin_c": 0.0,           # ours (Iowa): forecast Tmin at or below this within the same lookahead also triggers
    "fire_lookback_days": 5,       # spec: hotspot in the last 5 days
    "flood_ndvi_max": 0.0,         # ours: standing water reads as NDVI at or below zero
    "stress_ndvi_drop_min": 0.10,  # ours: drop between consecutive clear passes that is not a harvest (no zone context)
    "stress_zone_margin": 0.10,    # ours: with zone context, stress = this field's drop exceeds the zone's typical drop by this much
    "stress_max_gap_days": 12,     # ours: two clear passes further apart than this are not compared for stress
    "change_ndvi_min": 0.15,       # ours: swing between consecutive clear passes that counts as a visible change
    "peak_ndvi_min": 0.55, "burn_nbr_max": -0.05, "harvest_ndvi_max": 0.25,   # shared with harvest_model; region.json overrides
}
CONFIDENCE = {"l": "low", "n": "nominal", "h": "high"}
TODAY_TEXT = {
    ("act_now", "fire"): "Check the field for fire now. If it is burning, call emergency services first; the officer will follow up today.",
    ("act_now", "flood"): "Water is standing on the field: check the drainage today.",
    ("act_now", "burn_scar"): "The field went dark after harvest in the latest pass (a burn or a fresh tillage pass): the officer will visit today.",
    ("watch", "stress"): "Walk the part of the field that is browning faster than its neighbours and check for pests, lodging or water stress.",
    ("watch", "visible_change"): "Something changed since the last pass: confirm what happened in the field.",
    ("watch", "establishment"): "This field never greened up: check whether it was planted, or whether it is not a crop field at all.",
    ("watch", "burn_scar"): "An older dark scar (burn or tillage) is on record: no action today.",
}
QUIET = "Nothing needs action today. Harvest, rain, fire, flood and stress were checked and are fine."


def _check(kind, status, detail, where="", window=None):
    return {"kind": kind, "status": status, "detail": detail, "where": where, "window": window or {}}


def _win(a, b=None):
    return {"start": s(a), "end": s(b or a)}


def _span(a, b):
    return f"{s(a)} to {s(b)}"


def _cue_norm(cue):
    """A Perceptron cue with the fields decide relies on coerced: where/detail are strings ('' when null or not a
    string, the spec's 'empty when it does not apply'), date is YYYY-MM-DD (an ISO datetime is cut to its day)."""
    c = dict(cue or {})
    c["where"] = c["where"] if isinstance(c.get("where"), str) else ""
    c["detail"] = c["detail"] if isinstance(c.get("detail"), str) else ""
    dt = c.get("date")
    c["date"] = str(dt)[:10] if dt else None
    return c


def forecast_pairs(dates, precip, tmin=None):
    """(date, mm) per forecast day, or (date, mm, tmin_c) when the forecast carries minimum temperatures. Arrays
    shorter or longer than the dates are padded with None / cut, with a warning, instead of silently dropping days."""
    dates, precip = list(dates or []), list(precip or [])
    if len(precip) != len(dates):
        print(f"  WARN weather forecast has {len(dates)} dates but {len(precip)} precip values; padding with null")
        precip = (precip + [None] * len(dates))[:len(dates)]
    if tmin:
        tmin = list(tmin)
        if len(tmin) != len(dates):
            print(f"  WARN weather forecast has {len(dates)} dates but {len(tmin)} tmin values; padding with null")
            tmin = (tmin + [None] * len(dates))[:len(dates)]
        return [(d(x), mm, t) for x, mm, t in zip(dates, precip, tmin)]
    return [(d(x), mm) for x, mm in zip(dates, precip)]


def zone_declines(ts, zone_of, max_gap_days=12):
    """Per zone, the median NDVI drop between each field's last two clear passes (pairs further apart than
    max_gap_days are skipped). A whole zone drying down together (corn in September) then reads as normal, and a
    field falling much faster than its neighbours stands out."""
    import statistics
    dates = [d(x) for x in ts["dates"]]
    drops = {}
    for fid, series in ts["fields"].items():
        zone = zone_of.get(fid)
        if zone is None:
            continue
        clear = [(dates[i], v) for i, v in enumerate(series.get("ndvi", [])) if v is not None and i < len(dates)]
        if len(clear) < 2:
            continue
        (d1, v1), (d2, v2) = clear[-2], clear[-1]
        if (d2 - d1).days <= max_gap_days:
            drops.setdefault(zone, []).append(round(v1 - v2, 3))
    return {z: round(statistics.median(v), 3) for z, v in drops.items()}


def _cue_text(cue, default):
    """A Perceptron cue's own words (or a default), tagged with its confidence."""
    text = cue.get("detail") or default
    conf = cue.get("confidence")
    tag = f"Perceptron, {conf:.0%} confidence" if isinstance(conf, (int, float)) else "Perceptron"
    return f"{text} ({tag})"


def farm_plan(rec, passes, forecast, hotspots, insight, today: date, th=None, fallback_as_of=None, zone_drop=None,
              outlook=None):
    """One farm's plan. passes = [(date, ndvi, nbr)] clear passes; forecast = [(date, precip_mm)];
    hotspots = FIRMS detections in this field; insight = per-field Perceptron cues (kind -> cue) or None;
    fallback_as_of = the landscape's latest clear pass, used when this farm has no clear pass of its own;
    zone_drop = the zone's typical NDVI drop between the last two passes (see zone_declines), or None;
    outlook = weather_model.harvest_outlook(...) for a standing crop (combine window, rain, frost), or None."""
    th = {**DEFAULTS, **(th or {})}
    passes = sorted(p for p in passes if p[1] is not None)
    latest = passes[-1] if passes else None
    prev = passes[-2] if len(passes) > 1 else None
    as_of = latest[0] if latest else (fallback_as_of or today)
    insight = {k: _cue_norm(v) for k, v in (insight or {}).items()}
    perceptron = False
    stage = rec.get("stage", "unknown")
    standing = stage in ("growing", "maturing", "ready")
    ready = stage == "ready"
    harvest = d(rec["harvest_date"]) if rec.get("harvest_date") else None
    pred = None
    if rec.get("predicted_harvest_start") and rec.get("predicted_harvest_end"):
        pred = (d(rec["predicted_harvest_start"]), d(rec["predicted_harvest_end"]))
    checks = []

    # 1. harvest readiness (satellite says ready; the weather model adds the best combine window)
    outlook = outlook if (outlook and standing) else None
    if ready:
        detail = "Crop looks ready to harvest" + (f": expected {_span(*pred)}" if pred else "")
        win = _win(*pred) if pred else {}
        if outlook and outlook.get("window"):
            w = outlook["window"]
            win = {"start": w["start"], "end": w["end"]}
            detail += f"; best combine window {w['start']} to {w['end']}"
            if outlook.get("next_window"):
                detail += f", next {outlook['next_window']['start']} to {outlook['next_window']['end']}"
        elif outlook and outlook.get("summary"):
            detail += "; " + outlook["summary"].rstrip(".")
        checks.append(_check("harvest_ready", "watch", detail, window=win))
    elif harvest:
        checks.append(_check("harvest_ready", "clear", f"Looks harvested from the satellite: greenness collapsed around {s(harvest)}"))
    elif stage == "maturing" and pred:
        detail = f"Maturing: harvest expected {_span(*pred)}"
        if outlook and outlook.get("combine_ready"):
            detail += f"; combine-ready about {outlook['combine_ready']} by the weather"
        checks.append(_check("harvest_ready", "clear", detail))
    elif stage == "growing":
        checks.append(_check("harvest_ready", "clear", "Still greening up; no harvest expected within two weeks"))
    else:
        checks.append(_check("harvest_ready", "clear", "No harvest signal yet"))

    # 2. harvest versus weather: heavy rain (spec) or frost (Iowa addition) in the next 3 days on a ready crop
    horizon = [e for e in forecast if 0 <= (e[0] - today).days < th["rain_lookahead_days"]]
    rain = [(e[0], e[1]) for e in horizon if e[1] is not None and e[1] >= th["rain_act_now_mm"]]
    has_temps = any(len(e) > 2 for e in forecast)
    frost = [(e[0], e[2]) for e in horizon if len(e) > 2 and e[2] is not None and e[2] <= th["frost_tmin_c"]]
    if ready and (rain or frost):
        parts, days = [], []
        if rain:
            more = f" and more on {s(rain[-1][0])}" if len(rain) > 1 else ""
            parts.append(f"{rain[0][1]:.0f} mm of rain forecast on {s(rain[0][0])}{more}")
            days += [r[0] for r in rain]
        if frost:
            parts.append(f"frost forecast on {s(frost[0][0])} ({frost[0][1]:.0f} °C)")
            days += [f[0] for f in frost]
        what = "rain" if rain and not frost else ("frost" if frost and not rain else "rain and the frost")
        checks.append(_check("harvest_before_rain", "act_now", f"Harvest before the {what}: " + "; ".join(parts),
                             window=_win(min(days), max(days))))
        today_text = f"Harvest before {s(min(days))}: " + "; ".join(parts) + "."
    else:
        if ready:
            detail = (f"No rain of {th['rain_act_now_mm']:.0f} mm or more" + (" and no frost" if has_temps else "")
                      + f" in the next {th['rain_lookahead_days']} days")
        elif rain or frost:
            coming = ([f"{rain[0][1]:.0f} mm forecast on {s(rain[0][0])}"] if rain else []) + \
                     ([f"frost forecast on {s(frost[0][0])}"] if frost else [])
            detail = ", ".join(coming) + ", but the crop is not ready to cut"
        else:
            detail = "Crop not ready to cut; no heavy rain" + (" or frost" if has_temps else "") + " forecast"
        checks.append(_check("harvest_before_rain", "clear", detail))
        today_text = None

    # 3. establishment
    peak = rec.get("peak_ndvi")
    too_few = False
    if peak is None and passes:                      # the model gave up (too few passes) but the passes still show the crop
        peak = max(p[1] for p in passes)
    if peak is None:
        too_few = True
        checks.append(_check("establishment", "watch", "Too few clear passes to judge whether the crop established"))
    elif peak < th["peak_ndvi_min"]:
        checks.append(_check("establishment", "watch",
                             f"Never reached crop-level greenness (peak NDVI {peak:.2f}, crop level {th['peak_ndvi_min']:.2f})"))
    else:
        when = f" on {rec.get('peak_date')}" if rec.get("peak_date") else " in the clear passes"
        checks.append(_check("establishment", "clear", f"Crop established: peak NDVI {peak:.2f}{when}"))

    # 4. burn scar
    scar_date, scar_where, scar_note = None, "", ""
    if "scar" in (rec.get("burn_evidence") or ""):
        after = [p for p in passes if p[2] is not None and (harvest is None or p[0] >= harvest) and p[2] <= th["burn_nbr_max"]]
        scar_date = after[0][0] if after else (d(rec["burn_date"]) if rec.get("burn_date") else None)
    cue = insight.get("burn_scar")
    if cue is not None and cue.get("observed", True) and cue.get("date"):
        perceptron = True
        cue_date = d(cue["date"])
        scar_date = min(scar_date, cue_date) if scar_date else cue_date
        scar_where, scar_note = cue.get("where", ""), "; " + _cue_text(cue, "confirmed by Perceptron")
    if scar_date and (latest is None or scar_date >= latest[0]):     # on or after the last index pass = new
        checks.append(_check("burn_scar", "act_now",
                             f"Field went dark after harvest in the latest pass ({s(scar_date)}): burn or tillage scar{scar_note}",
                             where=scar_where, window=_win(scar_date)))
    elif scar_date:
        checks.append(_check("burn_scar", "watch", f"Dark scar (burn or tillage) on record since {s(scar_date)}{scar_note}",
                             where=scar_where, window=_win(scar_date)))
    else:
        checks.append(_check("burn_scar", "clear", "No burn or tillage scar"))

    # 5. fire
    recent = sorted((h for h in hotspots if 0 <= (today - d(h["date"])).days <= th["fire_lookback_days"]), key=lambda h: h["date"])
    if recent:
        h = recent[-1]
        conf = CONFIDENCE.get(h.get("confidence"), h.get("confidence") or "unknown")
        count = f"{len(recent)} hotspots, latest" if len(recent) > 1 else "Hotspot"
        detail = f"{count} on {h['date']} inside the field ({h.get('satellite', 'FIRMS')}, {conf} confidence)"
        checks.append(_check("fire", "act_now", detail, window=_win(d(recent[0]["date"]), d(h["date"]))))
    else:
        checks.append(_check("fire", "clear", f"No hotspot in the last {th['fire_lookback_days']} days"))

    # 6. flood
    cue = insight.get("flood")
    if cue is not None:
        perceptron = True
        current = latest is not None and (not cue.get("date") or d(cue["date"]) >= latest[0])
        if cue.get("observed", True) and current:
            checks.append(_check("flood", "act_now", _cue_text(cue, "Perceptron sees standing water in the latest pass"),
                                 where=cue.get("where", ""), window=_win(latest[0])))
        elif cue.get("observed", True):
            checks.append(_check("flood", "clear", f"Perceptron saw water on {cue.get('date')}, none in the latest pass"))
        else:
            checks.append(_check("flood", "clear", _cue_text(cue, "Perceptron saw no standing water in the latest pass")))
    elif latest and latest[1] <= th["flood_ndvi_max"]:
        checks.append(_check("flood", "act_now",
                             f"Standing-water signature in the latest pass (NDVI {latest[1]:.2f} on {s(latest[0])})",
                             window=_win(latest[0])))
    elif latest:
        checks.append(_check("flood", "clear", f"No standing water in the latest pass ({s(latest[0])})"))
    else:
        checks.append(_check("flood", "clear", "No clear pass to judge"))

    # 7. stress or browning
    cue = insight.get("stress")
    if cue is not None and standing:
        perceptron = True
        if cue.get("observed", True):
            checks.append(_check("stress", "watch", _cue_text(cue, "Perceptron sees part of the field declining"),
                                 where=cue.get("where", ""), window=_win(prev[0], latest[0]) if prev and latest else {}))
        else:
            checks.append(_check("stress", "clear", _cue_text(cue, "Perceptron sees no browning")))
    elif not standing:
        checks.append(_check("stress", "clear", "No standing crop to stress"))
    elif prev and latest:
        gap = (latest[0] - prev[0]).days
        drop = prev[1] - latest[1]
        between = f"between {s(prev[0])} and {s(latest[0])}"
        not_harvest = latest[1] > th["harvest_ndvi_max"]
        if gap > th["stress_max_gap_days"]:
            checks.append(_check("stress", "clear", f"Last two clear passes are {gap} days apart; too far apart to judge stress"))
        elif zone_drop is not None:
            if drop - zone_drop >= th["stress_zone_margin"] and not_harvest:
                checks.append(_check("stress", "watch",
                                     f"Greenness fell {drop:.2f} {between}, {drop - zone_drop:.2f} more than the zone's typical {zone_drop:.2f}",
                                     window=_win(prev[0], latest[0])))
            elif drop >= th["stress_ndvi_drop_min"]:
                checks.append(_check("stress", "clear", f"Declining in step with the zone (typical drop {zone_drop:.2f} {between})"))
            else:
                checks.append(_check("stress", "clear", f"Crop holding steady since {s(prev[0])}"))
        elif drop >= th["stress_ndvi_drop_min"] and not_harvest:
            checks.append(_check("stress", "watch",
                                 f"Greenness fell from {prev[1]:.2f} to {latest[1]:.2f} {between} without a harvest",
                                 window=_win(prev[0], latest[0])))
        else:
            checks.append(_check("stress", "clear", f"Crop holding steady since {s(prev[0])}"))
    else:
        checks.append(_check("stress", "clear", "Only one clear pass; nothing to compare"))

    # 8. visible change
    cue = insight.get("visible_change")
    if cue is not None and prev and latest:
        perceptron = True
        recent = not cue.get("date") or d(cue["date"]) > prev[0]
        if cue.get("observed", True) and recent:
            checks.append(_check("visible_change", "watch", _cue_text(cue, f"Perceptron dates a change to {cue.get('date')}"),
                                 where=cue.get("where", ""), window=_win(prev[0], latest[0])))
        else:
            checks.append(_check("visible_change", "clear", _cue_text(cue, f"Perceptron dates no change since {s(prev[0])}")))
    elif prev and latest:
        between = _span(prev[0], latest[0])
        if harvest and prev[0] < harvest <= latest[0]:
            checks.append(_check("visible_change", "watch", f"Field harvested between {s(prev[0])} and {s(latest[0])}",
                                 window=_win(prev[0], latest[0])))
        elif scar_date and scar_date >= latest[0]:
            checks.append(_check("visible_change", "watch", f"Field went dark (burn or tillage) between {s(prev[0])} and {s(latest[0])}",
                                 window=_win(prev[0], latest[0])))
        elif abs(latest[1] - prev[1]) >= th["change_ndvi_min"]:
            checks.append(_check("visible_change", "watch",
                                 f"Greenness moved from {prev[1]:.2f} to {latest[1]:.2f} ({between})",
                                 window=_win(prev[0], latest[0])))
        else:
            checks.append(_check("visible_change", "clear", f"No change since the previous pass ({s(prev[0])})"))
    else:
        checks.append(_check("visible_change", "clear", "Only one clear pass; nothing to compare"))

    by_kind = {c["kind"]: c for c in checks}
    worst = max(RANK[c["status"]] for c in checks)
    key = next(((st, k) for st, k in ROUTE_ORDER if by_kind[k]["status"] == st), None)
    if key is None:
        today_line = QUIET
    elif key == ("act_now", "harvest_before_rain"):
        today_line = today_text
    elif key == ("watch", "harvest_ready"):
        w = by_kind["harvest_ready"]["window"]
        when = f" for {w['start']} to {w['end']}" if w else ""
        today_line = f"Crop looks ready: line up the combine or your custom harvester{when}."
    elif key == ("watch", "establishment") and too_few:
        today_line = "Too few clear satellite passes to judge the crop yet: the officer will check by phone."
    else:
        today_line = TODAY_TEXT[key]
    plan = {
        "farm_id": rec["field_id"],
        "as_of": s(as_of),
        "flag": FLAG[worst],
        "source": "perceptron" if perceptron else "vegetation_index",
        "today": today_line,
        "checks": checks,
        "risks": [c["detail"] for c in checks if c["status"] != "clear"],
    }
    if outlook:
        plan["harvest_outlook"] = outlook          # additive: the weather side of the harvest decision
    return plan


def build_route(plans, today: date):
    """Stops in the spec's order; fine farms are not stops. act_now = visit, watch = call."""
    stops = []
    for plan in plans:
        by_kind = {c["kind"]: c for c in plan["checks"]}
        for i, (st, kind) in enumerate(ROUTE_ORDER):
            if by_kind[kind]["status"] == st:
                stops.append((i, plan["farm_id"], by_kind[kind]))
                break
    stops.sort(key=lambda t: (t[0], t[1]))
    return {"as_of": s(today),
            "stops": [{"farm_id": fid, "action": "visit" if c["status"] == "act_now" else "call", "why": c["detail"],
                       "kind": c["kind"], "status": c["status"]} for _, fid, c in stops]}


def _validate(name, doc):
    import json
    from jsonschema import Draft202012Validator
    schema = json.loads((ROOT / "contracts" / f"{name}.schema.json").read_text())
    return [f"{name}.json @ {'/'.join(str(x) for x in e.path) or '<root>'}: {e.message}"
            for e in Draft202012Validator(schema).iter_errors(doc)]


def run(data_dir: Path):
    region = load_region()
    th = dict(region.get("thresholds", {}))
    today = d(region["as_of"])
    status = read_json(data_dir, "status.json")
    ts = read_json(data_dir, "timeseries.json")
    fields = read_json(data_dir, "fields.geojson")
    weather = read_json(data_dir, "weather.json", default={"forecast": {"dates": [], "precip_mm": []}})
    fires = read_json(data_dir, "fires.json", default={"detections": []})
    insights = read_json(data_dir, "insights.json", default={"items": []})

    dates = [d(x) for x in ts["dates"]]
    fc = weather.get("forecast") or {}
    forecast = forecast_pairs(fc.get("dates", []), fc.get("precip_mm", []), fc.get("tmin_c"))
    # the landscape's latest clear pass: the as_of for a farm that has no clear pass of its own, and the document's
    clear_days = [i for i, _ in enumerate(dates)
                  if any(i < len(sr["ndvi"]) and sr["ndvi"][i] is not None for sr in ts["fields"].values())]
    latest_clear = dates[clear_days[-1]] if clear_days else today
    hotspots = defaultdict(list)
    for det in fires["detections"]:
        if det.get("field_id"):
            hotspots[det["field_id"]].append(det)
    cues = defaultdict(dict)                       # field_id -> kind -> cue (one per kind; the last listed wins)
    for c in insights.get("field_cues") or []:
        if c.get("field_id") and c.get("kind"):
            cues[c["field_id"]][c["kind"]] = c
    recs = {r["field_id"]: r for r in status["fields"]}
    zone_of = {f["properties"]["field_id"]: f["properties"]["zone_id"] for f in fields["features"]}
    thr = {**DEFAULTS, **th}
    drops = zone_declines(ts, zone_of, thr["stress_max_gap_days"])
    observed = weather.get("observed") or {}
    have_weather = bool(observed.get("dates")) and bool(fc.get("dates"))

    def outlook_for(rec, passes, crop):
        """Weather side for a standing crop: the satellite's predicted harvest start is the ready date when it
        exists, otherwise degree days from green-up decide."""
        if not have_weather or rec.get("stage") not in ("growing", "maturing", "ready"):
            return None
        emergence = next((dt for dt, v, _ in passes if v is not None and v >= 0.4), None)
        ready = d(rec["predicted_harvest_start"]) if rec.get("predicted_harvest_start") else None
        if ready is None and emergence is None:
            return None
        try:
            return wm.harvest_outlook(observed, fc, today, crop=crop, emergence=emergence, ready_override=ready)
        except Exception as e:  # a weather quirk must never cost the farm its plan
            print(f"  WARN outlook for {rec.get('field_id')}: {e}")
            return None

    plans = []
    for feat in fields["features"]:
        p = feat["properties"]
        fid = p["field_id"]
        rec = recs.get(fid) or {"field_id": fid, "zone_id": p["zone_id"], "stage": "unknown", "peak_ndvi": None}
        series = ts["fields"].get(fid, {"ndvi": [], "nbr": []})
        passes = [(dt, v, b) for dt, v, b in zip(dates, series["ndvi"], series["nbr"]) if v is not None]
        plan = farm_plan(rec, passes, forecast, hotspots.get(fid, []), cues.get(fid), today, th,
                         fallback_as_of=latest_clear, zone_drop=drops.get(p["zone_id"]),
                         outlook=outlook_for(rec, passes, p.get("crop") or "corn"))
        plan["zone_id"] = p["zone_id"]
        plans.append(plan)
    route = build_route(plans, today)
    plans_doc = {"as_of": s(latest_clear), "source": "intelligence/decide.py",
                 "vision_verdict": insights.get("verdict"), "plans": plans}

    errs = _validate("plans", plans_doc) + _validate("route", route)
    if errs:
        for e in errs[:10]:
            print("  INVALID", e)
        raise ValueError(f"decision run failed validation ({len(errs)} problems); previous plans.json / route.json kept")
    write_json(data_dir, "plans.json", plans_doc)
    write_json(data_dir, "route.json", route)
    flags = {f: sum(p["flag"] == f for p in plans) for f in ("act_now", "watch", "fine")}
    visits = sum(st["action"] == "visit" for st in route["stops"])
    print(f"  farms: {flags} | route: {len(route['stops'])} stops ({visits} visits, {len(route['stops']) - visits} calls)")
    return plans_doc, route


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    run(Path(ap.parse_args().data))
