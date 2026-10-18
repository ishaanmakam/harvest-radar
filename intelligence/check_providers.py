"""Read-only preflight for the live loop: what is configured in .env, what the providers answer, what is missing.
It never places a call or sends a text.

    python intelligence/check_providers.py [--roster ROSTER.csv]

Twilio: lists the account's numbers (and whether TWILIO_FROM is one of them, and its voice webhook).
Photon: resolves our own line as a Photon User (create-or-get; no message is sent).
xAI, roster, PUBLIC_URL /health are checked too. Exit code 0 when a real send could go ahead.
"""
import argparse
import os
import sys

from common import load_env
from providers import PHOTON_VARS, VOICE_VARS, PhotonSMS, _basic, _ok, _payload, _urllib_http
from tools import load_roster


def report(env, roster_path=None, http=None):
    http = http or _urllib_http
    rep = {}

    missing = [v for v in VOICE_VARS if not env.get(v)]
    tw = {"configured": not missing, "missing": missing, "from": env.get("TWILIO_FROM"), "numbers": [],
          "from_is_owned": None, "error": None, "api_key_auth": bool(env.get("TWILIO_API_KEY_SID") and env.get("TWILIO_API_KEY_SECRET"))}
    if tw["configured"]:
        auth = (_basic(env["TWILIO_API_KEY_SID"], env["TWILIO_API_KEY_SECRET"]) if tw["api_key_auth"]
                else _basic(env["TWILIO_ACCOUNT_SID"], env["TWILIO_AUTH_TOKEN"]))
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{env['TWILIO_ACCOUNT_SID']}/IncomingPhoneNumbers.json?PageSize=20"
            status, raw = http("GET", url, {"Authorization": auth}, None, 20)
            payload = _payload(raw)
            _ok(status, payload, "twilio")
            tw["numbers"] = [{"phone_number": n.get("phone_number"), "voice_url": n.get("voice_url"),
                              "capabilities": n.get("capabilities")} for n in payload.get("incoming_phone_numbers", [])]
            tw["from_is_owned"] = any(n["phone_number"] == tw["from"] for n in tw["numbers"])
        except Exception as e:
            tw["error"] = str(e)
    rep["twilio"] = tw

    missing = [v for v in PHOTON_VARS if not env.get(v)]
    ph = {"configured": not missing, "missing": missing, "from": env.get("PHOTON_FROM"), "line_user_id": None,
          "line_kind": None, "error": None, "webhook_secret": bool(env.get("PHOTON_WEBHOOK_SECRET"))}
    if ph["configured"]:
        try:
            p = PhotonSMS(env["PHOTON_API_KEY"], env["PHOTON_PROJECT_ID"], env["PHOTON_FROM"], http=http)
            payload = p._call("POST", "/users", {"handle": p.from_number, "platform": "sms"})
            ph["line_user_id"], ph["line_kind"] = payload.get("id"), payload.get("kind")
        except Exception as e:
            ph["error"] = str(e)
    rep["photon"] = ph

    rep["xai"] = {"configured": bool(env.get("XAI_API_KEY")), "model": env.get("XAI_MODEL") or "grok-4.7"}

    try:
        rows = load_roster(roster_path or env.get("ROSTER_PATH"))
        rep["roster"] = {"ok": True, "rows": len(rows), "officers": sum(r["role"] == "officer" for r in rows),
                         "farmers": sum(r["role"] == "farmer" for r in rows), "languages": sorted({r["language"] for r in rows})}
    except Exception as e:
        rep["roster"] = {"ok": False, "error": str(e)}

    pu = env.get("PUBLIC_URL")
    rep["public_url"] = {"url": pu, "health": None, "error": None}
    if pu:
        try:
            status, raw = http("GET", pu.rstrip("/") + "/health", {}, None, 10)
            payload = _payload(raw)
            _ok(status, payload, "inbound service")
            rep["public_url"]["health"] = payload
        except Exception as e:
            rep["public_url"]["error"] = str(e)
    rep["public_ws_url"] = env.get("PUBLIC_WS_URL")

    voice_ok = tw["configured"] and not tw["error"]
    sms_ok = ph["configured"] and not ph["error"]
    rep["ready_for_real_send"] = bool(rep["roster"].get("ok") and (voice_ok or sms_ok))
    return rep


def render(rep):
    def mark(ok):
        return "[ok]" if ok else "[..]"
    tw, ph, xa, ro, pu = rep["twilio"], rep["photon"], rep["xai"], rep["roster"], rep["public_url"]
    lines = ["Live-loop preflight (nothing was sent)"]
    if tw["configured"]:
        if tw["error"]:
            lines.append(f"{mark(False)} Twilio: credentials rejected: {tw['error']}")
        else:
            nums = ", ".join(f"{n['phone_number']} (voice -> {n['voice_url'] or 'no webhook'})" for n in tw["numbers"]) or "no numbers on the account"
            lines.append(f"{mark(tw['from_is_owned'])} Twilio: numbers: {nums}; TWILIO_FROM {tw['from']} "
                         f"{'is one of them' if tw['from_is_owned'] else 'is NOT on this account'}")
    else:
        lines.append(f"{mark(False)} Twilio: set {', '.join(tw['missing'])} in .env")
    if ph["configured"]:
        lines.append(f"{mark(not ph['error'])} Photon: " + (f"error: {ph['error']}" if ph["error"] else
                     f"line {ph['from']} is user {ph['line_user_id']} (kind {ph['line_kind']}); webhook secret {'set' if ph['webhook_secret'] else 'not set'}"))
    else:
        lines.append(f"{mark(False)} Photon: set {', '.join(ph['missing'])} in .env")
    lines.append(f"{mark(xa['configured'])} xAI: {'XAI_API_KEY set' if xa['configured'] else 'XAI_API_KEY not set'} (model {xa['model']})")
    lines.append(f"{mark(ro['ok'])} roster: " + (f"{ro['rows']} rows ({ro['officers']} officer, {ro['farmers']} farmers; languages {', '.join(ro['languages'])})"
                                                 if ro["ok"] else ro["error"]))
    if pu["url"]:
        lines.append(f"{mark(pu['health'] is not None)} PUBLIC_URL {pu['url']}: " + (f"health {pu['health']}" if pu["health"] else f"not reachable: {pu['error']}"))
    else:
        lines.append(f"{mark(False)} PUBLIC_URL not set (needed for Twilio webhooks and Photon webhooks)")
    lines.append(f"{mark(bool(rep['public_ws_url']))} PUBLIC_WS_URL {'set' if rep['public_ws_url'] else 'not set'} (Grok bridge)")
    lines.append(("READY: a real send can go ahead (morning.py --real)" if rep["ready_for_real_send"]
                  else "NOT READY: fix the [..] lines above"))
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--roster", default=None)
    a = ap.parse_args()
    load_env()
    rep = report(os.environ, roster_path=a.roster)
    print(render(rep))
    sys.exit(0 if rep["ready_for_real_send"] else 1)
