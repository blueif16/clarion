#!/usr/bin/env python3
"""clarion-sim.py — REAL-SIM driver: simulate a user with no mic and no Chrome.

It joins the LIVE LiveKit room as a participant, which makes the running
`voice_entry` worker DISPATCH the agent (real dispatch, real room, real session).
The worker — when started with CLARION_SIM_UTTERANCES — then drives those phrases
as user turns via `session.generate_reply(user_input=...)`, exercising the REAL
LLM + tools + TTS. Nothing here is faked: the only un-real link is the mic→STT
hop, with text standing in for speech ("drive with our own text input").

Usage:
  CLARION_ROOM=clarion-hero .venv/bin/python scripts/clarion-sim.py [hold_seconds]
"""
from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

_AGENT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent")
load_dotenv(os.path.join(_AGENT, ".env"))

from livekit import api, rtc  # noqa: E402

ROOM = os.environ.get("CLARION_ROOM", "clarion-hero")
URL = os.environ["LIVEKIT_URL"].strip().strip('"').strip("'")
HOLD = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0


async def main() -> int:
    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity("sim-human")
        .with_grants(
            api.VideoGrants(
                room_join=True, room=ROOM, can_publish=True, can_subscribe=True
            )
        )
        .to_jwt()
    )
    room = rtc.Room()

    @room.on("participant_connected")
    def _joined(p: rtc.RemoteParticipant) -> None:
        print(f"  [sim] saw participant: {p.identity}", flush=True)

    await room.connect(URL, token)
    print(f"  [sim] joined room {ROOM!r} as 'sim-human' → agent should DISPATCH now", flush=True)
    print(f"  [sim] holding {HOLD:.0f}s while the worker runs its scripted utterances…", flush=True)
    await asyncio.sleep(HOLD)
    await room.disconnect()
    print("  [sim] disconnected.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
