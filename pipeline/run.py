"""Live refresh: stage every pipeline output, validate, then swap it into data/out.

Fields and zones stay frozen. A failed step leaves data/out untouched.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker

from common import CACHE, ROOT

DATA_OUT = ROOT / "data" / "out"
STAGING = CACHE / "run_staging" / "out"
LOG_PATH = CACHE / "run_log.jsonl"
PYTHON = ROOT / ".venv" / "bin" / "python"
CLEAR_MIN = 0.5
PIPELINE_FILES = (
    "zones.geojson",
    "fields.geojson",
    "timeseries.json",
    "weather.json",
    "fires.json",
    "run.json",
)
STEPS = (
    ("s01", "s01_search.py"),
    ("s02", "s02_cube.py"),
    ("s04", "s04_timeseries.py"),
    ("s05", "s05_frames.py"),
    ("s06", "s06_weather.py"),
    ("s07", "s07_fires.py"),
)


def utc_stamp(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text):
    moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def say(msg):
    print(f"[run] {msg}", flush=True)


def append_log(record):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def fail(as_of, offline, step, durations, started_at):
    say(f"FAILED {step}")
    append_log({
        "as_of": as_of,
        "mode": "live",
        "offline": offline,
        "failed_step": step,
        "durations_s": durations,
        "generated_at": utc_stamp(datetime.now(timezone.utc)),
        "elapsed_s": round(time.perf_counter() - started_at, 3),
    })
    sys.exit(1)


def prepare_staging():
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DATA_OUT, STAGING)
    say(f"staging {STAGING}")


def child_env(as_of, out_dir=None):
    env = os.environ.copy()
    env["HR_AS_OF"] = as_of
    env["HR_OUT"] = str(out_dir or STAGING)
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("HR_FAIL_STEP", None)
    return env


def run_step(name, script, args, env):
    forced = os.environ.get("HR_FAIL_STEP", "").strip()
    if forced and forced in {name, script, Path(script).stem}:
        say(f"FAILED {name}: HR_FAIL_STEP={forced}, step not started")
        return 1, 0.0
    cmd = [str(PYTHON), str(ROOT / "pipeline" / script), *args]
    say(f"start {name}: {' '.join(cmd)}")
    started = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        text = line.rstrip("\n")
        if text.startswith(f"[{name}]"):
            print(text, flush=True)
        else:
            print(f"[{name}] {text}", flush=True)
    code = proc.wait()
    elapsed = time.perf_counter() - started
    if code == 0:
        say(f"{name} {elapsed:.1f}s")
    else:
        say(f"FAILED {name} exit {code} after {elapsed:.1f}s")
    return code, elapsed


def frame_source_dates():
    """Dates the current frames were already built from.

    frames.json only keeps passes clear enough to show. When s05_clear.json's
    clear passes are exactly those frame dates, the cloudy passes in that table
    were considered for these frames too and are not new work.
    """
    frames = json.loads((STAGING / "frames" / "frames.json").read_text())
    frame_dates = [frame["date"] for frame in frames["frames"]]
    accounted = set(frame_dates)
    clear_path = CACHE / "s05_clear.json"
    if not clear_path.exists():
        return accounted
    table = json.loads(clear_path.read_text())
    rendered = sorted(day for day, frac in table.items() if float(frac) >= CLEAR_MIN)
    if rendered == sorted(frame_dates):
        accounted.update(table)
    else:
        say("s05 clear table does not match the staging frames; only frame dates count as already built")
    return accounted


def missing_pass_dates(as_of):
    summary = json.loads((CACHE / "s01_summary.json").read_text())
    passes = [day for day in summary["dates"] if day <= as_of]
    have = frame_source_dates()
    return [day for day in passes if day not in have]


def summary_passes(as_of):
    summary = json.loads((CACHE / "s01_summary.json").read_text())
    passes = [day for day in summary["dates"] if day <= as_of]
    if not passes:
        return summary, None, 0
    return summary, passes[-1], len(passes)


def latest_overpass(pass_date):
    path = CACHE / "stac_items.json"
    if not path.exists():
        return None
    features = json.loads(path.read_text()).get("features") or []
    matches = []
    for feature in features:
        props = feature.get("properties") or {}
        raw = props.get("datetime")
        if not raw:
            continue
        when = parse_utc(raw)
        if when.date().isoformat() == pass_date:
            matches.append((when, props, feature.get("id")))
    if not matches:
        return None
    when, props, item_id = max(matches, key=lambda row: row[0])
    published = props.get("published", props.get("created"))
    field = "published" if props.get("published") else ("created" if props.get("created") else None)
    return when, published, field, item_id


def source_lines(summary, folder=None):
    folder = folder or STAGING
    weather = json.loads((folder / "weather.json").read_text())
    fires = json.loads((folder / "fires.json").read_text())
    observed = weather["observed"]["source"].split(";")[0].strip()
    forecast = weather["forecast"]["source"]
    return {
        "imagery": f"{summary['collection']} (Earth Search STAC)",
        "weather": f"{observed}; {forecast}",
        "fires": fires["source"],
    }


def validate_run(doc):
    schema = json.loads((ROOT / "contracts" / "run.schema.json").read_text())
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(doc),
        key=lambda err: list(err.path),
    )
    for err in errors:
        loc = "/".join(str(part) for part in err.path) or "<root>"
        say(f"run.json invalid @ {loc}: {err.message}")
    return not errors


def validate_folder(folder):
    cmd = [str(PYTHON), str(ROOT / "scripts" / "validate_contracts.py"), str(folder)]
    say(f"validate {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(f"[validate] {line.rstrip()}", flush=True)
    return proc.wait() == 0


def validate_staging():
    return validate_folder(STAGING)


def swap_files():
    for name in PIPELINE_FILES:
        src = STAGING / name
        dest = DATA_OUT / name
        if not src.exists():
            raise FileNotFoundError(src)
        os.replace(src, dest)
        say(f"swapped {name}")


def swap_frames():
    src = STAGING / "frames"
    dest = DATA_OUT / "frames"
    if not src.is_dir():
        raise FileNotFoundError(src)
    backup = CACHE / "_frames_backup"
    if backup.exists():
        shutil.rmtree(backup)
    moved = False
    if dest.exists():
        dest.rename(backup)
        moved = True
    try:
        src.rename(dest)
    except Exception:
        if moved and not dest.exists():
            backup.rename(dest)
        raise
    if backup.exists():
        shutil.rmtree(backup)
    say(f"replaced {dest}")


def replay_fail(as_of, step, durations, started_at):
    say(f"FAILED {step}")
    append_log({
        "as_of": as_of,
        "mode": "replay",
        "offline": False,
        "failed_step": step,
        "durations_s": durations,
        "generated_at": utc_stamp(datetime.now(timezone.utc)),
        "elapsed_s": round(time.perf_counter() - started_at, 3),
    })
    say("data/out was not written")
    sys.exit(1)


def copy_replay_frames(staging, as_of):
    src = DATA_OUT / "frames"
    dest = staging / "frames"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    doc = json.loads((src / "frames.json").read_text())
    kept = [frame for frame in doc["frames"] if frame["date"] <= as_of]
    if not kept:
        raise FileNotFoundError(f"no frames on or before {as_of}")
    for frame in kept:
        image = src / Path(frame["rgb"]).name
        if not image.exists():
            raise FileNotFoundError(image)
        shutil.copy2(image, dest / image.name)
    doc["frames"] = kept
    (dest / "frames.json").write_text(json.dumps(doc, separators=(",", ":")))
    say(f"frames {len(kept)} on or before {as_of}")


def hindsight_outcome(staging, as_of):
    from datetime import date, timedelta
    from s06_weather import archive_source

    weather = json.loads((DATA_OUT / "weather.json").read_text())
    observed = dict(zip(weather["observed"]["dates"], weather["observed"]["precip_mm"]))
    forecast = json.loads((staging / "weather.json").read_text())["forecast"]
    issued = dict(zip(forecast["dates"], forecast["precip_mm"]))
    day = date.fromisoformat(as_of)
    rain = []
    for offset in range(7):
        text = (day + timedelta(days=offset)).isoformat()
        rain.append({
            "date": text,
            "forecast_mm": issued.get(text),
            "actual_mm": observed.get(text),
        })
    summary = json.loads((CACHE / "s01_summary.json").read_text())
    later_pass = [text for text in summary["dates"] if text > as_of]
    later_clear = [text for text in summary.get("likely_clear_dates", []) if text > as_of]
    return {
        "note": f"HINDSIGHT: what actually happened after {as_of}; not used by any decision",
        "as_of": as_of,
        "forecast_issued": archive_source(day),
        "rain": rain,
        "next_pass": later_pass[0] if later_pass else None,
        "next_clear_pass": later_clear[0] if later_clear else None,
    }


def no_hindsight(staging, as_of):
    from s06_weather import archive_source
    from datetime import date, timedelta

    errors = []
    timeseries = json.loads((staging / "timeseries.json").read_text())
    late_series = [day for day in timeseries["dates"] if day > as_of]
    if late_series:
        errors.append(f"timeseries dates after as_of: {late_series[:3]}")
    frames = json.loads((staging / "frames" / "frames.json").read_text())
    late_frames = [frame["date"] for frame in frames["frames"] if frame["date"] > as_of]
    if late_frames:
        errors.append(f"frame dates after as_of: {late_frames[:3]}")
    fires = json.loads((staging / "fires.json").read_text())
    late_fires = [item["date"] for item in fires["detections"] if item["date"] > as_of]
    if late_fires:
        errors.append(f"fire dates after as_of: {late_fires[:3]}")
    weather = json.loads((staging / "weather.json").read_text())
    late_observed = [day for day in weather["observed"]["dates"] if day >= as_of]
    if late_observed:
        errors.append(f"observed weather on or after as_of: {late_observed[:3]}")
    forecast_dates = weather["forecast"]["dates"]
    if not forecast_dates or forecast_dates[0] != as_of:
        errors.append(f"forecast does not start at {as_of}")
    expected = archive_source(date.fromisoformat(as_of))
    if weather["forecast"]["source"] != expected:
        errors.append(f"forecast source is {weather['forecast']['source']!r}")
    cutoff = (date.fromisoformat(as_of) + timedelta(days=6)).isoformat()
    late_precip = [
        day for day, mm in zip(weather["forecast"]["dates"], weather["forecast"]["precip_mm"])
        if day > cutoff and mm is not None
    ]
    if late_precip:
        errors.append(f"forecast precip after as_of+6: {late_precip[:3]}")
    if errors:
        for error in errors:
            say(f"NO HINDSIGHT: FAIL {error}")
        return False
    say("NO HINDSIGHT: PASS")
    return True


def swap_replay(staging, as_of):
    dest = ROOT / "data" / "replay" / as_of
    dest.parent.mkdir(parents=True, exist_ok=True)
    backup = CACHE / "replay_backup"
    if backup.exists():
        shutil.rmtree(backup)
    moved = False
    if dest.exists():
        dest.rename(backup)
        moved = True
    try:
        staging.rename(dest)
    except Exception:
        if moved and not dest.exists():
            backup.rename(dest)
        raise
    if backup.exists():
        shutil.rmtree(backup)
    say(f"wrote {dest}")


def run_replay(as_of):
    from datetime import date

    try:
        day = date.fromisoformat(as_of)
    except ValueError:
        say(f"ERROR as_of {as_of} is not YYYY-MM-DD")
        sys.exit(1)
    today = datetime.now(ZoneInfo("America/Chicago")).date()
    if day >= today:
        say(f"replay as_of must be before today ({today.isoformat()}, America/Chicago)")
        sys.exit(1)
    as_of = day.isoformat()
    say(f"as_of {as_of} mode replay")
    started_at = time.perf_counter()
    staging = CACHE / "replay_staging" / as_of
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for name in ("zones.geojson", "fields.geojson"):
        shutil.copy2(DATA_OUT / name, staging / name)
    try:
        copy_replay_frames(staging, as_of)
    except FileNotFoundError as exc:
        say(f"ERROR {exc}")
        replay_fail(as_of, "frames", {}, started_at)
    env = child_env(as_of, staging)
    durations = {}
    for name, script, step_args in (
        ("s04", "s04_timeseries.py", []),
        ("s06", "s06_weather.py", ["--replay"]),
        ("s07", "s07_fires.py", ["--from-cache"]),
    ):
        code, elapsed = run_step(name, script, step_args, env)
        durations[name] = round(elapsed, 3)
        if code != 0:
            replay_fail(as_of, name, durations, started_at)
    durations["total"] = round(sum(durations.values()), 3)
    summary, latest, count = summary_passes(as_of)
    if latest is None:
        say("FAILED no pass date on or before as_of in s01_summary.json")
        replay_fail(as_of, "run.json", durations, started_at)
    completed = datetime.now(timezone.utc)
    doc = {
        "as_of": as_of,
        "mode": "replay",
        "generated_at": utc_stamp(completed),
        "latest_pass": latest,
        "pass_count": count,
        "offline": False,
        "durations_s": durations,
        "sources": source_lines(summary, staging),
    }
    if not validate_run(doc):
        replay_fail(as_of, "run.json", durations, started_at)
    (staging / "run.json").write_text(json.dumps(doc, separators=(",", ":")))
    outcome = hindsight_outcome(staging, as_of)
    (staging / "outcome.json").write_text(json.dumps(outcome, separators=(",", ":")))
    if not no_hindsight(staging, as_of):
        replay_fail(as_of, "hindsight", durations, started_at)
    if not validate_folder(staging):
        say("FAILED validation; data/out and data/replay left untouched")
        replay_fail(as_of, "validate", durations, started_at)
    swap_replay(staging, as_of)
    say(f"total {durations['total']:.1f}s")
    for name in ("s04", "s06", "s07"):
        say(f"{name} {durations[name]:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="Refresh pipeline outputs for today, or replay a past morning")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--as-of", dest="as_of", default=None)
    args = parser.parse_args()
    if not PYTHON.exists():
        say(f"ERROR missing {PYTHON}")
        sys.exit(1)
    if args.as_of:
        if args.offline:
            say("ERROR a replay does not take --offline")
            sys.exit(1)
        run_replay(args.as_of)
        return

    as_of = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    offline = args.offline
    say(f"as_of {as_of} (America/Chicago) mode live offline {offline}")
    started_at = time.perf_counter()
    prepare_staging()
    env = child_env(as_of)
    durations = {}

    plan = []
    if offline:
        say("skip s01: --offline")
    else:
        plan.append(("s01", "s01_search.py", []))
    plan.append(("s02", "s02_cube.py", []))
    plan.append(("s04", "s04_timeseries.py", []))

    # s05 is decided after s01, because the search is what finds a new pass.
    early = plan
    for name, script, step_args in early:
        code, elapsed = run_step(name, script, step_args, env)
        durations[name] = round(elapsed, 3)
        if code != 0:
            fail(as_of, offline, name, durations, started_at)

    if offline:
        say("skip s05: --offline (frames read imagery over the network)")
    else:
        missing = missing_pass_dates(as_of)
        if missing:
            say(f"s05: s01 pass dates not already in the staging frames' source dates: {', '.join(missing)}")
            code, elapsed = run_step("s05", "s05_frames.py", [], env)
            durations["s05"] = round(elapsed, 3)
            if code != 0:
                fail(as_of, offline, "s05", durations, started_at)
        else:
            say("skip s05: every s01 pass date is already in the staging frames' source dates")

    for name, script in (("s06", "s06_weather.py"), ("s07", "s07_fires.py")):
        step_args = ["--from-cache"] if offline else []
        code, elapsed = run_step(name, script, step_args, env)
        durations[name] = round(elapsed, 3)
        if code != 0:
            fail(as_of, offline, name, durations, started_at)

    completed = datetime.now(timezone.utc)
    durations["total"] = round(sum(value for key, value in durations.items() if key != "total"), 3)
    summary, latest, count = summary_passes(as_of)
    if latest is None:
        say("FAILED no pass date on or before as_of in s01_summary.json")
        fail(as_of, offline, "run.json", durations, started_at)

    doc = {
        "as_of": as_of,
        "mode": "live",
        "generated_at": utc_stamp(completed),
        "latest_pass": latest,
        "pass_count": count,
        "offline": offline,
        "durations_s": durations,
        "sources": source_lines(summary),
    }
    if not validate_run(doc):
        fail(as_of, offline, "run.json", durations, started_at)
    (STAGING / "run.json").write_text(json.dumps(doc, separators=(",", ":")))
    if not validate_staging():
        (STAGING / "run.json").unlink(missing_ok=True)
        say("FAILED validation; data/out left untouched")
        fail(as_of, offline, "validate", durations, started_at)

    swap_files()
    swap_frames()

    overpass = latest_overpass(latest)
    hours_since = None
    hours_pub = None
    pub_field = None
    item_id = None
    if overpass is None:
        say(f"latest pass {latest}; no STAC item datetime for that date")
    else:
        when, published, pub_field, item_id = overpass
        hours_since = (completed - when).total_seconds() / 3600
        say(f"latest pass {latest} item {item_id} acquired {utc_stamp(when)}")
        say(f"hours since overpass {hours_since:.2f}")
        if published and pub_field:
            hours_pub = (parse_utc(published) - when).total_seconds() / 3600
            say(f"hours from acquisition to {pub_field} {hours_pub:.2f}")
        else:
            say("item has no created or published property")

    for name, _script in STEPS:
        if name in durations and name != "total":
            say(f"{name} {durations[name]:.1f}s")
    say(f"total {durations['total']:.1f}s")
    record = {
        "as_of": as_of,
        "mode": "live",
        "offline": offline,
        "durations_s": durations,
        "total_s": durations["total"],
        "latest_pass": latest,
        "hours_since_overpass": None if hours_since is None else round(hours_since, 2),
        "item_id": item_id,
        "generated_at": doc["generated_at"],
    }
    if hours_pub is not None:
        record["hours_acquisition_to_publication"] = round(hours_pub, 2)
        record["publication_field"] = pub_field
    append_log(record)
    say(f"wrote {DATA_OUT / 'run.json'}")


if __name__ == "__main__":
    main()
