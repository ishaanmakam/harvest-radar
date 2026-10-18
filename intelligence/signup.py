"""Self-serve sign-up: an address (or "lat, lon") and a phone or an email become a roster row tied to the field the
point falls in (or the nearest one within MAX_MATCH_KM), with the person's own morning time and timezone.

    python intelligence/signup.py --data data/mock --roster roster.csv --name "Ana Lopez" \
        --address "123 County Rd, Ames, IA" --phone +15155550101 --time 06:30 --timezone America/Chicago

The roster stays a CSV outside git (ROSTER_PATH). Beyond the six columns the queue needs, these are optional:
email, send_time (HH:MM local), timezone (IANA name), address, lon, lat, created. Geocoding uses OpenStreetMap
Nominatim (free; one request a second; a User-Agent is required); a "lat, lon" address skips it. Nothing is sent from
here: scheduler.py sends at the chosen time, and the welcome text is returned for the caller to deliver.
"""
import argparse
import csv
import json
import os
import re
import urllib.parse
from datetime import datetime, timezone as _utc
from pathlib import Path
from zoneinfo import ZoneInfo

from common import haversine_km, product_name
from providers import LANGUAGE_NAMES, _urllib_http
from scheduler import DEFAULT_SEND_TIME, DEFAULT_TIMEZONE
from tools import ROSTER_COLUMNS, normalize_phone

OPTIONAL_COLUMNS = ["email", "send_time", "timezone", "address", "lon", "lat", "created"]
MAX_MATCH_KM = 5.0                       # an address this far from every field is outside the landscape we watch
NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "Overfield-signup/1 (field inspector for small farms)"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
LATLON_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


# ---------------------------------------------------------------- where
def parse_latlon(text):
    """"42.03, -93.47" -> (lon, lat); anything else -> None."""
    m = LATLON_RE.match(text or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return (lon, lat)


def geocode(address, http=None):
    """(lon, lat, display_name) from Nominatim, or None when nothing was found or the service was unreachable."""
    http = http or _urllib_http
    url = NOMINATIM + "?" + urllib.parse.urlencode({"q": address, "format": "json", "limit": 1}, quote_via=urllib.parse.quote)
    try:
        status, body = http("GET", url, {"User-Agent": USER_AGENT, "Accept": "application/json"}, None, 10)
    except Exception:
        return None
    if status != 200:
        return None
    try:
        hits = json.loads(body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body)
    except (ValueError, AttributeError):
        return None
    if not hits:
        return None
    h = hits[0]
    try:
        return (float(h["lon"]), float(h["lat"]), str(h.get("display_name", "")))
    except (KeyError, TypeError, ValueError):
        return None


def match_field(lon, lat, fields):
    """The field whose polygon holds the point, else the nearest field by centroid within MAX_MATCH_KM, else None."""
    from shapely.geometry import Point, shape
    pt = Point(lon, lat)
    best = None
    for feat in (fields or {}).get("features", []):
        props = feat.get("properties") or {}
        try:
            geom = shape(feat["geometry"])
        except Exception:
            continue
        if geom.contains(pt) or geom.touches(pt):
            return {"field_id": props.get("field_id"), "zone_id": props.get("zone_id"), "distance_km": 0.0, "inside": True}
        c = geom.centroid
        dkm = haversine_km(lon, lat, c.x, c.y)
        if best is None or dkm < best["distance_km"]:
            best = {"field_id": props.get("field_id"), "zone_id": props.get("zone_id"), "distance_km": round(dkm, 2), "inside": False}
    return best if best and best["distance_km"] <= MAX_MATCH_KM else None


# ---------------------------------------------------------------- roster file
def _read_rows(path):
    path = Path(path)
    if not path.is_file():
        return [], list(ROSTER_COLUMNS)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = [{k: (v or "") for k, v in r.items()} for r in reader]
        cols = list(reader.fieldnames or ROSTER_COLUMNS)
    return rows, cols


def _write_rows(path, rows, cols):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: (r.get(c) or "") for c in cols})
    os.replace(tmp, path)


def next_roster_id(rows):
    nums = []
    for r in rows:
        m = re.match(r"^R(\d+)$", (r.get("roster_id") or "").strip())
        if m:
            nums.append(int(m.group(1)))
    return f"R{(max(nums) + 1) if nums else 1:03d}"


def same_phone(a, b):
    """Two phone strings for the same line: equal with country codes, else equal on the last ten digits."""
    a, b = normalize_phone(a or ""), normalize_phone(b or "")
    if not a or not b:
        return False
    if a.startswith("+") and b.startswith("+"):
        return a == b
    return a.lstrip("+")[-10:] == b.lstrip("+")[-10:]


def find_row(rows, phone=None, email=None):
    email = (email or "").strip().lower()
    for r in rows:
        if phone and same_phone(r.get("phone"), phone):
            return r
        if email and (r.get("email") or "").strip().lower() == email:
            return r
    return None


# ---------------------------------------------------------------- register / stop
def welcome_text(row, match):
    chans = []
    if row.get("phone"):
        chans.append("a call and a text")
    if row.get("email"):
        chans.append("an email")
    where = f"your field is {match['field_id']}"
    if not match.get("inside"):
        where += f" (the nearest field, about {match['distance_km']} km from that address)"
    return (f"Welcome to {product_name()}, {row['name']}. From the satellite, {where}. Every morning at {row['send_time']} "
            f"({row['timezone']}) you will get the plan for it by {' and '.join(chans)}. Text or email STOP at any time to stop.")


def register(fields, roster_path, *, name="", phone=None, email=None, address=None, lon=None, lat=None, language="en",
             send_time=None, timezone=None, role="farmer", http=None, now=None):
    """Validate, locate, match a field, then add or update the roster row. Raises ValueError with a sentence the
    person can read. Returns {"row", "created", "field", "welcome"}."""
    name = (name or "").strip()
    if not name:
        raise ValueError("A name is needed.")
    phone_n = normalize_phone(phone or "")
    email_n = (email or "").strip().lower()
    if not phone_n and not email_n:
        raise ValueError("A phone or an email is needed to send the morning plan.")
    if email_n and not EMAIL_RE.match(email_n):
        raise ValueError(f"That email does not look right: {email_n}")
    language = ((language or "en").strip().lower() or "en")[:2]
    if language not in LANGUAGE_NAMES:
        raise ValueError(f"language must be one of {', '.join(sorted(LANGUAGE_NAMES))}, got {language}")
    send_time = (send_time or "").strip() or DEFAULT_SEND_TIME
    m = TIME_RE.match(send_time)
    if not m:
        raise ValueError("send time must be HH:MM on the 24-hour clock, like 06:30")
    send_time = f"{int(m.group(1)):02d}:{m.group(2)}"
    timezone = (timezone or "").strip() or DEFAULT_TIMEZONE
    try:
        ZoneInfo(timezone)
    except Exception:
        raise ValueError(f"timezone must be a name like America/Chicago or Asia/Kolkata, got {timezone}")
    if role not in ("farmer", "officer"):
        raise ValueError("role must be farmer or officer")

    display = (address or "").strip()
    if lon is None or lat is None:
        ll = parse_latlon(display)
        if ll:
            lon, lat = ll
        elif display:
            g = geocode(display, http=http)
            if not g:
                raise ValueError(f"Could not find that address: {display}. Try adding the town and state, or give latitude, longitude.")
            lon, lat = g[0], g[1]
        else:
            raise ValueError("An address or a location (latitude, longitude) is needed.")
    try:
        lon, lat = float(lon), float(lat)
    except (TypeError, ValueError):
        raise ValueError("latitude and longitude must be numbers")
    match = match_field(lon, lat, fields)
    if not match:
        raise ValueError(f"{product_name()} does not cover that location yet: the nearest watched field is more than "
                         f"{MAX_MATCH_KM:.0f} km away.")

    rows, cols = _read_rows(roster_path)
    for c in OPTIONAL_COLUMNS:
        if c not in cols:
            cols.append(c)
    existing = find_row(rows, phone_n, email_n)
    row = existing if existing is not None else {"roster_id": next_roster_id(rows)}
    if existing is not None and phone_n and not phone_n.startswith("+") and (existing.get("phone") or "").startswith("+"):
        phone_n = existing["phone"]                       # keep the fuller number we already had
    row.update({"farm_id": match["field_id"], "name": name, "phone": phone_n, "language": language, "role": role,
                "email": email_n or (row.get("email") or ""), "send_time": send_time, "timezone": timezone,
                "address": display, "lon": f"{lon:.5f}", "lat": f"{lat:.5f}"})
    if existing is None:
        row["created"] = (now or datetime.now(_utc.utc)).strftime("%Y-%m-%dT%H:%MZ")
        rows.append(row)
    _write_rows(roster_path, rows, cols)
    return {"row": dict(row), "created": existing is None, "field": match, "welcome": welcome_text(row, match)}


def unsubscribe(roster_path, handle):
    """Remove every row that matches the phone or email; True when something was removed."""
    rows, cols = _read_rows(roster_path)
    handle = (handle or "").strip()
    is_email = "@" in handle
    keep = [r for r in rows if not ((is_email and (r.get("email") or "").strip().lower() == handle.lower())
                                    or (not is_email and same_phone(r.get("phone"), handle)))]
    if len(keep) == len(rows):
        return False
    _write_rows(roster_path, keep, cols)
    return True


def main():
    ap = argparse.ArgumentParser(description="Add a person to the roster for the morning plan.")
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--roster", default=os.environ.get("ROSTER_PATH"))
    ap.add_argument("--name", required=True)
    ap.add_argument("--address")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--phone")
    ap.add_argument("--email")
    ap.add_argument("--language", default="en")
    ap.add_argument("--time", default=None, help="HH:MM local, default 06:30")
    ap.add_argument("--timezone", default=None)
    a = ap.parse_args()
    if not a.roster:
        ap.error("--roster or ROSTER_PATH is needed")
    fields = json.loads((Path(a.data) / "fields.geojson").read_text(encoding="utf-8"))
    out = register(fields, a.roster, name=a.name, phone=a.phone, email=a.email, address=a.address, lon=a.lon, lat=a.lat,
                   language=a.language, send_time=a.time, timezone=a.timezone)
    print(("added" if out["created"] else "updated"), out["row"]["roster_id"], "->", out["row"]["farm_id"])
    print(out["welcome"])


if __name__ == "__main__":
    main()
