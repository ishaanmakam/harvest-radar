#!/usr/bin/env python3
"""Validate every data file in a data dir against contracts/*.schema.json plus cross-file checks.

Usage:
    python scripts/validate_contracts.py data/mock
    python scripts/validate_contracts.py data/out --strict   # fail if any file is missing

Run this before every push. CI runs it on every PR.
"""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"

FILES = {
    "fields.geojson": "fields.schema.json",
    "zones.geojson": "zones.schema.json",
    "timeseries.json": "timeseries.schema.json",
    "weather.json": "weather.schema.json",
    "fires.json": "fires.schema.json",
    "frames/frames.json": "frames.schema.json",
    "status.json": "status.schema.json",
    "demand.json": "demand.schema.json",
    "routing.json": "routing.schema.json",
    "insights.json": "insights.schema.json",
    "plans.json": "plans.schema.json",
    "route.json": "route.schema.json",
    "deliveries.json": "deliveries.schema.json",
}

# Written by the morning queue on its first run, so never required, even with --strict
OPTIONAL = {"deliveries.json"}

CHECK_KINDS = ["harvest_ready", "harvest_before_rain", "establishment", "burn_scar", "fire", "flood", "stress", "visible_change"]
STATUS_RANK = {"clear": 0, "watch": 1, "act_now": 2}


def load(p):
    with open(p) as f:
        return json.load(f)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    strict = "--strict" in sys.argv
    data_dir = Path(args[0]) if args else ROOT / "data" / "mock"
    errors, loaded = [], {}

    for rel, schema_name in FILES.items():
        path = data_dir / rel
        if not path.exists():
            msg = f"missing {rel}"
            (errors.append(msg) if strict and rel not in OPTIONAL else print(f"  WARN {msg}"))
            continue
        try:
            doc = load(path)
        except json.JSONDecodeError as e:
            errors.append(f"{rel}: invalid JSON ({e})")
            continue
        schema = load(CONTRACTS / schema_name)
        errs = sorted(Draft202012Validator(schema).iter_errors(doc), key=lambda e: list(e.path))
        for e in errs[:10]:
            loc = "/".join(str(x) for x in e.path) or "<root>"
            errors.append(f"{rel} @ {loc}: {e.message}")
        if len(errs) > 10:
            errors.append(f"{rel}: ... {len(errs) - 10} more schema errors")
        loaded[rel] = doc
        print(f"  {'OK ' if not errs else 'BAD'} {rel}")

    # ---- cross-file checks ----
    fields = loaded.get("fields.geojson")
    field_ids = {f["properties"]["field_id"] for f in fields["features"]} if fields else set()
    zones = loaded.get("zones.geojson")
    zone_ids = {f["properties"]["zone_id"] for f in zones["features"]} if zones else set()

    if fields and zones:
        bad = {f["properties"]["zone_id"] for f in fields["features"]} - zone_ids
        if bad:
            errors.append(f"fields reference unknown zones: {sorted(bad)[:5]}")

    ts = loaded.get("timeseries.json")
    if ts:
        n = len(ts["dates"])
        if ts["dates"] != sorted(ts["dates"]):
            errors.append("timeseries dates are not sorted")
        for fid, s in ts["fields"].items():
            for k in ("ndvi", "nbr", "clear_frac"):
                if len(s[k]) != n:
                    errors.append(f"timeseries {fid}.{k} has {len(s[k])} values, expected {n}")
                    break
        if field_ids:
            missing = field_ids - set(ts["fields"])
            if missing:
                errors.append(f"{len(missing)} fields have no timeseries (e.g. {sorted(missing)[:3]})")

    st = loaded.get("status.json")
    if st and field_ids:
        unknown = {r["field_id"] for r in st["fields"]} - field_ids
        if unknown:
            errors.append(f"status references unknown fields: {sorted(unknown)[:5]}")

    fires = loaded.get("fires.json")
    if fires and field_ids:
        unknown = {d["field_id"] for d in fires["detections"] if d["field_id"]} - field_ids
        if unknown:
            errors.append(f"fires reference unknown fields: {sorted(unknown)[:5]}")

    frames = loaded.get("frames/frames.json")
    if frames:
        for fr in frames["frames"]:
            for key in ("rgb", "ndvi"):
                if fr.get(key) and not (data_dir / fr[key]).exists():
                    errors.append(f"frame image missing: {fr[key]}")

    plans = loaded.get("plans.json")
    if plans:
        for p in plans["plans"]:
            if [c["kind"] for c in p["checks"]] != CHECK_KINDS:
                errors.append(f"plan {p['farm_id']}: checks must list the 8 kinds in table order")
                break
            worst = max(STATUS_RANK[c["status"]] for c in p["checks"])
            if p["flag"] != {0: "fine", 1: "watch", 2: "act_now"}[worst]:
                errors.append(f"plan {p['farm_id']}: flag {p['flag']} is not the worst check status")
                break
        if field_ids:
            unknown = {p["farm_id"] for p in plans["plans"]} - field_ids
            if unknown:
                errors.append(f"plans reference unknown farms: {sorted(unknown)[:5]}")

    route = loaded.get("route.json")
    if route and plans:
        plan_ids = {p["farm_id"] for p in plans["plans"]}
        stop_ids = [s["farm_id"] for s in route["stops"]]
        if len(stop_ids) != len(set(stop_ids)):
            errors.append("route lists a farm more than once")
        unknown = set(stop_ids) - plan_ids
        if unknown:
            errors.append(f"route stops without a plan: {sorted(unknown)[:5]}")

    if errors:
        print(f"\nFAILED ({len(errors)} problems) in {data_dir}:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print(f"\nAll contracts valid in {data_dir}")


if __name__ == "__main__":
    main()
