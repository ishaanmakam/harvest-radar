"""Allocate residue machines from custom hiring centres (CHCs) to zones, and compare against
today's reactive first-come-first-served booking.

Inputs:  status.json, fields.geojson, zones.geojson, config/chcs.json
Output:  routing.json (contract: contracts/routing.schema.json)

Simulation (day by day, each machine serves one zone per day at `machine_rate_ha_per_day`):
  OPTIMIZED  (what we propose)
    - knows demand in advance from satellites: a field's job opens on its (predicted) harvest day
    - machines are pooled across all CHCs within `max_service_km`
    - zones are served earliest-sowing-deadline first
  BASELINE   (status quo: reactive booking apps / phone calls)
    - a job opens only after the farmer requests: harvest + `baseline_request_lag_days`
    - each zone is served only by its nearest CHC
    - first come, first served
A job whose sowing deadline passes before a machine arrives counts as `missed` = burn risk.
"""
import argparse
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from common import d, haversine_km, load_chcs, load_region, read_json, s, write_json
from demand import build_jobs


def simulate(jobs, chcs, zone_centroids, region, mode):
    as_of = d(region["as_of"])
    rate = region["machine_rate_ha_per_day"]
    max_km = region["max_service_km"]
    lag = region["baseline_request_lag_days"]
    horizon = region["forecast_horizon_days"]
    sim_days = horizon + region["sowing_window_days"] + 2

    dist = {(c["chc_id"], z): haversine_km(c["lon"], c["lat"], *zone_centroids[z]) for c in chcs for z in zone_centroids}
    nearest = {z: min(chcs, key=lambda c: dist[(c["chc_id"], z)])["chc_id"] for z in zone_centroids}

    pending = []  # [open_day, deadline, zone, remaining_ha, ha]
    for j in jobs:
        open_day = j["ready"] if mode == "optimized" else j["ready"] + timedelta(days=lag)
        pending.append([max(open_day, as_of), j["deadline"], j["zone_id"], j["ha"], j["ha"]])

    served, missed = 0.0, 0.0
    plan = defaultdict(lambda: [0, 0.0])  # (date, chc, zone) -> [machines, ha]
    for k in range(sim_days):
        day = as_of + timedelta(days=k)
        # deadlines that passed yesterday become missed
        for p in pending:
            if p[3] > 1e-9 and p[1] < day:
                missed += p[3]
                p[3] = 0.0
        free = {c["chc_id"]: c["machines"] for c in chcs}
        while True:
            open_jobs = [p for p in pending if p[3] > 1e-9 and p[0] <= day]
            if not open_jobs or not any(free.values()):
                break
            if mode == "optimized":
                open_jobs.sort(key=lambda p: (p[1], -p[3]))
            else:
                open_jobs.sort(key=lambda p: (p[0], p[1]))
            # pick the most urgent job that some CHC can still reach
            chosen_job, chosen_chc = None, None
            for p in open_jobs:
                zone = p[2]
                if mode == "optimized":
                    cands = [c for c in chcs if free[c["chc_id"]] > 0 and dist[(c["chc_id"], zone)] <= max_km]
                    cands.sort(key=lambda c: dist[(c["chc_id"], zone)])
                else:
                    cands = [c for c in chcs if c["chc_id"] == nearest[zone] and free[c["chc_id"]] > 0]
                if cands:
                    chosen_job, chosen_chc = p, cands[0]["chc_id"]
                    break
            if chosen_job is None:
                break
            free[chosen_chc] -= 1
            zone = chosen_job[2]
            # one machine-day in this zone: consume capacity across the zone's open jobs, most urgent first
            cap = rate
            for p in sorted([q for q in open_jobs if q[2] == zone], key=lambda q: q[1]):
                if cap <= 1e-9:
                    break
                take = min(cap, p[3])
                p[3] -= take
                cap -= take
                served += take
            key = (s(day), chosen_chc, zone)
            plan[key][0] += 1
            plan[key][1] += rate - cap
    still_open = sum(p[3] for p in pending)
    total = sum(p[4] for p in pending)
    return {
        "ha_served_in_window": round(served, 1),
        "ha_missed_window": round(missed + still_open, 1),
        "pct_served": round(100 * served / total, 1) if total else 0.0,
    }, plan, total


def run(data_dir: Path):
    region = load_region()
    chcs = load_chcs()
    status = read_json(data_dir, "status.json")
    fields = read_json(data_dir, "fields.geojson")
    zones = read_json(data_dir, "zones.geojson")
    zone_centroids = {z["properties"]["zone_id"]: tuple(z["properties"]["centroid"]) for z in zones["features"]}

    as_of = d(region["as_of"])
    last_ready = as_of + timedelta(days=region["forecast_horizon_days"] - 1)
    jobs = [j for j in build_jobs(status, fields, region) if j["ready"] <= last_ready]

    opt, plan, total = simulate(jobs, chcs, zone_centroids, region, "optimized")
    base, _, _ = simulate(jobs, chcs, zone_centroids, region, "baseline")
    out = {
        "as_of": s(as_of),
        "chcs": chcs,
        "plan": [{"date": k[0], "chc_id": k[1], "zone_id": k[2], "machines": v[0], "ha": round(v[1], 1)}
                 for k, v in sorted(plan.items())],
        "summary": {
            "ha_demand": round(total, 1),
            "optimized": opt,
            "baseline": base,
            "notes": "ha = machine-dependent area (field area x share_needing_chc). Baseline = reactive booking: "
                     "request after harvest + lag, nearest CHC only, first come first served. CHCs are simulated.",
        },
    }
    write_json(data_dir, "routing.json", out)
    print(f"  demand {total:.0f} ha | optimized served {opt['pct_served']}% (missed {opt['ha_missed_window']} ha)"
          f" | baseline served {base['pct_served']}% (missed {base['ha_missed_window']} ha)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    run(Path(ap.parse_args().data))
