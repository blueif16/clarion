"""Unit tests for the ``OpenAIReasoner`` adapter (Qwen via Nebius) — NETWORK-FREE.

The real Nebius/Qwen call is the ONLY thing stubbed: tests either inject a fake
``_generate_json`` (to exercise decode + the guard fence) or a fake ``openai``
client object (to exercise the strict-json_schema → json_object fallback). No key,
no socket. The live A/B against real usa.gov is gated behind ``-m live`` (and
requires ``NEBIUS_API_KEY``).

Coverage:
  1. ABC conformance + construct-only (no client, no network at import).
  2. Missing NEBIUS_API_KEY raises (not a network error).
  3. Clean decode → guard-valid ``StepProposal``; ``say`` forced verbatim.
  4. Post-decode FENCE: off-page index → one re-ask → recover; persistent → fail.
  5. The strict-json_schema → json_object FALLBACK fires on an unsupported-format
     4xx, is remembered, and a subsequent call goes straight to json_object.
  6. ``_strip_fence`` unwraps a ```json fence; ``_strictify`` adds
     additionalProperties:false.
"""

from __future__ import annotations

import pytest

from clarion.adapters.gemini_reasoner import ReasonerError
from clarion.adapters.openai_reasoner import (
    OpenAIReasoner,
    _looks_like_format_unsupported,
    _strictify,
    _strip_fence,
)
from clarion.contracts.ports import Reasoner
from clarion.contracts.state import AxNode, Fact, SelectorMap


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
# 1 + 2. ABC conformance, construct-only, missing-key raise.
# ---------------------------------------------------------------------------


def test_is_reasoner_and_construct_only_builds_no_client() -> None:
    r = OpenAIReasoner(api_key="unit-test-key", model="Qwen/Qwen3.5-397B-A17B-fast")
    assert isinstance(r, Reasoner)
    assert r.model == "Qwen/Qwen3.5-397B-A17B-fast"
    assert r.base_url.startswith("https://api.tokenfactory")
    assert r._client is None  # lazy
    assert r.last_decide_ms is None


def test_ensure_client_without_key_raises_not_network() -> None:
    r = OpenAIReasoner(api_key="x")
    r._api_key = None
    with pytest.raises(RuntimeError, match="NEBIUS_API_KEY"):
        r._ensure_client()


# ---------------------------------------------------------------------------
# 3. Clean decode → guard-valid; say forced verbatim from the ref Fact.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decide_step_clean_decode_grounds_say_verbatim() -> None:
    facts = _facts()
    fid = facts[0].id

    async def fake_gen(system, prompt, schema):  # noqa: ARG001
        return {
            "scratch_reasoning": "amount-due answers the goal",
            "action_kind": "read",
            "target_index": "3",
            "value_ref": fid,
            "irreversibility": "reversible",
            "irreversibility_rationale": "a read changes nothing",
            "success_check": "confirmation_fact",
            "say": "you owe eighty four dollars",  # paraphrase — must be overwritten
        }

    r = OpenAIReasoner(api_key="k")
    r._generate_json = fake_gen  # type: ignore[method-assign]
    proposal = await r.decide_step("find the amount due", _live_map(), facts, [])

    assert proposal.target_index == 3
    assert proposal.value_ref == fid
    assert proposal.say == "Amount due: $84.32"  # verbatim grounded span
    assert r.last_decide_ms is not None and r.last_decide_ms >= 0.0


# ---------------------------------------------------------------------------
# 4. Post-decode FENCE.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offpage_index_triggers_reask_then_recovers() -> None:
    facts = _facts()
    fid = facts[0].id
    calls = {"n": 0}

    async def fake_gen(system, prompt, schema):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "scratch_reasoning": "guess",
                "action_kind": "read",
                "target_index": "999",  # off-page
                "value_ref": fid,
                "irreversibility": "reversible",
                "irreversibility_rationale": "",
                "success_check": "confirmation_fact",
                "say": "",
            }
        assert "rejected" in prompt.lower()
        return {
            "scratch_reasoning": "real index now",
            "action_kind": "read",
            "target_index": "3",
            "value_ref": fid,
            "irreversibility": "reversible",
            "irreversibility_rationale": "",
            "success_check": "confirmation_fact",
            "say": "",
        }

    r = OpenAIReasoner(api_key="k")
    r._generate_json = fake_gen  # type: ignore[method-assign]
    proposal = await r.decide_step("find amount", _live_map(), facts, [])
    assert calls["n"] == 2
    assert proposal.target_index == 3


@pytest.mark.asyncio
async def test_persistent_dangling_ref_fails_closed() -> None:
    facts = _facts()

    async def fake_gen(system, prompt, schema):  # noqa: ARG001
        return {
            "scratch_reasoning": "fabricated",
            "action_kind": "fill",
            "target_index": "7",
            "value_ref": "fact-NOPE",  # dangling, every time
            "irreversibility": "reversible",
            "irreversibility_rationale": "",
            "success_check": "field_nonempty",
            "say": "",
        }

    r = OpenAIReasoner(api_key="k")
    r._generate_json = fake_gen  # type: ignore[method-assign]
    with pytest.raises(ReasonerError, match="guard-valid"):
        await r.decide_step("fill it", _live_map(), facts, [])


# ---------------------------------------------------------------------------
# 5. The strict-json_schema → json_object FALLBACK (fake openai client).
# ---------------------------------------------------------------------------


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class _BadRequest(Exception):
    """Mimics an openai.BadRequestError naming json_schema as unsupported."""

    status_code = 400

    def __str__(self) -> str:  # noqa: D401
        return "400 invalid_request: response_format json_schema is not supported"


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        rf = kwargs.get("response_format", {})
        if rf.get("type") == "json_schema":
            raise _BadRequest()  # endpoint can't do strict json_schema
        # json_object path: return a valid step JSON.
        return _Resp(
            '{"scratch_reasoning":"ok","action_kind":"read","target_index":"3",'
            '"value_ref":"null","irreversibility":"reversible",'
            '"irreversibility_rationale":"","success_check":"navigated","say":""}'
        )


class _FakeChat:
    def __init__(self, completions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self) -> None:
        self.chat = _FakeChat(_FakeCompletions())


@pytest.mark.asyncio
async def test_json_schema_unsupported_falls_back_to_json_object_and_is_cached() -> None:
    r = OpenAIReasoner(api_key="k")
    r._client = _FakeClient()  # inject the fake client directly (bypass lazy build)

    proposal = await r.decide_step("find amount", _live_map(), _facts(), [])
    # The fallback produced a guard-valid step from the json_object path.
    assert proposal.target_index == 3
    assert r.last_used_fallback is True
    assert r._use_json_object_fallback is True

    calls = r._client.chat.completions.calls
    # First call tried json_schema (rejected), second used json_object.
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"]["type"] == "json_object"

    # A SECOND decide goes STRAIGHT to json_object (no wasted json_schema attempt).
    before = len(calls)
    await r.decide_step("again", _live_map(), _facts(), [])
    new = calls[before:]
    assert all(c["response_format"]["type"] == "json_object" for c in new)


# ---------------------------------------------------------------------------
# 6. Pure utility units.
# ---------------------------------------------------------------------------


def test_strip_fence_unwraps_json_block() -> None:
    fenced = '```json\n{"a": 1}\n```'
    assert _strip_fence(fenced) == '{"a": 1}'
    assert _strip_fence('{"a": 1}') == '{"a": 1}'


def test_strictify_adds_additional_properties_false_recursively() -> None:
    schema = {
        "type": "OBJECT",
        "properties": {
            "x": {"type": "STRING"},
            "nested": {"type": "OBJECT", "properties": {"y": {"type": "STRING"}}},
        },
    }
    out = _strictify(schema)
    assert out["additionalProperties"] is False
    assert out["properties"]["nested"]["additionalProperties"] is False


def test_format_unsupported_only_on_bad_request() -> None:
    assert _looks_like_format_unsupported(_BadRequest()) is True
    # A 500 / network error is NOT a fallback trigger (it re-raises).
    other = RuntimeError("503 service unavailable")
    assert _looks_like_format_unsupported(other) is False
