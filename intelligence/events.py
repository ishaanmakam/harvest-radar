"""What happened on each field beyond the eight morning checks: how badly it burned, how long it stood under water,
and how close the nearest fire is. Owner: Person B (intelligence/).

  burn   NBR after harvest (or after the peak when there is no harvest date) falling to burn_nbr_max or below, or a
         burn_date in status.json. dNBR = NBR on the last clear pass before the drop minus the lowest NBR from the
         drop on, read against the USGS severity classes; recovery = the NDVI rise since the event. When the harvest
         itself falls between the pre pass and the drop (pre_is_standing_crop), the class is an upper bound and the
         scar type is left open, because a tillage pass after a harvest in that gap reads like a burn.
  flood  consecutive clear passes with NDVI at or below flood_ndvi_max: how many, first and last, a days estimate,
         and whether the latest clear pass is still under water.
  fire   FIRMS hotspots of the last fire_lookback_days: distance from the field centroid, how many within
         fire_nearby_km, and whether one lies inside the field (by its field_id tag or by the polygon).

Module contract (shared by the analytics modules):
    analyze(data, config, prior=None) -> {"key", "fields", "landscape", "zones", "assumptions", "method"}
Nothing here raises on bad or missing input: the field gets None / 0 and landscape["notes"] says why. Every number
comes from the input files; no measurement is invented. prior (results of earlier modules) is accepted for the
contract and not needed here. Dates are ISO strings, floats are rounded, nothing numpy leaks out.
"""
import math
from collections import Counter
from datetime import date

import numpy as np

KEY = "events"

DEFAULTS = {
    "burn_nbr_max": -0.05,     # region.json thresholds (shared with harvest_model): NBR at or below this is a dark scar
    "flood_ndvi_max": 0.0,     # decide.py default: standing water reads as NDVI at or below zero (ASSUMPTION)
    "fire_lookback_days": 5,   # spec: a hotspot in the last 5 days
    "fire_nearby_km": 2.0,     # module brief: a hotspot closer than this to the field centroid is "nearby"
}
# dNBR burn severity, USGS FIREMON / Key and Benson 2006 (dNBR on a 0 to 1 scale): lower bound, class, scar type.
# 0.66 itself is still moderate_high; only above it is high. Below 0.10 is unburned: tillage or residue, not fire.
DNBR_CLASSES = [(0.44, "moderate_high", "burn_moderate"), (0.27, "moderate_low", "burn_moderate"), (0.10, "low", "burn_low")]
DNBR_HIGH_ABOVE = 0.66
HIGH = ("high", "burn_high")
UNBURNED = ("unburned", "tillage_or_residue")
SCAR_TYPES = ["tillage_or_residue", "burn_low", "burn_moderate", "burn_high"]
CLASS_NAMES = ["unburned", "low", "moderate_low", "moderate_high", "high", "unclassified"]
RECOVERY_NDVI_RISE_MIN = 0.05    # ASSUMPTION: an NDVI rise this large after the event is regreening, not index noise (about 0.02 to 0.03)
DEFAULT_PASS_INTERVAL_DAYS = 5   # Sentinel-2A/B revisit at this latitude; used only when no interval can be measured from the dates
EARTH_RADIUS_KM = 6371.0

EMPTY_BURN = {"burned": False, "event_date": None, "pre_date": None, "pre_nbr": None, "post_date": None, "post_nbr": None,
              "dnbr": None, "dnbr_class": None, "scar_type": None, "pre_is_standing_crop": False, "recovery": None,
              "evidence": None}
EMPTY_FLOOD = {"flood_passes": 0, "flood_first": None, "flood_last": None, "flood_days_estimate": None, "ongoing": False,
               "flood_episodes": 0}
EMPTY_FIRE = {"nearest_hotspot_km": None, "hotspots_within_2km": 0, "hotspots_inside": 0, "fire_exposure": "none",
              "latest_hotspot_date": None}


# ---------------------------------------------------------------- small helpers

def _num(x):
    """A finite float, or None (null, NaN, inf, text and numpy scalars all come out as plain values or None)."""
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _day(x):
    """A date from an ISO date or datetime string (cut to the day), else None."""
    if not x:
        return None
    try:
        return date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError):
        return None


def _iso(x):
    return x.isoformat() if isinstance(x, date) else None


def _r(x, nd=3):
    v = _num(x)
    return None if v is None else round(v, nd)


def haversine_km(lon1, lat1, lon2, lat2):
    """Great-circle distance in km. lon2 / lat2 may be numpy arrays; the result then is an array."""
    p1, p2 = math.radians(lat1), np.radians(np.asarray(lat2, dtype=float))
    dp = p2 - p1
    dl = np.radians(np.asarray(lon2, dtype=float) - lon1)
    a = np.sin(dp / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def classify_dnbr(dnbr):
    """(class, scar_type) for a dNBR value by the USGS thresholds; (None, None) when there is no value."""
    v = _num(dnbr)
    if v is None:
        return None, None
    if v > DNBR_HIGH_ABOVE:
        return HIGH
    for lower, cls, scar in DNBR_CLASSES:
        if v >= lower:
            return cls, scar
    return UNBURNED


def median_interval_days(dates):
    """Median gap in days between consecutive dates, or None with fewer than two."""
    if len(dates) < 2:
        return None
    ords = np.array(sorted(d.toordinal() for d in dates), dtype=float)
    return float(np.median(np.diff(ords)))


# ---------------------------------------------------------------- the three events for one field

def burn_event(passes, rec, nbr_max):
    """Burn or tillage scar for one field. passes = sorted [(date, ndvi, nbr)], None where cloudy; rec = the
    status.json record or None. The window opens at the harvest date, else the peak date, else the greenest pass.
    event = the first pass in the window with NBR <= nbr_max, else the status burn_date (hotspot-only burns);
    pre = NBR on the last clear pass before the event, post = the lowest NBR from the event on, dNBR = pre - post.

    status.json puts harvest_date halfway between the last standing-crop pass and the first harvested pass, so when
    pre_date < harvest_date <= event the harvest happened inside the drop: removing the crop is part of dNBR and a
    tillage pass in that gap reads like a burn. Such a field gets pre_is_standing_crop True, keeps dnbr_class as an
    upper bound and has no scar_type, unless even the upper bound is unburned, which settles it as tillage or residue.
    A status burn_date before the harvest (fire in the standing crop) is not flagged: there the crop is the real pre."""
    rec = rec or {}
    nbr_passes = [(dt, v, b) for dt, v, b in passes if b is not None]
    ndvi_passes = [(dt, v) for dt, v, _ in passes if v is not None]
    harvest = _day(rec.get("harvest_date"))
    start = harvest or _day(rec.get("peak_date"))
    if start is None and ndvi_passes:
        start = max(ndvi_passes, key=lambda p: p[1])[0]
    status_burn = _day(rec.get("burn_date"))
    scar = None
    if start is not None:
        scar = next((p for p in nbr_passes if p[0] >= start and p[2] <= nbr_max), None)
    if scar is None and status_burn is None:
        return dict(EMPTY_BURN)
    event = scar[0] if scar else status_burn
    before = [p for p in nbr_passes if p[0] < event]
    after = [p for p in nbr_passes if p[0] >= event]
    pre = before[-1] if before else None
    post = min(after, key=lambda p: p[2]) if after else None
    dnbr = round(pre[2] - post[2], 3) if (pre and post) else None
    cls, scar_type = classify_dnbr(dnbr)
    pre_is_crop = bool(harvest and pre and pre[0] < harvest <= event)
    if pre_is_crop and cls not in (None, UNBURNED[0]):
        scar_type = None
    return {
        "burned": True,
        "event_date": _iso(event),
        "pre_date": _iso(pre[0]) if pre else None, "pre_nbr": _r(pre[2]) if pre else None,
        "post_date": _iso(post[0]) if post else None, "post_nbr": _r(post[2]) if post else None,
        "dnbr": dnbr, "dnbr_class": cls, "scar_type": scar_type, "pre_is_standing_crop": pre_is_crop,
        "recovery": _recovery(ndvi_passes, event),
        "evidence": "nbr+status" if (scar and status_burn) else ("nbr" if scar else "status"),
    }


def _recovery(ndvi_passes, event):
    """Regreening after the event: the largest NDVI rise over the first clear pass on or after the event, and the
    pass that first showed it. None until the rise reaches RECOVERY_NDVI_RISE_MIN."""
    since = [(dt, v) for dt, v in ndvi_passes if dt >= event]
    if len(since) < 2:
        return None
    base = since[0][1]
    later = np.array([v for _, v in since[1:]], dtype=float) - base
    if later.max() < RECOVERY_NDVI_RISE_MIN:
        return None
    first = int(np.argmax(later >= RECOVERY_NDVI_RISE_MIN))
    return {"date": _iso(since[1 + first][0]), "ndvi_rise": round(float(later.max()), 3)}


def flood_event(passes, ndvi_max, fallback_interval=None):
    """Standing water for one field: runs of consecutive clear passes with NDVI <= ndvi_max (cloudy passes do not
    break a run). The latest run is reported; flood_episodes counts them all. The days estimate is the span of the
    run plus half the field's median pass interval (the landscape's interval when the field has only one pass)."""
    clear = [(dt, v) for dt, v, _ in passes if v is not None]
    runs, cur = [], []
    for dt, v in clear:
        if v <= ndvi_max:
            cur.append(dt)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    out = dict(EMPTY_FLOOD, flood_episodes=len(runs))
    if not runs:
        return out
    last = runs[-1]
    interval = median_interval_days([dt for dt, _ in clear])
    if interval is None:
        interval = fallback_interval if fallback_interval else DEFAULT_PASS_INTERVAL_DAYS
    out.update({
        "flood_passes": len(last),
        "flood_first": _iso(last[0]), "flood_last": _iso(last[-1]),
        "flood_days_estimate": round((last[-1] - last[0]).days + interval / 2, 1),
        "ongoing": clear[-1][0] == last[-1],
    })
    return out


def field_centroid(geometry):
    """(lon, lat, shapely polygon or None) for a GeoJSON geometry; (None, None, None) when it cannot be read."""
    if not isinstance(geometry, dict):
        return None, None, None
    try:
        from shapely.geometry import shape
        poly = shape(geometry)
        if not poly.is_empty:
            c = poly.centroid
            return float(c.x), float(c.y), poly
    except Exception:
        pass
    try:                                   # no shapely or an odd ring: the mean of the outer ring's vertices
        ring = np.asarray(geometry["coordinates"][0], dtype=float)
        if ring.ndim == 2 and len(ring) >= 3:
            return float(ring[:-1, 0].mean()), float(ring[:-1, 1].mean()), None
    except Exception:
        pass
    return None, None, None


def fire_proximity(fid, geometry, hot, nearby_km):
    """Fire exposure for one field from the recent hotspots (see recent_hotspots). inside = a detection tagged with
    this field_id or lying in the polygon; nearby = closer than nearby_km to the centroid; else none."""
    out = dict(EMPTY_FIRE)
    n = len(hot["lon"])
    if n == 0:
        return out
    inside = np.array([f == fid for f in hot["field_id"]], dtype=bool)
    lon, lat, poly = field_centroid(geometry)
    if poly is not None:
        try:
            import shapely
            inside |= np.asarray(shapely.intersects_xy(poly, hot["lon"], hot["lat"]), dtype=bool)
        except Exception:
            pass
    near = np.zeros(n, dtype=bool)
    if lon is not None:
        dist = haversine_km(lon, lat, hot["lon"], hot["lat"])
        near = dist < nearby_km
        out["nearest_hotspot_km"] = round(float(dist.min()), 2)
    close = near | inside
    out["hotspots_within_2km"] = int(close.sum())
    out["hotspots_inside"] = int(inside.sum())
    out["fire_exposure"] = "inside" if inside.any() else ("nearby" if close.any() else "none")
    if close.any():
        out["latest_hotspot_date"] = _iso(max(d for d, c in zip(hot["date"], close) if c))
    return out


def recent_hotspots(fires, today, lookback_days):
    """FIRMS detections dated within lookback_days before today (inclusive), as arrays ready for the distance math.
    Rows without a readable position or date are dropped and counted."""
    dets = fires.get("detections") if isinstance(fires, dict) else None
    dets = dets if isinstance(dets, list) else []
    lon, lat, fids, days, bad = [], [], [], [], 0
    for det in dets:
        if not isinstance(det, dict):
            bad += 1
            continue
        x, y, dt = _num(det.get("lon")), _num(det.get("lat")), _day(det.get("date"))
        if x is None or y is None or dt is None:
            bad += 1
            continue
        if today is None or not (0 <= (today - dt).days <= lookback_days):
            continue
        lon.append(x)
        lat.append(y)
        fids.append(det.get("field_id"))
        days.append(dt)
    return {"lon": np.array(lon, dtype=float), "lat": np.array(lat, dtype=float), "field_id": fids, "date": days,
            "total": len(dets), "bad": bad}


# ---------------------------------------------------------------- the module entry point

def _features(fields, notes):
    """[(field_id, zone_id, area_ha, geometry)] from fields.geojson; features without an id are dropped."""
    feats = fields.get("features") if isinstance(fields, dict) else None
    if not isinstance(feats, list):
        notes.append("fields.geojson missing or not a FeatureCollection: no fields analysed")
        return []
    out, skipped, no_area = [], 0, 0
    for f in feats:
        props = f.get("properties") if isinstance(f, dict) else None
        fid = (props or {}).get("field_id")
        if not fid:
            skipped += 1
            continue
        area = _num(props.get("area_ha"))
        if area is None:
            no_area += 1
            area = 0.0
        out.append((str(fid), props.get("zone_id"), area, f.get("geometry")))
    if skipped:
        notes.append(f"{skipped} features without field_id skipped")
    if no_area:
        notes.append(f"{no_area} fields without area_ha counted with 0 ha")
    return out


def _timeseries(ts, notes):
    """(dates, fields) from timeseries.json with unreadable dates dropped; ([], {}) when it is missing."""
    if not isinstance(ts, dict) or not isinstance(ts.get("dates"), list):
        notes.append("timeseries.json missing: no burn or flood events")
        return [], {}
    dates = [(i, _day(x)) for i, x in enumerate(ts["dates"])]
    good = [(i, d) for i, d in dates if d is not None]
    if len(good) < len(dates):
        notes.append(f"{len(dates) - len(good)} unreadable dates in timeseries.json skipped")
    fields = ts.get("fields") if isinstance(ts.get("fields"), dict) else {}
    return good, fields


def _passes(dates, series):
    """Sorted [(date, ndvi, nbr)] for one field; None where the pass was cloudy or the value unreadable."""
    if not isinstance(series, dict):
        return []
    ndvi, nbr = series.get("ndvi") or [], series.get("nbr") or []
    out = []
    for i, dt in dates:
        v = _num(ndvi[i]) if i < len(ndvi) else None
        b = _num(nbr[i]) if i < len(nbr) else None
        out.append((dt, v, b))
    return sorted(out, key=lambda p: p[0])


def _today(config, data, notes):
    """as_of from config, else status.json, else the last satellite pass. Never the clock."""
    t = _day((config or {}).get("as_of"))
    if t is None:
        st = (data or {}).get("status")
        t = _day(st.get("as_of")) if isinstance(st, dict) else None
        if t is not None:
            notes.append("as_of taken from status.json (config has none)")
    if t is None:
        ts = (data or {}).get("timeseries")
        last = [_day(x) for x in (ts.get("dates") if isinstance(ts, dict) else []) or []]
        last = [x for x in last if x]
        t = max(last) if last else None
        notes.append("as_of taken from the last satellite pass (no config or status as_of)" if t
                     else "no as_of anywhere: fire lookback cannot be applied, no hotspots counted")
    return t


def _thresholds(config):
    th = dict(DEFAULTS)
    for k, v in ((config or {}).get("thresholds") or {}).items():
        if k in th and _num(v) is not None:
            th[k] = _num(v)
    th["fire_lookback_days"] = int(th["fire_lookback_days"])
    return th


def _assumptions(th):
    return {
        "burn_nbr_max": f"{th['burn_nbr_max']}: NBR at or below this after harvest (or after the peak) is a dark scar; config/region.json thresholds, shared with harvest_model",
        "burn_window": "the scar is looked for from the status harvest_date, else the peak_date, else the greenest pass; a status burn_date (hotspot-only burn) counts even without an NBR drop",
        "dnbr_classes": "USGS FIREMON / Key and Benson 2006 on a 0 to 1 scale: below 0.10 unburned (tillage or residue), 0.10 to 0.27 low, 0.27 to 0.44 moderate_low, 0.44 to 0.66 moderate_high, above 0.66 high",
        "pre_fire_nbr": "the last clear pass before the drop (module brief); when that pass is still the standing crop (pre_date before the status harvest_date and the event at or after it) the harvest is part of the drop, so a tillage pass can read as a burn: dnbr_class is then an upper bound, pre_is_standing_crop is true and scar_type is left None unless even the upper bound is unburned",
        "recovery_ndvi_rise_min": f"{RECOVERY_NDVI_RISE_MIN}: ASSUMPTION, an NDVI rise this large after the event is regreening rather than index noise",
        "flood_ndvi_max": f"{th['flood_ndvi_max']}: standing water reads as NDVI at or below this; decide.py default, ASSUMPTION",
        "flood_days_estimate": "days from the first to the last flooded clear pass plus half the median pass interval (module brief); one flooded pass reads as half an interval",
        "pass_interval_default_days": f"{DEFAULT_PASS_INTERVAL_DAYS}: Sentinel-2A/B revisit at this latitude, used only when no interval can be measured from the dates",
        "fire_lookback_days": f"{th['fire_lookback_days']}: spec, a hotspot in the last 5 days",
        "fire_nearby_km": f"{th['fire_nearby_km']}: module brief, nearby = a hotspot closer than this to the field centroid (great-circle distance, Earth radius {EARTH_RADIUS_KM:.0f} km)",
    }


METHOD = ("After the eight morning checks, this module says what actually happened on a field. For a dark scar it takes "
          "the burn index (NBR) on the last clear pass before the field went dark and the lowest value after it; the "
          "difference, dNBR, is read against the USGS severity scale: a small drop reads as unburned, a larger one as a "
          "low, moderate or high burn, with the date the scar first showed and whether the field has greened back "
          "since. When the last clear pass before the drop is still the standing crop, the harvest is inside the drop "
          "and a tillage pass can read as a burn, so the field is flagged, its class is an upper bound and no scar "
          "type is claimed. For a flood it counts the run of clear passes with greenness at or below the water "
          "line, turns the span between the first and last flooded pass plus half a satellite revisit into a "
          "days-under-water estimate, and says whether the latest pass is still flooded. For fire it measures the "
          "distance from the field centre to every hotspot of the last five days, counts those within two kilometres, "
          "and calls the field inside when a hotspot falls within its boundary or carries its field id.")


def _skeleton(notes, th=None):
    th = th or dict(DEFAULTS)
    by_class = {c: {"fields": 0, "area_ha": 0.0} for c in CLASS_NAMES}
    return {"key": KEY, "fields": {}, "zones": {},
            "landscape": {"as_of": None, "fields": 0, "burn_nbr_max": th["burn_nbr_max"], "flood_ndvi_max": th["flood_ndvi_max"],
                          "fire_lookback_days": th["fire_lookback_days"], "fire_nearby_km": th["fire_nearby_km"],
                          "burned_fields": 0, "burned_area_ha": 0.0, "by_class": by_class,
                          "scar_types": {t: 0 for t in SCAR_TYPES}, "pre_standing_crop_fields": 0, "recovering_fields": 0,
                          "flooded_fields": 0, "flooded_area_ha": 0.0, "flood_ongoing_fields": 0, "flood_ongoing_area_ha": 0.0,
                          "fire_exposed_fields": {"inside": 0, "nearby": 0}, "fire_exposed_area_ha": 0.0,
                          "recent_hotspots": 0, "hotspots_total": 0, "notes": notes},
            "assumptions": _assumptions(th), "method": METHOD}


def _zone_row():
    return {"fields": 0, "burned": 0, "burned_area_ha": 0.0, "flooded": 0, "flood_ongoing": 0, "fire_inside": 0, "fire_nearby": 0}


def analyze(data, config, prior=None):
    """Burn severity, flood duration and fire proximity for every field in fields.geojson. See the module docstring
    for the contract; this never raises, it degrades to None / 0 with a note instead."""
    notes = []
    data = data if isinstance(data, dict) else {}
    th = _thresholds(config)
    out = _skeleton(notes, th)
    L = out["landscape"]
    try:
        today = _today(config, data, notes)
        L["as_of"] = _iso(today)
        feats = _features(data.get("fields"), notes)
        dates, series = _timeseries(data.get("timeseries"), notes)
        landscape_interval = median_interval_days([d for _, d in dates])
        status = data.get("status")
        recs = {}
        for r in (status.get("fields") if isinstance(status, dict) else None) or []:
            if isinstance(r, dict) and r.get("field_id"):
                recs[str(r["field_id"])] = r
        hot = recent_hotspots(data.get("fires"), today, th["fire_lookback_days"])
        L["recent_hotspots"], L["hotspots_total"] = len(hot["lon"]), hot["total"]
        if hot["bad"]:
            notes.append(f"{hot['bad']} fire detections without a readable position or date skipped")
        zones = out["zones"]
        zdoc = data.get("zones")
        for z in (zdoc.get("features") if isinstance(zdoc, dict) else None) or []:
            zid = (z.get("properties") or {}).get("zone_id") if isinstance(z, dict) else None
            if zid:
                zones[str(zid)] = _zone_row()
        missing_ts = sum(1 for fid, *_ in feats if fid not in series) if series else 0
        if series and missing_ts:
            notes.append(f"{missing_ts} fields absent from timeseries.json: no burn or flood events for them")
    except Exception as e:  # a broken input document costs the run its detail, never the caller its process
        notes.append(f"events not computed: {type(e).__name__}: {e}")
        return out

    classes, scar_types = Counter(), Counter()
    for fid, zid, area, geometry in feats:
        zid = str(zid) if zid else "unknown"
        zrow = zones.setdefault(zid, _zone_row())
        zrow["fields"] += 1
        passes = _passes(dates, series.get(fid))
        row = {"zone_id": zid, "clear_passes": sum(1 for _, v, _ in passes if v is not None), "burn": None, "flood": None, "fire": None}
        try:
            row["burn"] = burn_event(passes, recs.get(fid), th["burn_nbr_max"])
        except Exception as e:
            notes.append(f"{fid}: burn not computed ({type(e).__name__})")
        try:
            row["flood"] = flood_event(passes, th["flood_ndvi_max"], landscape_interval)
        except Exception as e:
            notes.append(f"{fid}: flood not computed ({type(e).__name__})")
        try:
            row["fire"] = fire_proximity(fid, geometry, hot, th["fire_nearby_km"])
        except Exception as e:
            notes.append(f"{fid}: fire proximity not computed ({type(e).__name__})")
        out["fields"][fid] = row

        b, fl, fr = row["burn"], row["flood"], row["fire"]
        if b and b["burned"]:
            cls = b["dnbr_class"] or "unclassified"
            classes[cls] += 1
            L["by_class"][cls]["area_ha"] += area
            if b["scar_type"]:
                scar_types[b["scar_type"]] += 1
            L["pre_standing_crop_fields"] += b["pre_is_standing_crop"]
            L["burned_fields"] += 1
            L["burned_area_ha"] += area
            L["recovering_fields"] += b["recovery"] is not None
            zrow["burned"] += 1
            zrow["burned_area_ha"] += area
        if fl and fl["flood_passes"]:
            L["flooded_fields"] += 1
            L["flooded_area_ha"] += area
            zrow["flooded"] += 1
            if fl["ongoing"]:
                L["flood_ongoing_fields"] += 1
                L["flood_ongoing_area_ha"] += area
                zrow["flood_ongoing"] += 1
        if fr and fr["fire_exposure"] != "none":
            L["fire_exposed_fields"][fr["fire_exposure"]] += 1
            L["fire_exposed_area_ha"] += area
            zrow["fire_inside" if fr["fire_exposure"] == "inside" else "fire_nearby"] += 1

    L["fields"] = len(out["fields"])
    for cls in CLASS_NAMES:
        L["by_class"][cls]["fields"] = classes.get(cls, 0)
        L["by_class"][cls]["area_ha"] = round(L["by_class"][cls]["area_ha"], 1)
    for t in SCAR_TYPES:
        L["scar_types"][t] = scar_types.get(t, 0)
    for k in ("burned_area_ha", "flooded_area_ha", "flood_ongoing_area_ha", "fire_exposed_area_ha"):
        L[k] = round(L[k], 1)
    for z in zones.values():
        z["burned_area_ha"] = round(z["burned_area_ha"], 1)
    if not feats and not notes:
        notes.append("no fields in fields.geojson")
    return out
