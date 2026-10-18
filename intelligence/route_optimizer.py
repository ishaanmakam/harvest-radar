"""A drivable order for the officer's day, built on route.json without changing it.

route.json already says WHO is a stop and in what priority: act_now visits first (fire, flood, harvest_before_rain,
burn_scar), then watch calls. This module only decides the practical sequence:

  calls   phoned first from the office, CALL_MINUTES each, no travel
  visits  the listed tiers (kinds, in first-appearance order) are kept; inside one tier the farms are re-ordered by
          nearest neighbour from the previous stop, then improved with 2-opt on that tier's segment; a tier that
          would come out longer than it was listed keeps the listed order, and so does the whole day when the
          re-ordered tiers add up to more than driving route.json's own order (a tier can end far from the next one)
  km      listed_order_km is route.json's order driven from the start point; km_saved is measured against it and is
          never claimed on a longer drive
  clock   DAY_START, the calls, then SPEED_KMH between farm centroids (haversine) and VISIT_MINUTES on each farm

Start point: the first depot in config/chcs.json (simulated), else the bbox centre, else the first located visit.
Field centroids are the mean of the polygon's vertices (fine for the small, near-rectangular fields here).

    analyze(data, config, prior=None) -> {"key": "route", "fields", "landscape", "zones", "assumptions", "method"}

Pure: no clock, no randomness, no file access; bad or missing input degrades to empty lists and a note.
"""
import numpy as np

MODULE_KEY = "route"

CALL_MINUTES = 5.0          # ASSUMPTION: one phone call to a farmer about one check
VISIT_MINUTES = 20.0        # ASSUMPTION: time on the farm (walk to the field, talk to the farmer)
SPEED_KMH = 40.0            # ASSUMPTION: rural average on the county grid, incl. farm tracks and turning in
DAY_START_MIN = 8 * 60      # 08:00: the officer picks up the phone; the drive starts when the calls are done (ASSUMPTION)
DAY_END_MIN = 18 * 60       # ASSUMPTION: end of a field day; only used to count how many visits fit
EARTH_RADIUS_KM = 6371.0    # mean Earth radius, same as common.haversine_km
TWO_OPT_MAX_PASSES = 100    # safety cap; the mock's 46-stop tier converges in a handful of passes
KM_DIGITS = 3


# ----------------------------------------------------------------------------------------------------------------------
# geometry

def distance_matrix(lons, lats):
    """Pairwise great-circle distances in km (haversine) between points given as two equal-length sequences."""
    lon = np.radians(np.asarray(lons, dtype=float))
    lat = np.radians(np.asarray(lats, dtype=float))
    dlat = lat[:, None] - lat[None, :]
    dlon = lon[:, None] - lon[None, :]
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat[:, None]) * np.cos(lat[None, :]) * np.sin(dlon / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _ring_vertices(geom):
    """The outer-ring vertices of a Polygon (or every polygon of a MultiPolygon), closing vertex dropped."""
    if not isinstance(geom, dict):
        return []
    coords, kind = geom.get("coordinates"), geom.get("type")
    rings = []
    if kind == "Polygon" and coords:
        rings = [coords[0]]
    elif kind == "MultiPolygon" and coords:
        rings = [poly[0] for poly in coords if poly]
    elif kind == "Point" and coords:
        rings = [[coords]]
    pts = []
    for ring in rings:
        ring = [p for p in (ring or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
        if len(ring) > 1 and ring[0][0] == ring[-1][0] and ring[0][1] == ring[-1][1]:
            ring = ring[:-1]
        pts.extend(ring)
    return pts


def field_centroids(fields):
    """field_id -> (lon, lat) as the mean of the polygon vertices; fields with no usable geometry are left out."""
    out = {}
    for feat in (fields or {}).get("features") or []:
        props = feat.get("properties") or {}
        fid = props.get("field_id")
        if not fid:
            continue
        pts = _ring_vertices(feat.get("geometry"))
        if not pts:
            continue
        arr = np.asarray([[float(p[0]), float(p[1])] for p in pts], dtype=float)
        if not np.all(np.isfinite(arr)):
            continue
        lon, lat = arr.mean(axis=0)
        out[fid] = (float(lon), float(lat))
    return out


def field_zones(fields):
    return {(f.get("properties") or {}).get("field_id"): (f.get("properties") or {}).get("zone_id")
            for f in (fields or {}).get("features") or [] if (f.get("properties") or {}).get("field_id")}


def start_point(chcs, config, fallback=None):
    """(lon, lat, name): the first depot in config/chcs.json, else the bbox centre, else the fallback point."""
    depots = chcs.get("chcs") if isinstance(chcs, dict) else chcs
    for c in depots or []:
        if isinstance(c, dict) and c.get("lon") is not None and c.get("lat") is not None:
            return float(c["lon"]), float(c["lat"]), str(c.get("name") or c.get("chc_id") or "depot")
    bbox = (config or {}).get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        return (float(bbox[0]) + float(bbox[2])) / 2.0, (float(bbox[1]) + float(bbox[3])) / 2.0, "bbox centre"
    if fallback is not None:
        return float(fallback[0]), float(fallback[1]), "first visit (no depot, no bbox)"
    return None


# ----------------------------------------------------------------------------------------------------------------------
# ordering

def path_km(dist, start_idx, order):
    """Length of the open path start -> order[0] -> ... -> order[-1]."""
    total, here = 0.0, start_idx
    for j in order:
        total += float(dist[here, j])
        here = j
    return total


def nearest_neighbour(dist, start_idx, candidates):
    """Greedy: from the current point always drive to the nearest unvisited farm. Ties keep the listed order."""
    order, here, left = [], start_idx, list(candidates)
    while left:
        nxt = min(left, key=lambda j: dist[here, j])        # min is stable: first listed wins a tie
        order.append(nxt)
        left.remove(nxt)
        here = nxt
    return order


def two_opt(dist, start_idx, order, max_passes=TWO_OPT_MAX_PASSES):
    """Open-path 2-opt: reverse any segment that shortens the drive, until nothing does. The start is fixed and
    the path does not return to it (the officer ends the day at the last farm)."""
    path = list(order)
    n = len(path)
    passes, improved = 0, True
    while improved and passes < max_passes:
        improved, passes = False, passes + 1
        for i in range(n - 1):
            prev = start_idx if i == 0 else path[i - 1]
            for j in range(i + 1, n):
                a, b = path[i], path[j]
                nxt = path[j + 1] if j + 1 < n else None
                before = dist[prev, a] + (dist[b, nxt] if nxt is not None else 0.0)
                after = dist[prev, b] + (dist[a, nxt] if nxt is not None else 0.0)
                if after < before - 1e-9:
                    path[i:j + 1] = path[i:j + 1][::-1]
                    improved = True
    return path


def order_tier(dist, start_idx, listed):
    """One tier's located visits: nearest neighbour, then 2-opt; the listed order is kept when it is not longer."""
    if len(listed) < 2:
        return list(listed)
    best = two_opt(dist, start_idx, nearest_neighbour(dist, start_idx, listed))
    if path_km(dist, start_idx, best) > path_km(dist, start_idx, listed) - 1e-9:
        return list(listed)
    return best


def order_day(dist, start_idx, tiers_listed):
    """The whole drive: one sequence per tier, each tier ordered from where the previous one ends.

    Per-tier ordering cannot see where the next tier starts, so a tier can end on the far side of the box and hand the
    next tier a long first leg. The day is therefore checked as a whole: when the re-ordered tiers add up to more than
    driving the listed tiers in order, every tier keeps its listed order. Returns (planned, kept_listed)."""
    planned, here = [], start_idx
    for listed in tiers_listed:
        seq = order_tier(dist, here, list(listed)) if listed else []
        planned.append(seq)
        if seq:
            here = seq[-1]
    listed_day = [j for tier in tiers_listed for j in tier]
    if path_km(dist, start_idx, [j for seq in planned for j in seq]) > path_km(dist, start_idx, listed_day) + 1e-9:
        return [list(tier) for tier in tiers_listed], True
    return planned, False


# ----------------------------------------------------------------------------------------------------------------------
# clock

def clock(minutes):
    """Minutes after midnight -> 'HH:MM'; past midnight the day offset is appended ('00:05 +1d')."""
    m = int(round(float(minutes)))
    days, m = divmod(m, 24 * 60)
    h, mm = divmod(m, 60)
    text = f"{h:02d}:{mm:02d}"
    return text if days == 0 else f"{text} +{days}d"


def _km(x):
    return None if x is None else round(float(x), KM_DIGITS)


# ----------------------------------------------------------------------------------------------------------------------
# the module

def _empty(notes, start=None):
    return {"start": start, "calls": [], "visits": [], "total_km": 0.0, "listed_order_km": 0.0, "km_saved": 0.0,
            "total_hours": 0.0, "departure": clock(DAY_START_MIN), "finish_time": clock(DAY_START_MIN),
            "day_start": clock(DAY_START_MIN), "day_end": clock(DAY_END_MIN), "visits_by_day_end": 0,
            "n_calls": 0, "n_visits": 0, "unlocated_visits": [], "notes": list(notes)}


ASSUMPTIONS = {
    "speed_kmh": f"{SPEED_KMH:.0f} km/h door to door on the county road grid, farm tracks included (ASSUMPTION)",
    "visit_minutes": f"{VISIT_MINUTES:.0f} minutes on each farm visited (ASSUMPTION)",
    "call_minutes": f"{CALL_MINUTES:.0f} minutes per phone call, made from the office before leaving, no travel (ASSUMPTION)",
    "day_start": f"{clock(DAY_START_MIN)} the officer starts the calls; the drive begins when they are done (ASSUMPTION)",
    "day_end": f"{clock(DAY_END_MIN)} end of a field day, used only to say how many visits fit (ASSUMPTION)",
    "start_point": "first depot in config/chcs.json (simulated), else the centre of config/region.json bbox",
    "centroid": "mean of the field polygon's vertices; good to a few tens of metres on these small fields",
    "distance": "great-circle (haversine) km between centroids; road detours are absorbed by the slow average speed",
    "order": "route.json tiers kept (visits by kind in listed order, calls after); nearest neighbour then 2-opt inside a tier; "
             "the listed order is kept whenever the re-ordered day would be longer",
    "listed_order_km": "route.json's visit order driven from the start point; km_saved is measured against it and is never "
                       "reported on a longer drive",
}

METHOD = ("route.json already says who gets a visit and who gets a call, and in what priority. This module turns that "
          "into a day: the officer phones the watch farms first from the office, five minutes each, then drives. The "
          "visits keep their priority tiers (fire, flood, harvest before rain, burn scar) and inside each tier the farms "
          "are re-ordered to cut driving: nearest farm first, then any two legs that cross are untangled (2-opt). Legs "
          "are straight-line kilometres between field centres at a slow rural average speed, with twenty minutes on "
          "each farm, so every stop gets an arrival time and the day gets a finish time and the kilometres saved "
          "against driving the listed order from the depot. If the re-ordered day would come out longer, the listed "
          "order is kept and no saving is claimed.")


def _analyze(data, config, prior):
    notes = []
    data = data or {}
    config = config or {}
    route = data.get("route")
    stops = (route or {}).get("stops") if isinstance(route, dict) else None
    if not stops:
        notes.append("route.json has no stops" if isinstance(route, dict) else "route.json missing: nothing to order")
        start = start_point(data.get("chcs"), config)
        return {"key": MODULE_KEY, "fields": {}, "landscape": _empty(notes, start and {"lon": start[0], "lat": start[1], "name": start[2]}),
                "zones": {}, "assumptions": dict(ASSUMPTIONS), "method": METHOD}

    fields = data.get("fields") if isinstance(data.get("fields"), dict) else None
    centroids = field_centroids(fields)
    zone_of = field_zones(fields)
    if fields is None:
        notes.append("fields.geojson missing: visits cannot be placed on the map, so no driving distances")

    calls, visits, skipped = [], [], 0
    for st in stops:
        if not isinstance(st, dict) or not st.get("farm_id"):
            skipped += 1
            continue
        rec = {"farm_id": str(st["farm_id"]), "kind": st.get("kind") or "unknown", "status": st.get("status")}
        (visits if st.get("action") == "visit" else calls).append(rec)
    if skipped:
        notes.append(f"{skipped} route stop(s) without a farm_id were skipped")
    if any(st.get("action") not in ("visit", "call") for st in stops if isinstance(st, dict)):
        notes.append("route stops with an action other than visit/call were treated as calls")

    located = [v for v in visits if v["farm_id"] in centroids]
    unlocated = [v["farm_id"] for v in visits if v["farm_id"] not in centroids]
    if unlocated and fields is not None:
        notes.append("no field polygon for " + ", ".join(unlocated) + ": placed last in their tier with no travel")
    elif unlocated:
        notes.append("unlocated visits: " + ", ".join(unlocated))

    first_pt = centroids[located[0]["farm_id"]] if located else None
    start = start_point(data.get("chcs"), config, fallback=first_pt)
    if start is not None and start[2].startswith("first visit"):
        notes.append("no depot and no bbox in config: the day is measured from the first listed visit")

    # tiers in first-appearance order of the listed visits (route.json is already in spec order)
    tiers = []
    for v in visits:
        if v["kind"] not in tiers:
            tiers.append(v["kind"])

    # one distance matrix: index 0 = start, then the located visits in listed order
    idx_of = {}
    lons, lats = [start[0] if start else 0.0], [start[1] if start else 0.0]
    for v in located:
        idx_of[v["farm_id"]] = len(lons)
        lon, lat = centroids[v["farm_id"]]
        lons.append(lon)
        lats.append(lat)
    dist = distance_matrix(lons, lats) if located else None

    # the calls, from the office
    t = float(DAY_START_MIN)
    out_calls, fields_out = [], {}
    for i, c in enumerate(calls, 1):
        eta = clock(t)
        out_calls.append({"order": i, "farm_id": c["farm_id"], "kind": c["kind"], "eta": eta})
        fields_out[c["farm_id"]] = {"call_order": i, "eta": eta, "km_from_previous": None}
        t += CALL_MINUTES
    departure = t

    # the drive: decide the order first (whole day), then run the clock
    tiers_listed = [[idx_of[v["farm_id"]] for v in located if v["kind"] == kind] for kind in tiers]
    listed_km, planned = 0.0, [[] for _ in tiers]
    if dist is not None:
        # the yardstick is route.json's own order, driven from the start point (not tier by tier from wherever the
        # re-ordered previous tier happened to end)
        listed_km = path_km(dist, 0, [idx_of[v["farm_id"]] for v in located])
        planned, kept_listed = order_day(dist, 0, tiers_listed)
        if kept_listed:
            notes.append("re-ordering inside the tiers would make the day longer than route.json's order, "
                         "so the listed order is kept: no kilometres saved")

    out_visits, here, total_km = [], 0, 0.0
    order_n, fits = 0, 0
    for kind, seq in zip(tiers, planned):
        tier_unlocated = [v for v in visits if v["kind"] == kind and v["farm_id"] not in centroids]
        for j in seq:
            leg = float(dist[here, j])
            total_km += leg
            t += leg / SPEED_KMH * 60.0
            order_n += 1
            fid = located[j - 1]["farm_id"]
            eta = clock(t)
            out_visits.append({"order": order_n, "farm_id": fid, "kind": kind, "km_from_previous": _km(leg), "eta": eta,
                               "lon": round(lons[j], 6), "lat": round(lats[j], 6)})
            fields_out[fid] = {"visit_order": order_n, "eta": eta, "km_from_previous": _km(leg)}
            t += VISIT_MINUTES
            if t <= DAY_END_MIN + 1e-9:
                fits += 1
            here = j
        for v in tier_unlocated:
            order_n += 1
            eta = clock(t)
            out_visits.append({"order": order_n, "farm_id": v["farm_id"], "kind": kind, "km_from_previous": None, "eta": eta,
                               "lon": None, "lat": None})
            fields_out[v["farm_id"]] = {"visit_order": order_n, "eta": eta, "km_from_previous": None}
            t += VISIT_MINUTES
            if t <= DAY_END_MIN + 1e-9:
                fits += 1

    finish = t
    if total_km > listed_km + 1e-9:
        # only possible when route.json lists a kind in two separate runs, so the tier grouping itself is a detour
        notes.append(f"the tiers as grouped drive {total_km:.1f} km against {listed_km:.1f} km for route.json's own order: "
                     "no kilometres saved")
    if out_visits and finish > DAY_END_MIN + 1e-9:
        notes.append(f"{len(out_visits)} visits run to {clock(finish)}: {fits} of them are done by {clock(DAY_END_MIN)}; "
                     "the rest spill into tomorrow unless a second officer takes a tier")

    # zones: visits, calls and drive km per zone (km is charged to the zone of the farm the leg arrives at)
    zones_out = {}
    zone_ids = [(f.get("properties") or {}).get("zone_id") for f in ((data.get("zones") or {}).get("features") or [])
                if isinstance(f, dict)]
    for z in list(dict.fromkeys([z for z in zone_ids if z] + [z for z in zone_of.values() if z])):
        zones_out[z] = {"visits": 0, "calls": 0, "drive_km": 0.0, "first_eta": None}
    for v in out_visits:
        z = zone_of.get(v["farm_id"])
        if z in zones_out:
            zones_out[z]["visits"] += 1
            zones_out[z]["drive_km"] = round(zones_out[z]["drive_km"] + (v["km_from_previous"] or 0.0), KM_DIGITS)
            if zones_out[z]["first_eta"] is None:
                zones_out[z]["first_eta"] = v["eta"]
    for c in out_calls:
        z = zone_of.get(c["farm_id"])
        if z in zones_out:
            zones_out[z]["calls"] += 1

    landscape = {
        "as_of": (route or {}).get("as_of") or config.get("as_of"),
        "start": {"lon": round(start[0], 6), "lat": round(start[1], 6), "name": start[2]} if start else None,
        "calls": out_calls,
        "visits": out_visits,
        "total_km": _km(total_km),
        "listed_order_km": _km(listed_km),
        "km_saved": _km(max(0.0, listed_km - total_km)),
        "total_hours": round((finish - DAY_START_MIN) / 60.0, 2),
        "departure": clock(departure),
        "finish_time": clock(finish),
        "day_start": clock(DAY_START_MIN),
        "day_end": clock(DAY_END_MIN),
        "visits_by_day_end": int(fits),
        "n_calls": len(out_calls),
        "n_visits": len(out_visits),
        "unlocated_visits": unlocated,
        "notes": notes,
    }
    return {"key": MODULE_KEY, "fields": fields_out, "landscape": landscape, "zones": zones_out,
            "assumptions": dict(ASSUMPTIONS), "method": METHOD}


def analyze(data, config, prior=None):
    """The module contract. prior (results of earlier modules) is accepted and not needed here."""
    try:
        return _analyze(data, config, prior)
    except Exception as e:                      # never cost the morning its other sections
        note = f"route ordering failed ({type(e).__name__}: {e}); nothing ordered"
        return {"key": MODULE_KEY, "fields": {}, "landscape": _empty([note]), "zones": {},
                "assumptions": dict(ASSUMPTIONS), "method": METHOD}
