"""Inbound handlers: a farmer or the officer calls (Grok Voice) or texts (Photon iMessage) and asks about today.
Spec: "Inbound. Caller ID or the iMessage handle maps to one roster row." Farmer voice reads only that farm; officer
voice and iMessage read the route and any farm; an unknown number is told no farm is registered and nothing else.

Two entry points, both over the same read-only tools (tools.py):
  answer(handle, text, data_dir, roster) -> str      a plain-language reply for a voice or text turn
  call_tool(name, args, handle, data_dir, roster)    JSON-friendly dispatcher for a voice agent's tool calls
                                                     (get_plan / get_route / get_change), with the same scoping
Nothing here places calls or sends texts; the provider layer wires these in once it is verified.
"""
import re

from queue_morning import render_plan, render_route
from tools import UNKNOWN_CALLER_MESSAGE, Tools, resolve_caller

FARM_RE = re.compile(r"\bF\d{4,}\b", re.IGNORECASE)
CHANGE_WORDS = ("change", "changed", "happened", "different", "new since")
ROUTE_WORDS = ("route", "stop", "first", "visit", "where do i go", "today's list")


def describe_change(ch):
    w = ch.get("window") or {}
    when = f" (between {w['start']} and {w['end']})" if w else ""
    where = f" Location: {ch['where']}." if ch.get("where") else ""
    head = "Change" if ch["status"] != "clear" else "No change"
    return f"{head} on farm {ch['farm_id']} as of {ch['as_of']}: {ch['detail']}{when}.{where}"


def answer(handle, text, data_dir, roster):
    caller = resolve_caller(_strip_scheme(handle), roster)
    if caller is None:
        return UNKNOWN_CALLER_MESSAGE
    t = Tools(data_dir, caller)
    q = (text or "").lower()
    ids = [m.upper() for m in FARM_RE.findall(text or "")]
    wants_change = any(w in q for w in CHANGE_WORDS)

    if caller["role"] == "farmer":
        if any(i != caller["farm_id"] for i in ids):
            return "I can only tell you about your own farm."
        try:
            return describe_change(t.get_change()) if wants_change else render_plan(t.get_plan(), caller["language"])
        except KeyError:
            return f"There is no plan for your farm ({caller['farm_id']}) this morning yet."

    # officer: a named farm, otherwise the route
    if ids:
        fid = ids[0]
        try:
            plan = t.get_plan(fid)
        except KeyError:
            return f"No plan for farm {fid} this morning."
        return describe_change(t.get_change(fid)) if wants_change else render_plan(plan, caller["language"])
    return render_route(t.get_route(), caller["language"])


def call_tool(name, args, handle, data_dir, roster):
    """Dispatcher for the three shared tools. Returns the tool's JSON, or {"error": message}."""
    caller = resolve_caller(_strip_scheme(handle), roster)
    t = Tools(data_dir, caller)
    args = args or {}
    try:
        if name == "get_plan":
            return t.get_plan(args.get("farm_id"))
        if name == "get_route":
            return t.get_route()
        if name == "get_change":
            return t.get_change(args.get("farm_id"))
        return {"error": f"unknown tool {name}"}
    except PermissionError as e:
        return {"error": str(e)}
    except KeyError as e:
        return {"error": str(e).strip("'")}
    except ValueError as e:
        return {"error": str(e)}


def _strip_scheme(handle):
    h = (handle or "").strip()
    for prefix in ("tel:", "sms:", "imessage:"):
        if h.lower().startswith(prefix):
            return h[len(prefix):]
    return h
