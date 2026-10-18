"""M4: the worlds list for the opening globe -> data/out/worlds.json.

Earth (live), Mars (open, with its sites file), three ocean moons (coming soon, descriptors only) and fifteen
exoplanets (coming soon) whose numbers come from the NASA Exoplanet Archive TAP service (table pscomppars). Names the
archive does not know are printed and dropped; nothing is typed in by hand. Cached in data/cache/mars/exoplanets_tap.json.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.mars import CACHE, CONTRACTS, ROOT, USER_AGENT, write_json_atomic  # noqa: E402

TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
PARSEC_LY = 3.26156
EXOPLANETS = ["Proxima Cen b", "Ross 128 b", "Teegarden's Star b", "Wolf 1069 b", "TRAPPIST-1 e", "TRAPPIST-1 f", "TRAPPIST-1 g",
              "LHS 1140 b", "TOI-700 d", "K2-18 b", "Kepler-186 f", "Kepler-442 b", "Kepler-452 b", "Kepler-62 f", "Kepler-1649 c"]
COLUMNS = "pl_name,hostname,sy_dist,pl_rade,pl_bmasse,pl_eqt,pl_insol,pl_orbper,st_spectype,disc_year,discoverymethod"

ORBITERS = [
    {"name": "Mars Global Surveyor", "years": "1997 to 2006", "instruments": "MOLA laser altimeter; TES thermal emission spectrometer",
     "used_for": "MOLA heights (low ground, slope, the globe and map relief); TES nighttime thermal inertia (plume-safe ground) and albedo (dust)"},
    {"name": "Mars Odyssey", "years": "since 2001", "instruments": "THEMIS; neutron spectrometer",
     "used_for": "THEMIS daytime infrared mosaics (site thumbnails); neutron hydrogen counts inside the SWIM ice maps"},
    {"name": "Mars Reconnaissance Orbiter", "years": "since 2006", "instruments": "HiRISE; CTX; SHARAD radar",
     "used_for": "HiRISE images of the six 2019 candidate sites; SHARAD radar and CTX landforms inside the SWIM ice maps"},
]

SOURCES = [
    {"name": "NASA Exoplanet Archive, Planetary Systems Composite Parameters (pscomppars)", "url": "https://exoplanetarchive.ipac.caltech.edu/",
     "citation": "NASA Exoplanet Archive, operated by Caltech under contract with NASA; TAP query on pscomppars",
     "terms": "Public; the archive asks users to acknowledge it (https://exoplanetarchive.ipac.caltech.edu/docs/acknowledge.html)"},
]


def fetch_exoplanets(http=None):
    cache = CACHE / "exoplanets_tap.json"
    if cache.exists():
        rows = json.loads(cache.read_text(encoding="utf-8"))
        fetched = datetime.fromtimestamp(cache.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
        print(f"  cached  {cache.name}: {len(rows)} rows")
    else:
        inlist = ",".join("'" + n.replace("'", "''").lower() + "'" for n in EXOPLANETS)
        query = f"select {COLUMNS} from pscomppars where lower(pl_name) in ({inlist})"
        url = TAP + "?" + urlencode({"query": query, "format": "json"})
        print(f"  fetch   {TAP} (pscomppars, {len(EXOPLANETS)} names)")
        import requests
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=180)
        r.raise_for_status()
        rows = r.json()
        CACHE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        fetched = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    found = {r["pl_name"].lower(): r for r in rows}
    missing = [n for n in EXOPLANETS if n.lower() not in found]
    if missing:
        print(f"  not in the archive, dropped: {missing}")
    return [found[n.lower()] for n in EXOPLANETS if n.lower() in found], fetched


def num(v, digits=3):
    return None if v is None else round(float(v), digits)


def exoplanet_world(row, order, fetched):
    dist_ly = num(row["sy_dist"] * PARSEC_LY, 1) if row.get("sy_dist") is not None else None
    facts = []
    if dist_ly is not None:
        facts.append(f"{dist_ly:g} light-years away")
    if row.get("pl_rade") is not None:
        facts.append(f"{num(row['pl_rade'], 2):g} times Earth's radius")
    if row.get("pl_eqt") is not None:
        facts.append(f"equilibrium temperature about {int(round(row['pl_eqt']))} K")
    if row.get("pl_orbper") is not None:
        facts.append(f"a year lasts {num(row['pl_orbper'], 1):g} days")
    if row.get("disc_year"):
        facts.append(f"found in {int(row['disc_year'])} by {str(row.get('discoverymethod') or 'an unstated method').lower()}")
    return {"id": row["pl_name"].lower().replace(" ", "-").replace("'", ""), "name": row["pl_name"], "kind": "exoplanet", "status": "coming_soon",
            "order": order, "parent": None, "host_star": row.get("hostname"), "spectral_type": row.get("st_spectype"),
            "distance_ly": dist_ly, "radius_earth": num(row.get("pl_rade"), 2), "mass_earth": num(row.get("pl_bmasse"), 2),
            "eq_temp_k": num(row.get("pl_eqt"), 0), "insolation_earth": num(row.get("pl_insol"), 2),
            "orbital_period_days": num(row.get("pl_orbper"), 2), "discovered": int(row["disc_year"]) if row.get("disc_year") else None,
            "method": row.get("discoverymethod"), "facts": facts,
            "tagline": f"{row['pl_name']} orbits {row.get('hostname') or 'its star'}" + (f", {dist_ly:g} light-years from here." if dist_ly else "."),
            "source": f"NASA Exoplanet Archive, pscomppars, fetched {fetched}", "sites": None, "illustration": True}


def build(rows, fetched, now=None):
    worlds = [
        {"id": "earth", "name": "Earth", "kind": "home", "status": "live", "order": 0, "parent": None, "host_star": "Sun", "spectral_type": "G2 V",
         "distance_ly": 0.0, "radius_earth": 1.0, "mass_earth": 1.0, "eq_temp_k": None, "insolation_earth": 1.0, "orbital_period_days": 365.25,
         "discovered": None, "method": None, "facts": ["Story County, Iowa: 136 real fields, a plan every morning"],
         "tagline": "The satellite visits every farm. Every farmer gets a morning plan.", "source": None, "sites": None, "illustration": False},
        {"id": "mars", "name": "Mars", "kind": "planet", "status": "open", "order": 1, "parent": None, "host_star": "Sun", "spectral_type": "G2 V",
         "distance_ly": None, "radius_earth": 0.532, "mass_earth": 0.107, "eq_temp_k": None, "insolation_earth": 0.43, "orbital_period_days": 687.0,
         "discovered": None, "method": None,
         "facts": ["A day lasts 24 hours 39 minutes", "Gravity is 38 percent of Earth's", "Surface pressure is under 1 percent of Earth's"],
         "tagline": "The next home. A city on Mars starts with one landing: near water, through enough air to slow down, on ground that holds.",
         "source": "NASA Mars facts", "sites": "out/mars/sites.json", "illustration": False},
        {"id": "europa", "name": "Europa", "kind": "ocean_moon", "status": "coming_soon", "order": 2, "parent": "Jupiter", "host_star": "Sun", "spectral_type": "G2 V",
         "distance_ly": None, "radius_earth": None, "mass_earth": None, "eq_temp_k": None, "insolation_earth": None, "orbital_period_days": None,
         "discovered": None, "method": None, "facts": ["An ice shell over a global ocean"], "tagline": "Jupiter's ice moon: a global ocean under the shell.",
         "source": None, "sites": None, "illustration": True},
        {"id": "enceladus", "name": "Enceladus", "kind": "ocean_moon", "status": "coming_soon", "order": 3, "parent": "Saturn", "host_star": "Sun", "spectral_type": "G2 V",
         "distance_ly": None, "radius_earth": None, "mass_earth": None, "eq_temp_k": None, "insolation_earth": None, "orbital_period_days": None,
         "discovered": None, "method": None, "facts": ["Water plumes from a subsurface ocean"], "tagline": "Saturn's small moon that sprays its ocean into space.",
         "source": None, "sites": None, "illustration": True},
        {"id": "titan", "name": "Titan", "kind": "ocean_moon", "status": "coming_soon", "order": 4, "parent": "Saturn", "host_star": "Sun", "spectral_type": "G2 V",
         "distance_ly": None, "radius_earth": None, "mass_earth": None, "eq_temp_k": None, "insolation_earth": None, "orbital_period_days": None,
         "discovered": None, "method": None, "facts": ["A thick atmosphere, methane lakes, a subsurface ocean"], "tagline": "Saturn's largest moon: thick air, methane lakes, an ocean below.",
         "source": None, "sites": None, "illustration": True},
    ]
    ordered = sorted(rows, key=lambda r: (r.get("sy_dist") is None, r.get("sy_dist") or 0))
    for i, row in enumerate(ordered):
        worlds.append(exoplanet_world(row, 5 + i, fetched))
    doc = {"generated_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": SOURCES, "orbiters": ORBITERS, "worlds": worlds}
    return doc


def main():
    print("M4 worlds")
    rows, fetched = fetch_exoplanets()
    doc = build(rows, fetched)
    out = ROOT / "data" / "out" / "worlds.json"
    write_json_atomic(out, doc, CONTRACTS / "worlds.schema.json")
    print(f"  wrote   {out.relative_to(ROOT)}: {len(doc['worlds'])} worlds ({len(rows)} exoplanets), validated")


if __name__ == "__main__":
    main()
