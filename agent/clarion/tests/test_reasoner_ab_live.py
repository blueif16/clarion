"""Live A/B — Gemini(thinking=0) vs Qwen(Nebius) on the SAME real usa.gov page.

Marked ``live`` (network + Playwright + real Chromium + real keys), EXCLUDED from
the deterministic gate. Run:

    pytest clarion -m live -k reasoner_ab -s

Perceives ``https://www.usa.gov/benefits`` ONCE, grounds ONE live map + ONE fact
set, then runs BOTH reasoners (N runs each) against the identical slice + facts so
the only variable is the provider. Reports per provider: decode latency (N runs),
whether the enums were honored (target_index ∈ live map, value_ref ∈ Fact ids),
whether ``say`` was verbatim-grounded, and whether ``validate_step_proposal``
passed first try or needed the re-ask. Endpoint flakiness (503/availability) is
caught + reported, not failed-on.

The Qwen leg SKIPS (does not fail) if ``NEBIUS_API_KEY`` is absent — so the Gemini
A side still produces numbers on a partial environment.
"""

from __future__ import annotations

import os

import pytest

from clarion.contracts.state import SelectorMap
from clarion.kernel.reasoner_guard import resolve_value_ref, validate_step_proposal

pytestmark = pytest.mark.live

USA_GOV_URL = "https://www.usa.gov/benefits"
GOAL = "find benefits I may qualify for on this page"
RUNS = 3


async def _measure(reasoner, live_map, facts, runs: int):
    """Run decide_step ``runs`` times; return a per-run record list + an error."""
    records = []
    for _ in range(runs):
        try:
            proposal = await reasoner.decide_step(GOAL, live_map, facts, history=[])
        except Exception as exc:  # noqa: BLE001 - capture 503/availability honestly
            records.append({"error": f"{type(exc).__name__}: {str(exc)[:160]}"})
            continue
        verdict = validate_step_proposal(proposal, live_map, facts)
        index_ok = proposal.target_index in live_map.nodes
        ref = resolve_value_ref(proposal.value_ref, facts)
        ref_ok = proposal.value_ref is None or (ref is not None)
        say_ok = (not proposal.say) or any(proposal.say in f.value for f in facts)
        records.append(
            {
                "decide_ms": reasoner.last_decide_ms,
                # decide_calls grows by 1 (clean) or 2 (one re-ask) per run.
                "calls": len(reasoner.decide_calls),
                "valid": verdict.ok,
                "index_ok": index_ok,
                "ref_ok": ref_ok,
                "say_ok": say_ok,
                "target_index": proposal.target_index,
                "value_ref": proposal.value_ref,
                "say": proposal.say,
                "fallback": getattr(reasoner, "last_used_fallback", None),
            }
        )
    return records


def _print_block(name: str, model: str, records: list) -> None:
    print(f"\n--- {name}  (model={model}) ---")
    for i, r in enumerate(records, 1):
        if "error" in r:
            print(f"  run {i}: ERROR {r['error']}")
            continue
        ms = r["decide_ms"]
        print(
            f"  run {i}: decide_ms={ms:.0f}  valid={r['valid']}  "
            f"index_ok={r['index_ok']}  ref_ok={r['ref_ok']}  say_ok={r['say_ok']}  "
            f"target_index={r['target_index']}  value_ref={r['value_ref']}  "
            f"fallback={r['fallback']}"
        )
    oks = [r for r in records if "error" not in r]
    if oks:
        times = [r["decide_ms"] for r in oks]
        print(
            f"  >> decide_ms min/median/max = "
            f"{min(times):.0f}/{sorted(times)[len(times)//2]:.0f}/{max(times):.0f} ms"
            f" | valid={sum(r['valid'] for r in oks)}/{len(oks)}"
            f" | enums-honored(first-try, no re-ask) ="
            f" {sum(1 for r in oks if r['valid'] and r['index_ok'] and r['ref_ok'])}/{len(oks)}"
        )


@pytest.mark.asyncio
async def test_reasoner_ab_on_real_usa_gov_page() -> None:
    from dotenv import load_dotenv

    from clarion.actuator.actuator import PlaywrightActuator
    from clarion.adapters.gemini_reasoner import GeminiReasoner
    from clarion.app.page_retriever import PageRetriever

    load_dotenv()

    actuator = await PlaywrightActuator.create(USA_GOV_URL, headless=True)
    try:
        live_map: SelectorMap = await actuator.perceive()
        facts = await PageRetriever(actuator).query(GOAL, k=12)
        assert live_map.nodes and facts

        print("\n=== Reasoner A/B — REAL usa.gov/benefits ===")
        print(
            f"live map nodes = {len(live_map.nodes)} | grounded facts = {len(facts)} | "
            f"runs = {RUNS}"
        )

        # A — Gemini, thinking OFF (config knob on the fixed model).
        gemini = GeminiReasoner()
        a = await _measure(gemini, live_map, facts, RUNS)
        _print_block("A  Gemini (thinking=0)", gemini.model, a)

        # B — Qwen via Nebius (skips, does not fail, if the key is absent).
        if not os.environ.get("NEBIUS_API_KEY"):
            print(
                "\n--- B  Qwen (Nebius): SKIPPED — NEBIUS_API_KEY not set. "
                "Add it to agent/.env to run this leg. ---"
            )
        else:
            from clarion.adapters.openai_reasoner import OpenAIReasoner

            qwen = OpenAIReasoner()
            b = await _measure(qwen, live_map, facts, RUNS)
            _print_block("B  Qwen (Nebius)", qwen.model, b)

        # The A side must produce at least one valid, guard-passing decode.
        assert any(r.get("valid") for r in a if "error" not in r), (
            "Gemini(thinking=0) produced no guard-valid decode"
        )
    finally:
        await actuator.close()
