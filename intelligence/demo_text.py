"""Today's plan for one farm, texted to one phone now through Photon: the morning text, from the shell.

    python intelligence/demo_text.py --phone +15155550100 --farm F0061          # dry run: prints the text, sends nothing
    python intelligence/demo_text.py --phone +15155550100 --farm F0061 --send   # sends it

The same route the Account page uses (POST /demo/text) without an account: the plan file, the plan renderer and the
Photon client, nothing else. Keys come from .env (PHOTON_API_KEY, PHOTON_PROJECT_ID, PHOTON_FROM).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import load_env, read_json  # noqa: E402
from tools import normalize_phone  # noqa: E402
import providers  # noqa: E402
import queue_morning  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default="data/out", help="folder with plans.json (default data/out)")
    ap.add_argument("--phone", required=True, help="the phone to text, with the country code")
    ap.add_argument("--farm", required=True, help="farm id, e.g. F0061")
    ap.add_argument("--language", default="en")
    ap.add_argument("--send", action="store_true", help="send the text; without this flag it is only printed")
    a = ap.parse_args(argv)
    load_env()
    phone = normalize_phone(a.phone)
    if not phone:
        sys.exit(f"not a phone number: {a.phone}")
    plans = {p["farm_id"]: p for p in read_json(a.data, "plans.json")["plans"]}
    plan = plans.get(a.farm)
    if plan is None:
        sys.exit(f"no plan for farm {a.farm} in {a.data}; farms run {min(plans)} to {max(plans)}")
    text = queue_morning.render_plan(plan, a.language)
    print(text)
    print()
    if not a.send:
        print(f"dry run: nothing sent. Add --send to text {phone}.")
        return 0
    photon = providers.photon_from_env()
    if photon is None:
        sys.exit("Photon is not configured: PHOTON_API_KEY, PHOTON_PROJECT_ID and PHOTON_FROM in .env")
    result = photon.send(phone, text, a.language)
    print(f"sent to {phone}: {json.dumps(result)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
