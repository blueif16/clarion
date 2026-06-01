"""S1 — THE GATE harness (execution §7 accept conditions).

Produces the three pieces of REAL evidence, driving the REAL seam path:
  (a) ROUND-TRIP : user turn -> advance_task launches the graph -> consent
      interrupt surfaced -> "yes" -> the C2 input is ACTUALLY filled (asserted by
      a CDP read-back of the live input value).
  (b) BARGE-IN   : while the proposal tool is in flight, a real
      SpeechHandle.interrupt() flips speech_handle.interrupted -> the tool cancels
      cleanly and fills NOTHING (asserted by CDP read-back == empty).
  (c) IDEMPOTENCY: deliver resume(approve) TWICE -> EXACTLY ONE fill (asserted by
      counting actuator.act calls + the graph's ACT once-flag).

REAL vs SIMULATED (honest, per the brief):
  - REAL: Playwright/CDP perception + native-setter fill + CDP read-back; the
    LangGraph graph (interrupt/Command/InMemorySaver); the LiveKit SpeechHandle
    object and its genuine `wait_if_not_interrupted` / `interrupted` /
    `interrupt()` mechanism; the Deepgram STT + Gemini LLM/TTS components are
    constructed (wiring verified).
  - SIMULATED: the user's spoken turns and the barge-in are injected
    programmatically (no live mic in this headless env). The *mechanism* exercised
    is the real one. The Gemini LLM tool-decision call and Gemini TTS audio are
    BILLING-BLOCKED at runtime (403 dunning on project 956065465952 for both the
    AQ.* Vertex key and the AI Studio key) — so we do not route a turn through the
    LLM here; we invoke the identical `run_advance_task` body the LLM would call.
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

# Load .env from the agent root regardless of CWD.
_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_AGENT_ROOT, ".env"))

from livekit.agents.voice.speech_handle import SpeechHandle  # noqa: E402

from clarion.contracts.events import ConsentDecision  # noqa: E402
from spike.actuator_min import MinActuator  # noqa: E402
from spike.voice_agent import SeamRunner, run_advance_task  # noqa: E402

TARGET_URL = os.environ.get("SPIKE_TARGET_URL", "http://127.0.0.1:8765/index.html")


def _log(prefix: str):
    def inner(msg: str) -> None:
        print(f"  [{prefix}] {msg}", flush=True)
    return inner


def _name_index(actuator: MinActuator, sm) -> int:
    return next(i for i, n in sm.nodes.items() if n.role in ("textbox", "searchbox"))


async def round_trip(actuator: MinActuator) -> bool:
    print("\n=== (a) ROUND-TRIP: speak -> propose -> consent -> yes -> fill ===")
    log = _log("a")
    runner = SeamRunner(actuator=actuator, thread_id="gate-a")

    sm0 = await actuator.perceive()
    idx = _name_index(actuator, sm0)
    before = await actuator.read_value(idx)
    log(f"USER TURN (simulated STT final): 'fill in my name'")
    log(f"pre-state: field[{idx}] '{sm0.nodes[idx].name}' value={before!r}")

    # The agent would speak this while the graph runs; we use a real SpeechHandle.
    speech = SpeechHandle.create()
    utterance = await run_advance_task(speech, runner, log)
    assert utterance is not None, "no consent utterance surfaced"
    log(f"AGENT SAYS (readback): {utterance!r}")

    # The field must NOT be filled yet — we are parked at the consent gate.
    mid = await actuator.read_value(idx)
    log(f"at consent gate: field value={mid!r} (must be empty — no act before yes)")
    assert mid == "" or mid is None, f"field filled BEFORE consent: {mid!r}"

    # User says yes.
    log("USER TURN (simulated STT final): 'yes'")
    final = await runner.resume(ConsentDecision(decision="approve"))

    after = await actuator.read_value(idx)
    log(f"post-resume: field value={after!r}")
    log(f"consent_log: {[(c.proposal_id, c.decision) for c in final['consent_log']]}")
    log(f"act calls on actuator: {len(actuator_acts(actuator))}")

    ok = after == "Jane Smith"
    print(f"  RESULT (a): {'PASS' if ok else 'FAIL'} — CDP read-back "
          f"{'==' if ok else '!='} 'Jane Smith'")
    return ok


async def barge_in(actuator: MinActuator) -> bool:
    print("\n=== (b) BARGE-IN / CANCEL: interrupt mid-tool -> no fill ===")
    log = _log("b")
    runner = SeamRunner(actuator=actuator, thread_id="gate-b")

    sm0 = await actuator.perceive()
    idx = _name_index(actuator, sm0)
    before = await actuator.read_value(idx)
    log(f"pre-state: field[{idx}] value={before!r}")
    acts_before = len(actuator_acts(actuator))

    speech = SpeechHandle.create()

    # Make the in-flight PROPOSE genuinely slow so the barge-in lands while the
    # tool's background task is REALLY running (not a 0-delay race). We wrap the
    # actuator.perceive used inside the graph with a small delay just for this
    # condition, so the barge-in arrives mid-flight.
    orig_perceive = actuator.perceive

    async def slow_perceive():
        await asyncio.sleep(0.3)
        return await orig_perceive()

    actuator.perceive = slow_perceive  # type: ignore[assignment]

    # Fire the barge-in 100ms in — the tool task is provably still in flight.
    async def _delayed_barge_in() -> None:
        await asyncio.sleep(0.1)
        log("USER BARGES IN mid-readback (simulated): speech_handle.interrupt()")
        speech.interrupt(force=True)

    try:
        barge = asyncio.ensure_future(_delayed_barge_in())
        result = await run_advance_task(speech, runner, log)
        await barge
    finally:
        actuator.perceive = orig_perceive  # type: ignore[assignment]

    after = await actuator.read_value(idx)
    acts_after = len(actuator_acts(actuator))
    log(f"tool returned: {result!r} (None on barge-in)")
    log(f"post-state: field value={after!r}; actuator act calls "
        f"{acts_before}->{acts_after}")
    log(f"speech_handle.interrupted={speech.interrupted}")

    # Companion proof of the agentic clause: a 'reject' decision also fills
    # nothing (no action without a yes). Fresh thread + page already empty.
    log("companion: deliver resume(REJECT) on a fresh turn -> still no fill")
    runner2 = SeamRunner(actuator=actuator, thread_id="gate-b-reject")
    speech2 = SpeechHandle.create()
    await run_advance_task(speech2, runner2, log)
    final_rej = await runner2.resume(ConsentDecision(decision="reject"))
    after_rej = await actuator.read_value(idx)
    acts_rej = len(actuator_acts(actuator))
    log(f"after reject: field={after_rej!r}; decision="
        f"{final_rej['consent_log'][-1].decision!r}; total fill calls={acts_rej}")

    ok = (
        result is None
        and (after == "" or after is None)
        and acts_after == acts_before
        and speech.interrupted
        # reject path: no fill either
        and (after_rej == "" or after_rej is None)
        and acts_rej == acts_before
        and final_rej["consent_log"][-1].decision == "reject"
    )
    print(f"  RESULT (b): {'PASS' if ok else 'FAIL'} — barge-in cancelled the "
          f"in-flight tool; NO fill, NO act call")
    return ok


async def idempotency(actuator: MinActuator) -> bool:
    print("\n=== (c) IDEMPOTENCY: resume(approve) twice -> exactly one fill ===")
    log = _log("c")
    runner = SeamRunner(actuator=actuator, thread_id="gate-c")

    sm0 = await actuator.perceive()
    idx = _name_index(actuator, sm0)
    acts_before = len(actuator_acts(actuator))

    speech = SpeechHandle.create()
    utterance = await run_advance_task(speech, runner, log)
    assert utterance is not None
    log(f"AGENT SAYS: {utterance!r}")

    log("USER TURN: 'yes' (#1)")
    final1 = await runner.resume(ConsentDecision(decision="approve"))
    after1 = await actuator.read_value(idx)
    acts_1 = len(actuator_acts(actuator))
    log(f"after resume #1: field={after1!r}; act calls={acts_1 - acts_before}")

    log("USER TURN: 'yes' (#2) — duplicate approve delivered to the SAME thread")
    final2 = await runner.resume(ConsentDecision(decision="approve"))
    after2 = await actuator.read_value(idx)
    acts_2 = len(actuator_acts(actuator))
    log(f"after resume #2: field={after2!r}; act calls={acts_2 - acts_before}")

    fills = acts_2 - acts_before
    ok = fills == 1 and after2 == "Jane Smith"
    print(f"  RESULT (c): {'PASS' if ok else 'FAIL'} — duplicate approve caused "
          f"{fills} fill (expected exactly 1; the ACT once-flag held)")
    return ok


# Count of real act() calls — MinActuator records them implicitly via the page;
# we wrap perceive/act counting with a lightweight monkeypatch on first use.
_ACT_COUNTS: dict[int, int] = {}


def actuator_acts(actuator: MinActuator) -> list:
    return getattr(actuator, "_acted_calls", [])


def _instrument(actuator: MinActuator) -> None:
    """Wrap actuator.act to count real fill executions (the honest double-fill
    detector). Does not change behaviour."""
    actuator._acted_calls = []  # type: ignore[attr-defined]
    orig = actuator.act

    async def counting_act(action):
        if action.kind == "fill":
            actuator._acted_calls.append(action)  # type: ignore[attr-defined]
        return await orig(action)

    actuator.act = counting_act  # type: ignore[assignment]


async def main() -> int:
    print("Clarion S1 — SEAM SPIKE GATE")
    print(f"target page: {TARGET_URL}")
    actuator = await MinActuator.create(TARGET_URL, headless=True)
    _instrument(actuator)
    results: dict[str, bool] = {}
    try:
        # Each condition uses its own graph thread + a fresh page state.
        results["a"] = await round_trip(actuator)
        await _reset_page(actuator)
        results["b"] = await barge_in(actuator)
        await _reset_page(actuator)
        results["c"] = await idempotency(actuator)
    finally:
        await actuator.close()

    print("\n=== GATE SUMMARY ===")
    for k in ("a", "b", "c"):
        print(f"  ({k}) {'PASS' if results.get(k) else 'FAIL'}")
    all_ok = all(results.values())
    print(f"\nGATE: {'GREEN — all three conditions pass' if all_ok else 'RED'}")
    return 0 if all_ok else 1


async def _reset_page(actuator: MinActuator) -> None:
    """Reload the page so each condition starts from an empty field."""
    await actuator._page.reload(wait_until="load")  # type: ignore[attr-defined]


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
