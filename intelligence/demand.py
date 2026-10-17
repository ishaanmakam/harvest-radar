"""Forecast residue-machine demand per zone per day.

Inputs:  status.json, fields.geojson, zones.geojson
Output:  demand.json (contract: contracts/demand.schema.json)

A field "needs a machine" from its harvest date until its sowing deadline (harvest + window).
- backlog: already harvested, not burned, not sown, deadline not passed -> needs a machine now
- predicted: area spread evenly over its predicted harvest window
"""
import argparse
from datetime import timedelta
from pathlib import Path

from common import d, load_region, read_json, s, write_json


def build_jobs(status, fields, region):
    """Machine jobs used by both demand and routing. ha is machine-dependent area (area * share)."""
    as_of = d(region["as_of"])
    window = region["sowing_window_days"]
    share = region["share_needing_chc"]
    area = {f["properties"]["field_id"]: f["properties"]["area_ha"] for f in fields["features"]}
    jobs = []
    for r in status["fields"]:
        ha = area.get(r["field_id"], 0) * share
        if ha <= 0:
            continue
        if r["stage"] == "harvested" and r["harvest_date"]:
            h = d(r["harvest_date"])
            deadline = h + timedelta(days=window)
            if deadline >= as_of:
                jobs.append({"field_id": r["field_id"], "zone_id": r["zone_id"], "ha": ha,
                             "ready": h, "deadline": deadline, "kind": "backlog"})
        elif r["predicted_harvest_start"]:
            a, b = d(r["predicted_harvest_start"]), d(r["predicted_harvest_end"])
            ready = a + (b - a) / 2
            jobs.append({"field_id": r["field_id"], "zone_id": r["zone_id"], "ha": ha, "ready": ready,
                         "deadline": ready + timedelta(days=window), "kind": "predicted",
                         "win": (a, b)})
    return jobs


def run(data_dir: Path):
    region = load_region()
    as_of = d(region["as_of"])
    H = region["forecast_horizon_days"]
    rate = region["machine_rate_ha_per_day"]
    share = region["share_needing_chc"]
    status = read_json(data_dir, "status.json")
    fields = read_json(data_dir, "fields.geojson")
    zones = read_json(data_dir, "zones.geojson")
    area = {f["properties"]["field_id"]: f["properties"]["area_ha"] for f in fields["features"]}
    days = [as_of + timedelta(days=k) for k in range(H)]

    out_zones = []
    for z in zones["features"]:
        zid, name = z["properties"]["zone_id"], z["properties"]["name"]
        daily = {x: 0.0 for x in days}
        backlog = 0.0
        for r in status["fields"]:
            if r["zone_id"] != zid:
                continue
            a = area.get(r["field_id"], 0)
            if r["stage"] == "harvested" and r["sowing_deadline"] and d(r["sowing_deadline"]) >= as_of:
                backlog += a
            elif r["predicted_harvest_start"]:
                ws, we = d(r["predicted_harvest_start"]), d(r["predicted_harvest_end"])
                span = [ws + timedelta(days=k) for k in range((we - ws).days + 1)]
                for x in span:
                    if x in daily:
                        daily[x] += a / len(span)
        nxt7 = backlog + sum(daily[x] for x in days[:7])
        out_zones.append({
            "zone_id": zid, "name": name, "backlog_ha": round(backlog, 1),
            "daily": [{"date": s(x), "ha_ready": round(daily[x], 1)} for x in days],
            "ha_next_7d": round(nxt7, 1),
            "machine_days_next_7d": round(nxt7 * share / rate, 1),
        })
    out_zones.sort(key=lambda z: -z["machine_days_next_7d"])
    out = {"as_of": s(as_of), "horizon_days": H, "machine_rate_ha_per_day": rate,
           "share_needing_chc": share, "zones": out_zones}
    write_json(data_dir, "demand.json", out)
    top = out_zones[0]
    print(f"  hottest zone: {top['name']} needs {top['machine_days_next_7d']} machine-days in 7d")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    run(Path(ap.parse_args().data))
