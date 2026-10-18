"""Grok Voice bridge: relays a Twilio Media Stream to a Grok realtime session and back, with the shared tools.

    pip install websockets
    python intelligence/grok_bridge.py --data data/out --port 8789 [--roster ROSTER.csv]

Twilio reaches it through the TwiML that inbound_server serves at /twilio/stream:
    <Response><Connect><Stream url="wss://<PUBLIC_WS_URL>/media-stream">
      <Parameter name="from" value="+91..."/></Stream></Connect></Response>
The `from` parameter is the person on the line (the caller for an inbound call, the callee for an outbound one);
it selects the roster row, the language, the facts in the session and the tool scope (see voice_agent.py).

Needs: XAI_API_KEY (and optionally XAI_REALTIME_URL, XAI_VOICE) in .env, a public wss:// URL (ngrok or a
deployment) in PUBLIC_WS_URL, and a Twilio number whose voice webhook points at inbound_server /twilio/stream.
The async plumbing here is thin on purpose; everything decidable is in voice_agent.py and covered by tests.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import voice_agent as va
from common import load_env
from tools import load_roster

DEFAULT_URL = "wss://api.x.ai/v1/realtime"


async def _pump_grok(grok, twilio, stream_sid, caller, data_dir, roster, voice, log):
    """Server events from Grok -> audio to Twilio, tool calls to our tools, session setup."""
    async for raw in grok:
        ev = json.loads(raw)
        t = ev.get("type")
        if t == "conversation.created":
            await grok.send(json.dumps(va.session_config(caller, data_dir, roster, voice)))
        elif t == "session.updated":
            await grok.send(json.dumps(va.greeting_event()))
            await grok.send(json.dumps({"type": "response.create"}))
        elif t == "error":
            log(f"grok error: {ev.get('error', ev)}")
        for out in va.realtime_to_twilio(ev, stream_sid):
            await twilio.send(json.dumps(out))
        call = va.function_call_from(ev)
        if call:
            call_id, name, args = call
            log(f"tool {name} for a caller")
            result = va.run_tool(name, args, caller, data_dir, roster)
            for e in va.tool_result_events(call_id, result):
                await grok.send(json.dumps(e))
        if t == "conversation.item.input_audio_transcription.completed":
            log("caller said: [redacted]")


async def handle_twilio(twilio, data_dir, roster, api_key, api_url, voice, log=print):
    """One Twilio media stream = one call = one Grok session."""
    import websockets

    stream_sid, caller, grok, pump = None, "", None, None
    query = parse_qs(urlparse(getattr(twilio, "path", "") or "").query)
    try:
        async for raw in twilio:
            frame = json.loads(raw)
            ev = frame.get("event")
            if ev == "start":
                stream_sid = frame["start"]["streamSid"]
                params = frame["start"].get("customParameters") or {}
                caller = params.get("from") or (query.get("from") or [""])[0]
                log(f"stream {stream_sid} for a caller")
                grok = await websockets.connect(api_url, additional_headers={"Authorization": f"Bearer {api_key}"})
                pump = asyncio.create_task(_pump_grok(grok, twilio, stream_sid, caller, data_dir, roster, voice, log))
            elif ev == "media" and grok is not None:
                await grok.send(json.dumps(va.twilio_to_realtime(frame)))
            elif ev == "stop":
                break
    finally:
        if pump:
            pump.cancel()
        if grok is not None:
            await grok.close()
        log(f"stream {stream_sid} closed")


async def serve(data_dir, roster_path, host, port):
    import websockets

    load_env()
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        raise SystemExit("XAI_API_KEY is not set in .env")
    api_url = os.environ.get("XAI_REALTIME_URL") or DEFAULT_URL
    voice = os.environ.get("XAI_VOICE") or va.DEFAULT_VOICE
    roster = load_roster(roster_path)

    async def handler(ws):
        await handle_twilio(ws, Path(data_dir), roster, api_key, api_url, voice)

    print(f"  grok bridge on ws://{host}:{port}/media-stream -> {api_url} (voice {voice}, {len(roster)} roster rows)")
    async with websockets.serve(handler, host, port):
        await asyncio.Future()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/out")
    ap.add_argument("--roster", default=None)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8789)
    a = ap.parse_args()
    asyncio.run(serve(a.data, a.roster, a.host, a.port))
