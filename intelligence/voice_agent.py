"""The Grok Voice session for a Harvest Radar call, and the pure protocol mapping the bridge uses.

Protocol facts (from xAI's voice-agent-phone example and docs.x.ai/docs/guides/voice):
  connect   wss://api.x.ai/v1/realtime  with  Authorization: Bearer <XAI_API_KEY>
  client -> session.update {session: {voice, instructions, audio.input/output.format {type: "audio/pcmu"},
                                      turn_detection {type: "server_vad"}, tools [function tools]}}
            input_audio_buffer.append {audio: <base64 mulaw from Twilio>}      (server VAD; no commit needed)
            conversation.item.create {item: {type: "message", role: "user", content: [{type: "input_text", text}]}}
            conversation.item.create {item: {type: "function_call_output", call_id, output: <JSON string>}}
            response.create
  server -> conversation.created (send session.update), session.updated (greet + response.create),
            response.output_audio.delta {delta} -> Twilio media frame, input_audio_buffer.speech_started -> Twilio clear,
            response.output_item.done {item: {type: "function_call", name, call_id, arguments}} -> run the tool,
            response.output_audio_transcript.delta, conversation.item.input_audio_transcription.completed, error
Twilio Media Streams: frames {event: connected|start|media|stop|mark|dtmf}; start.streamSid, start.customParameters,
  media.payload (base64 mulaw 8 kHz); we send {event: "media", streamSid, media: {payload}} and {event: "clear", streamSid}.

The session instructions carry the caller's own plan (or the officer's route), so the agent can speak without a
tool call, and the three shared tools let it answer questions; scoping is enforced server-side in tools.py, so a
farmer's session cannot read another farm even if the model asks.
"""
import json

import inbound
import queue_morning as q
from providers import LANGUAGE_NAMES
from common import product_name
from tools import UNKNOWN_CALLER_MESSAGE, Tools, resolve_caller
from voice_guard import VOICE_OF_THE_MORNING

VOICES = ("ara", "rex", "sal", "eve", "leo")
DEFAULT_VOICE = "ara"
AUDIO_FORMAT = {"type": "audio/pcmu"}      # telephony mulaw, passed through untouched (8 kHz)

TOOL_SCHEMAS = {
    "get_plan": {"type": "function", "name": "get_plan",
                 "description": "Today's plan for a farm: flag, what to do today, all eight checks with status and detail, risks.",
                 "parameters": {"type": "object", "properties": {"farm_id": {"type": "string", "description": "Farm id such as F0012"}},
                                "required": []}},
    "get_route": {"type": "function", "name": "get_route",
                  "description": "The officer's route for this morning: ordered stops with farm id, visit or call, and why.",
                  "parameters": {"type": "object", "properties": {}, "required": []}},
    "get_change": {"type": "function", "name": "get_change",
                   "description": "What changed on a farm since the previous satellite pass.",
                   "parameters": {"type": "object", "properties": {"farm_id": {"type": "string"}}, "required": []}},
    "get_outlook": {"type": "function", "name": "get_outlook",
                    "description": "The outlook for a farm beyond today's plan: yield band from the greenness curve, harvest now "
                                   "or wait and why, the chance to finish before the rain, soil water, and whether this field is "
                                   "browning faster than a named next-door neighbour (surname only, never a phone number).",
                    "parameters": {"type": "object", "properties": {"farm_id": {"type": "string"}}, "required": []}},
}
RULES = ("Rules: speak in {language}; keep answers short, they are spoken aloud. Never invent a measurement, a date or "
         "a status: say only what the plan or the tools return. You may only talk about {scope}; if asked about "
         "anything else say you can only help with that. Dates are YYYY-MM-DD; read farm ids digit by digit.")


def _instructions(row, data_dir, roster):
    language = LANGUAGE_NAMES.get((row["language"] or "en")[:2], row["language"])
    t = Tools(data_dir, row, roster)
    if row["role"] == "officer":
        words = q.render_route(t.get_route(), "en")
        scope = "the morning route and the farms in today's plans"
        who = f"You are {product_name()}'s voice assistant speaking with the extension officer {row['name']}."
    else:
        try:
            words = q.render_plan(t.get_plan(), "en")
        except KeyError:
            words = f"There is no plan for farm {row['farm_id']} this morning."
        try:
            outlook = t.get_outlook()["lines"]
            if outlook:
                words += "\nOutlook: " + " ".join(outlook)
        except Exception:  # the outlook is additive; a missing or odd analytics file never breaks the call
            pass
        scope = f"this caller's own farm, {row['farm_id']}"
        who = f"You are {product_name()}'s voice assistant speaking with farmer {row['name']} about farm {row['farm_id']}."
    return "\n".join([who, RULES.format(language=language, scope=scope), VOICE_OF_THE_MORNING,
                      "Today's facts (English; translate as you speak):", words])


def session_config(handle, data_dir, roster, voice=DEFAULT_VOICE):
    """The session.update event for this caller (or an unknown-caller session that only says so)."""
    row = resolve_caller(inbound._strip_scheme(handle), roster)
    if row is None:
        instructions = (f"Say exactly this, then say goodbye: '{UNKNOWN_CALLER_MESSAGE}' Do not discuss any farm.")
        tools = []
    else:
        instructions = _instructions(row, data_dir, roster)
        names = (["get_plan", "get_route", "get_change", "get_outlook"] if row["role"] == "officer"
                 else ["get_plan", "get_change", "get_outlook"])
        tools = [TOOL_SCHEMAS[n] for n in names]
    return {"type": "session.update",
            "session": {"voice": voice if voice in VOICES else DEFAULT_VOICE, "instructions": instructions,
                        "audio": {"input": {"format": dict(AUDIO_FORMAT)}, "output": {"format": dict(AUDIO_FORMAT)}},
                        "turn_detection": {"type": "server_vad"}, "tools": tools}}


def greeting_event(text="Greet the caller by name in their language and read today's plan."):
    return {"type": "conversation.item.create",
            "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}


def run_tool(name, arguments, handle, data_dir, roster):
    """Run a tool call from the model; always returns a JSON string (the function_call_output)."""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) and arguments.strip() else (arguments or {})
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
    except Exception as e:
        return json.dumps({"error": f"bad arguments: {e}"})
    return json.dumps(inbound.call_tool(name, args, handle, data_dir, roster), ensure_ascii=False)


# ---- Twilio <-> realtime mapping (pure) ----
def twilio_to_realtime(frame):
    if frame.get("event") == "media":
        return {"type": "input_audio_buffer.append", "audio": frame["media"]["payload"]}
    return None


def realtime_to_twilio(event, stream_sid):
    t = event.get("type")
    if t == "response.output_audio.delta":
        return [{"event": "media", "streamSid": stream_sid, "media": {"payload": event["delta"]}}]
    if t == "input_audio_buffer.speech_started":
        return [{"event": "clear", "streamSid": stream_sid}]
    return []


def function_call_from(event):
    """(call_id, name, arguments) when the event completes a function call, else None."""
    if event.get("type") == "response.output_item.done":
        item = event.get("item") or {}
        if item.get("type") == "function_call":
            return item.get("call_id"), item.get("name"), item.get("arguments") or "{}"
    return None


def tool_result_events(call_id, output):
    return [{"type": "conversation.item.create", "item": {"type": "function_call_output", "call_id": call_id, "output": output}},
            {"type": "response.create"}]
