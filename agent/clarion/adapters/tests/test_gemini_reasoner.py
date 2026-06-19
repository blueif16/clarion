"""Unit tests for the ``GeminiReasoner`` adapter — NETWORK-FREE.

The real Gemini call is the ONLY thing stubbed: every test injects a fake
``_generate_json`` (or asserts the SDK client is never built), so the full
adapter logic runs without a key or a socket. The live proof against real
usa.gov is the ``-m live`` spike in
``clarion/tests/test_gemini_reasoner_spike_live.py``.

What is covered (the contract this adapter must hold):
  1. ABC conformance + construct-only (no client, no network at import).
  2. The per-call schema puts ``scratch_reasoning`` FIRST and enumerates the LIVE
     indices + real Fact ids (the "per-call enum of live ids" requirement).
  3. A clean decode → guard-valid ``StepProposal`` (string enums coerced back).
  4. ``say`` is forced VERBATIM from the resolved value_ref Fact; an ungrounded
     ``say`` the model invents is cleared.
  5. The POST-DECODE FENCE: an off-page index triggers ONE re-ask; a second bad
     decode fails closed (``ReasonerError``); a re-ask that recovers succeeds.
  6. ``plan_goal`` decodes a generic plan and clamps ``done_check`` to the enum.
"""

from __future__ import annotations

import pytest

from clarion.adapters.gemini_reasoner import (
    SUCCESS_CHECKS,
    GeminiReasoner,
    ReasonerError,
    _step_schema,
)
from clarion.contracts.ports import Reasoner
from clarion.contracts.state import (
    AxNode,
    Fact,
    PageReadout,
    SelectorMap,
    StepProposal,
)


def _live_map() -> SelectorMap:
    return SelectorMap(
        nodes={
            3: AxNode(index=3, role="link", name="Benefits", node_id="ax-3"),
            7: AxNode(index=7, role="button", name="Apply", node_id="ax-7"),
        },
        token_estimate=10,
    )


def _facts() -> list[Fact]:
    return [
        Fact(value="Amount due: $84.32", source_node_id="ax-99", verified=True),
        Fact(value="Due June 15, 2026", source_node_id="ax-100", verified=True),
    ]


# ---------------------------------------------------------------------------
# 1. ABC conformance + construct-only (no network, no client).
# ---------------------------------------------------------------------------


def test_is_reasoner_and_construct_only_builds_no_client() -> None:
    r = GeminiReasoner(api_key="unit-test-key", model="gemini-3.5-flash")
    assert isinstance(r, Reasoner)
    assert r.model == "gemini-3.5-flash"
    # Construction is pure config — the SDK client is NOT built (lazy).
    assert r._client is None
    assert r.last_decide_ms is None


def test_ensure_client_without_key_raises_not_network() -> None:
    r = GeminiReasoner(api_key="", model="m")
    r._api_key = None  # simulate a missing key
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        r._ensure_client()


# ---------------------------------------------------------------------------
# 2. The per-call schema: scratch_reasoning FIRST + live-id enums.
# ---------------------------------------------------------------------------


def test_schema_orders_scratch_reasoning_first() -> None:
    schema = _step_schema([3, 7], ["fact-a", "fact-b"])
    assert schema["propertyOrdering"][0] == "scratch_reasoning"


def test_schema_enumerates_live_indices_and_fact_ids() -> None:
    schema = _step_schema([3, 7], ["fact-a", "fact-b"])
    idx_enum = schema["properties"]["target_index"]["enum"]
    ref_enum = schema["properties"]["value_ref"]["enum"]
    # Live indices appear as strings (+ a null sentinel for value-less actions).
    assert "3" in idx_enum and "7" in idx_enum and "null" in idx_enum
    # value_ref enumerates the REAL fact ids (+ null) — never free text.
    assert "fact-a" in ref_enum and "fact-b" in ref_enum and "null" in ref_enum
    # success_check is the canonical SELECTION enum.
    assert tuple(schema["properties"]["success_check"]["enum"]) == SUCCESS_CHECKS


# ---------------------------------------------------------------------------
# 3 + 4. Clean decode → guard-valid; say forced verbatim from the ref Fact.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decide_step_clean_decode_grounds_say_verbatim() -> None:
    facts = _facts()
    fid = facts[0].id

    async def fake_gen(system, prompt, schema):  # noqa: ARG001
        return {
            "scratch_reasoning": "the amount-due value answers the goal",
            "action_kind": "read",
            "target_index": "3",  # string enum, coerced back to int
            "value_ref": fid,
            "irreversibility": "reversible",
            "irreversibility_rationale": "a read changes nothing",
            "success_check": "confirmation_fact",
            # Model paraphrases — must be OVERWRITTEN by the grounded value.
            "say": "you owe eighty four dollars",
        }

    r = GeminiReasoner(api_key="k")
    r._generate_json = fake_gen  # type: ignore[method-assign]
    proposal = await r.decide_step("find the amount due", _live_map(), facts, [])

    assert proposal.target_index == 3  # coerced str -> int
    assert proposal.value_ref == fid
    # say is the BYTE-IDENTICAL grounded span, not the model's paraphrase.
    assert proposal.say == "Amount due: $84.32"
    assert r.last_decide_ms is not None and r.last_decide_ms >= 0.0


@pytest.mark.asyncio
async def test_ungrounded_say_without_value_ref_is_cleared() -> None:
    facts = _facts()

    async def fake_gen(system, prompt, schema):  # noqa: ARG001
        return {
            "scratch_reasoning": "click apply",
            "action_kind": "click",
            "target_index": "7",
            "value_ref": "null",  # a click carries no value
            "irreversibility": "unknown",
            "irreversibility_rationale": "submits an application",
            "success_check": "navigated",
            "say": "I will now pay this for you",  # ungrounded fabrication
        }

    r = GeminiReasoner(api_key="k")
    r._generate_json = fake_gen  # type: ignore[method-assign]
    proposal = await r.decide_step("apply", _live_map(), facts, [])

    assert proposal.target_index == 7
    assert proposal.value_ref is None
    # No value_ref AND not a substring of any grounded fact → say is cleared.
    assert proposal.say == ""


# ---------------------------------------------------------------------------
# 5. The post-decode FENCE: off-page index → re-ask → recover / fail-closed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offpage_index_triggers_reask_then_recovers() -> None:
    facts = _facts()
    fid = facts[0].id
    calls = {"n": 0}

    async def fake_gen(system, prompt, schema):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            # First decode hallucinates an off-page index.
            return {
                "scratch_reasoning": "guessing",
                "action_kind": "read",
                "target_index": "999",
                "value_ref": fid,
                "irreversibility": "reversible",
                "irreversibility_rationale": "",
                "success_check": "confirmation_fact",
                "say": "",
            }
        # The re-ask (it must carry the guard error) picks a LIVE index.
        assert "rejected" in prompt.lower()
        return {
            "scratch_reasoning": "use a real index",
            "action_kind": "read",
            "target_index": "3",
            "value_ref": fid,
            "irreversibility": "reversible",
            "irreversibility_rationale": "",
            "success_check": "confirmation_fact",
            "say": "",
        }

    r = GeminiReasoner(api_key="k")
    r._generate_json = fake_gen  # type: ignore[method-assign]
    proposal = await r.decide_step("find amount", _live_map(), facts, [])

    assert calls["n"] == 2  # exactly one re-ask
    assert proposal.target_index == 3


@pytest.mark.asyncio
async def test_persistent_offpage_index_fails_closed() -> None:
    facts = _facts()

    async def fake_gen(system, prompt, schema):  # noqa: ARG001
        # The model keeps hallucinating an off-page index even after the re-ask.
        return {
            "scratch_reasoning": "still wrong",
            "action_kind": "click",
            "target_index": "424242",
            "value_ref": "null",
            "irreversibility": "reversible",
            "irreversibility_rationale": "",
            "success_check": "navigated",
            "say": "",
        }

    r = GeminiReasoner(api_key="k")
    r._generate_json = fake_gen  # type: ignore[method-assign]
    with pytest.raises(ReasonerError, match="guard-valid"):
        await r.decide_step("apply", _live_map(), facts, [])


@pytest.mark.asyncio
async def test_dangling_value_ref_fails_closed() -> None:
    facts = _facts()

    async def fake_gen(system, prompt, schema):  # noqa: ARG001
        return {
            "scratch_reasoning": "fabricated ref",
            "action_kind": "fill",
            "target_index": "7",
            "value_ref": "fact-DOES-NOT-EXIST",  # dangling
            "irreversibility": "reversible",
            "irreversibility_rationale": "",
            "success_check": "field_nonempty",
            "say": "",
        }

    r = GeminiReasoner(api_key="k")
    r._generate_json = fake_gen  # type: ignore[method-assign]
    with pytest.raises(ReasonerError):
        await r.decide_step("fill it", _live_map(), facts, [])


# ---------------------------------------------------------------------------
# 6. plan_goal: generic plan, done_check clamped to the enum.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_goal_decodes_and_clamps_done_check() -> None:
    async def fake_gen(system, prompt, schema):  # noqa: ARG001
        return [
            {"description": "find the amount due", "done_check": "confirmation_fact"},
            {"description": "open the pay page", "done_check": "not_a_real_check"},
        ]

    r = GeminiReasoner(api_key="k")
    r._generate_json = fake_gen  # type: ignore[method-assign]
    orient = PageReadout(title="Bill", url="https://x", summary="a bill page")
    plan = await r.plan_goal("pay my bill", orient, [])

    assert [s.description for s in plan] == ["find the amount due", "open the pay page"]
    assert plan[0].done_check == "confirmation_fact"
    # An invalid done_check is clamped to "" (never a fabricated check name).
    assert plan[1].done_check == ""
    assert r.plan_calls == ["pay my bill"]


@pytest.mark.asyncio
async def test_plan_goal_accepts_subgoal_key_not_just_description() -> None:
    """REGRESSION (live 2026-06-07): MiniMax-M3 emits the plan with the key
    ``"subgoal"`` instead of the schema's ``"description"``. The parser read only
    ``"description"`` → every subgoal came back EMPTY (``plan=['','','','']``),
    leaving the decider planless so it free-formed on the homepage carousel and
    never navigated. The description key must be tolerant; an item with no usable
    text under ANY known key is dropped, not appended empty."""
    async def fake_gen(system, prompt, schema):  # noqa: ARG001
        return [
            {"subgoal": "type Point Reyes in the search field and submit",
             "done_check": "navigated"},
            {"subgoal": "open the Point Reyes campground result", "done_check": "navigated"},
            {"done_check": "navigated"},  # no description under any key → dropped
        ]

    r = GeminiReasoner(api_key="k")
    r._generate_json = fake_gen  # type: ignore[method-assign]
    orient = PageReadout(title="Rec", url="https://recreation.gov", summary="home")
    plan = await r.plan_goal("reserve Point Reyes", orient, [])

    assert [s.description for s in plan] == [
        "type Point Reyes in the search field and submit",
        "open the Point Reyes campground result",
    ]
    assert all(s.done_check == "navigated" for s in plan)
