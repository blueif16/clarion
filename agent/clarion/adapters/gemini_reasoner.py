"""The ``GeminiReasoner`` adapter — the ONLY new LLM home in the system
(architecture Components / ``GeminiReasoner``).

One generic LLM **reasons** the plan and the next grounded action behind the
frozen ``Reasoner`` port; the deterministic kernel **acts** and enforces the two
invariants in code. This adapter is the de-hardcoding boundary's provider impl:
the Gemini SDK lives ONLY here, so ``contracts/`` and ``kernel/`` stay
provider-SDK-free (foundation §6 / project CLAUDE.md).

Provider truth (project CLAUDE.md): Gemini via **AI Studio**, model
**gemini-3.5-flash** (env ``GEMINI_MODEL``), key ``GOOGLE_API_KEY``. Construction
mirrors ``tts_vertex.VertexExpressSynthesizer``: config is resolved eagerly
(cheap, no I/O) but the SDK client is built LAZILY on first use, so the adapter is
importable / constructible in a headless no-network env (the unit tests construct
it without a key and assert no client is built).

The four non-negotiables this adapter implements (architecture Components):

  1. **Structured output** — ``response_mime_type='application/json'`` +
     ``response_schema`` built per-call so the model emits a ``StepProposal`` /
     ``list[Subgoal]`` shape directly (decoded via the genai schema, not regex).
  2. **``scratch_reasoning`` produced FIRST** — the schema's ``propertyOrdering``
     forces ``scratch_reasoning`` as the first generated property (reason BEFORE
     committing to an index — the architecture's explicit requirement).
  3. **Per-call enums of LIVE ids** — ``target_index`` is constrained to the live
     ``ranked_slice`` indices, ``value_ref`` to the real ``Fact.id`` enum (the
     "per-call enum of 50+ live ids" research point). Structured output is NOT a
     logit mask, though, so:
  4. **Post-decode fence** — after decode we run ``kernel.reasoner_guard``
     (``validate_step_proposal``) against the LIVE map + facts. On reject we
     re-ask ONCE with the error, then **fail closed** (raise ``ReasonerError`` the
     kernel can catch) — an invalid proposal NEVER passes through.

``say`` is copied **verbatim from a grounded fact span**, never generated: after
a clean decode we prefer the resolved ``value_ref`` Fact's value, and we reject
any ``say`` that is not a byte-substring of some grounded fact text.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Optional

from clarion.contracts.ports import Reasoner
from clarion.contracts.state import (
    Fact,
    PageReadout,
    SelectorMap,
    StepProposal,
    Subgoal,
)
from clarion.kernel.reasoner_guard import (
    resolve_value_ref,
    validate_step_proposal,
)

_DEFAULT_MODEL = "gemini-3.5-flash"

# The canonical success-check enum (shared with AG-DONE, who implements the
# CODE-side checks). The model SELECTS one by name — never invents a check, never
# self-grades (killer-closer #3). Kept here so the schema can enum-constrain it.
SUCCESS_CHECKS: tuple[str, ...] = (
    "field_nonempty",
    "node_added",
    "error_absent",
    "navigated",
    "confirmation_fact",
)

_ACTION_KINDS: tuple[str, ...] = ("click", "fill", "navigate", "read")
_IRREVERSIBILITY: tuple[str, ...] = ("reversible", "irreversible", "unknown")


class ReasonerError(RuntimeError):
    """Typed fail-closed signal the kernel can catch. Raised when the model's
    proposal still fails the post-decode guard AFTER the single re-ask — so the
    kernel discards + replans (or surfaces a safe read-back) rather than ever
    acting on an off-page index / dangling value_ref."""


# ---------------------------------------------------------------------------
# Per-call schemas — built from the LIVE map + facts so the index / value_ref /
# success_check fields are ENUMS over real ids (architecture "per-call enum of
# 50+ live ids"). Plain dict schemas (OpenAPI-subset) so we can set
# ``propertyOrdering`` and inline the live enums — the genai SDK accepts a dict
# ``response_schema`` (validated against the Python SDK docs, Context7).
# ---------------------------------------------------------------------------


def _step_schema(live_indices: list[int], fact_ids: list[str]) -> dict[str, Any]:
    """The ``StepProposal`` response schema for ONE ``decide_step`` call.

    ``scratch_reasoning`` is FIRST in ``propertyOrdering`` (reason-before-point).
    ``target_index`` enumerates the live ranked_slice indices; ``value_ref``
    enumerates the real Fact ids PLUS a literal ``"null"`` sentinel (so a click
    can carry no value — Gemini's enum strings can't express JSON null, so we use
    a sentinel and map it back to ``None`` on decode)."""
    # Indices arrive as ints; the OpenAPI enum is string-typed, so we enum the
    # string forms and coerce back to int on decode.
    index_enum = [str(i) for i in live_indices] + ["null"]
    value_enum = list(fact_ids) + ["null"]
    return {
        "type": "OBJECT",
        "propertyOrdering": [
            "scratch_reasoning",
            "action_kind",
            "target_index",
            "value_ref",
            "irreversibility",
            "irreversibility_rationale",
            "success_check",
            "say",
        ],
        "required": [
            "scratch_reasoning",
            "action_kind",
            "target_index",
            "value_ref",
            "irreversibility",
            "irreversibility_rationale",
            "success_check",
            "say",
        ],
        "properties": {
            "scratch_reasoning": {
                "type": "STRING",
                "description": (
                    "Reason FIRST, before choosing an index: what the goal needs "
                    "next, which numbered item serves it, and why it is reversible "
                    "or not. Audit only; never spoken."
                ),
            },
            "action_kind": {"type": "STRING", "enum": list(_ACTION_KINDS)},
            "target_index": {
                "type": "STRING",
                "enum": index_enum,
                "description": (
                    "The numbered item to act on — MUST be one of the listed live "
                    "indices, or 'null' for an action that needs no node."
                ),
            },
            "value_ref": {
                "type": "STRING",
                "enum": value_enum,
                "description": (
                    "The id of the grounded fact whose value to fill/speak — MUST "
                    "be one of the listed fact ids, or 'null' for a value-less "
                    "click. NEVER invent a value."
                ),
            },
            "irreversibility": {"type": "STRING", "enum": list(_IRREVERSIBILITY)},
            "irreversibility_rationale": {"type": "STRING"},
            "success_check": {"type": "STRING", "enum": list(SUCCESS_CHECKS)},
            "say": {
                "type": "STRING",
                "description": (
                    "What the voice plane speaks — copied VERBATIM from a grounded "
                    "fact value, or empty for a silent step. Never paraphrase."
                ),
            },
        },
    }


def _plan_schema() -> dict[str, Any]:
    """The ``list[Subgoal]`` response schema for ``plan_goal`` — a generic,
    site-agnostic plan. ``done_check`` is enum-constrained to the success-check
    selection (a SELECTION, never model say-so)."""
    return {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "propertyOrdering": ["description", "done_check"],
            "required": ["description", "done_check"],
            "properties": {
                "description": {
                    "type": "STRING",
                    "description": (
                        "A short, generic, site-agnostic step intent "
                        "(e.g. 'find the amount due')."
                    ),
                },
                "done_check": {
                    "type": "STRING",
                    "enum": list(SUCCESS_CHECKS),
                    "description": "The registered check that certifies this step done.",
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# Prompt builders — present the NUMBERED ranked_slice (so target_index is a real
# live index) and the facts WITH their ids (so value_ref is a real id).
# ---------------------------------------------------------------------------

_DECIDE_SYSTEM = (
    "You are the reasoning core of a voice co-pilot that lets a blind person finish "
    "a private web task themselves. You NEVER act on a page directly — you only "
    "propose the single next step as structured JSON, and a deterministic kernel "
    "enforces consent + grounding. Two hard rules:\n"
    "  1. NO FACT WITHOUT A SOURCE. The 'say' line and any filled value MUST be "
    "copied verbatim from one of the grounded facts listed below. Never invent or "
    "paraphrase a value; if nothing grounded answers, say nothing (empty 'say').\n"
    "  2. NO ACTION WITHOUT A YES. Judge irreversibility honestly. If a control "
    "might submit, send, pay, confirm, or navigate off-site and you are not sure it "
    "is undoable, mark it 'irreversible' or 'unknown' — never downgrade a risky "
    "control to 'reversible'.\n"
    "Reason FIRST in scratch_reasoning, THEN choose. target_index must be one of "
    "the numbered live items; value_ref must be one of the listed fact ids (or "
    "null). Pick success_check by name from the allowed set."
)

_PLAN_SYSTEM = (
    "You are the planning core of a voice co-pilot for blind users on the open web. "
    "Given a goal and what a screen reader sees on the CURRENT page, produce a "
    "short, GENERIC, site-agnostic plan: an ordered list of subgoals. No "
    "site-specific names, no assumptions about a page you haven't seen. Each "
    "subgoal names a registered done_check from the allowed set."
)


def _render_slice(ranked_slice: SelectorMap) -> str:
    lines = []
    for idx in sorted(ranked_slice.nodes):
        n = ranked_slice.nodes[idx]
        state = "".join(
            f" [{k}]" for k, v in (n.state or {}).items() if v
        )
        lines.append(f"  [{idx}] {n.role} {n.name!r}{state}")
    return "\n".join(lines) if lines else "  (no interactive items on this page)"


def _render_facts(facts: list[Fact]) -> str:
    lines = []
    for f in facts:
        pol = "" if f.polarity == "present" else " (ABSENT)"
        lines.append(f"  id={f.id} value={f.value!r}{pol}")
    return "\n".join(lines) if lines else "  (no grounded facts available)"


def _render_history(history: list[StepProposal]) -> str:
    if not history:
        return "  (no prior steps)"
    lines = []
    for i, h in enumerate(history, 1):
        lines.append(
            f"  {i}. {h.action_kind} index={h.target_index} "
            f"value_ref={h.value_ref} say={h.say!r}"
        )
    return "\n".join(lines)


def _decide_prompt(
    goal: str,
    ranked_slice: SelectorMap,
    facts: list[Fact],
    history: list[StepProposal],
    *,
    retry_error: str | None = None,
) -> str:
    parts = [
        f"GOAL: {goal}",
        "",
        "NUMBERED ITEMS YOU CAN ACT ON (target_index MUST be one of these):",
        _render_slice(ranked_slice),
        "",
        "GROUNDED FACTS (value_ref MUST be one of these ids; 'say' MUST be a "
        "verbatim value):",
        _render_facts(facts),
        "",
        "STEPS ALREADY TAKEN:",
        _render_history(history),
        "",
        f"ALLOWED success_check values: {', '.join(SUCCESS_CHECKS)}",
    ]
    if retry_error:
        parts += [
            "",
            "YOUR PREVIOUS PROPOSAL WAS REJECTED by the code guard:",
            f"  {retry_error}",
            "Choose a target_index that is in the live list and a value_ref that "
            "is a listed fact id (or null). Try again.",
        ]
    parts += ["", "Decide the single next step now."]
    return "\n".join(parts)


def _plan_prompt(goal: str, orient: PageReadout, affordances: list[Fact]) -> str:
    aff = "\n".join(f"  - {f.value!r}" for f in affordances) or "  (none)"
    return "\n".join(
        [
            f"GOAL: {goal}",
            "",
            f"CURRENT PAGE: {orient.title!r} ({orient.url})",
            f"SCREEN-READER SUMMARY: {orient.summary}",
            "",
            "AFFORDANCES (controls the page offers):",
            aff,
            "",
            f"ALLOWED done_check values: {', '.join(SUCCESS_CHECKS)}",
            "",
            "Produce the generic, site-agnostic plan as a JSON array of subgoals.",
        ]
    )


# ---------------------------------------------------------------------------
# Decode helpers — coerce the genai JSON (string enums) back into the pure
# contract value objects, then run the kernel guard.
# ---------------------------------------------------------------------------


def _coerce_index(raw: Any) -> Optional[int]:
    if raw is None or raw == "null" or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _coerce_ref(raw: Any) -> Optional[str]:
    if raw is None or raw == "null" or raw == "":
        return None
    return str(raw)


def _decode_step(payload: dict[str, Any]) -> StepProposal:
    """Build a ``StepProposal`` from the decoded JSON, coercing the string enums
    (index, value_ref) back to their contract types. Liberal on unknown enum
    values — the guard is the truth, this only shapes."""
    action_kind = payload.get("action_kind", "read")
    if action_kind not in _ACTION_KINDS:
        action_kind = "read"
    irr = payload.get("irreversibility", "unknown")
    if irr not in _IRREVERSIBILITY:
        irr = "unknown"
    success_check = payload.get("success_check", "")
    if success_check not in SUCCESS_CHECKS:
        success_check = ""
    return StepProposal(
        scratch_reasoning=str(payload.get("scratch_reasoning", "")),
        action_kind=action_kind,  # type: ignore[arg-type]
        target_index=_coerce_index(payload.get("target_index")),
        value_ref=_coerce_ref(payload.get("value_ref")),
        irreversibility=irr,  # type: ignore[arg-type]
        irreversibility_rationale=str(payload.get("irreversibility_rationale", "")),
        success_check=success_check,
        say=str(payload.get("say", "")),
    )


def _ground_say(proposal: StepProposal, facts: list[Fact]) -> StepProposal:
    """Enforce verbatim-grounded ``say`` (epistemic clause fence #1). Prefer the
    resolved ``value_ref`` Fact's value as the spoken span; otherwise the model's
    ``say`` is kept ONLY if it is a byte-substring of some grounded fact text —
    else it is cleared (never speak an ungrounded span)."""
    resolved = resolve_value_ref(proposal.value_ref, facts)
    if resolved is not None:
        # The spoken value is the byte-identical grounded span the ref points at.
        return proposal.model_copy(update={"say": resolved.value})
    say = proposal.say.strip()
    if not say:
        return proposal
    for f in facts:
        if say in f.value:
            return proposal  # a verbatim substring of a grounded span — allowed.
    # Ungrounded restatement — clear it (the kernel speaks nothing, never a guess).
    return proposal.model_copy(update={"say": ""})


class GeminiReasoner(Reasoner):
    """The real ``Reasoner`` — Gemini structured output behind the frozen port.

    Lazy client (no I/O at import/construct). Model + key from env, ``load_dotenv``
    on construct so a script that imports this picks up ``agent/.env`` keys (the
    BEHAVIORAL-on-a-real-site rule). NEVER swaps the model to fix latency.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        # load_dotenv so keys resolve from agent/.env without an explicit export.
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:  # noqa: BLE001 - dotenv is optional; env may be exported
            pass
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get(
            "GEMINI_API_KEY"
        )
        self._model = model or os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)
        self._client = None  # built lazily in _ensure_client()
        # Observability parity with the fakes (tests/spike assert what was asked).
        self.plan_calls: list[str] = []
        self.decide_calls: list[str] = []
        # The decode latency of the LAST decide_step (ms) — the <800ms turn-budget
        # signal the spike reports. None until a call has run.
        self.last_decide_ms: float | None = None

    @property
    def model(self) -> str:
        return self._model

    def _ensure_client(self):
        if self._client is None:
            if not self._api_key:
                raise RuntimeError(
                    "GOOGLE_API_KEY (AI Studio key) is not set; cannot construct "
                    "the GeminiReasoner client."
                )
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def _generate_json(self, system: str, prompt: str, schema: dict) -> Any:
        """One structured-output round-trip → parsed JSON. The blocking genai call
        runs in a worker thread so it never blocks the event loop."""
        from google.genai import types

        client = self._ensure_client()
        model = self._model

        def _call() -> str:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,
                    # Keep it tight + deterministic for a decision, not prose.
                    temperature=0.0,
                    # THINKING OFF — a config knob on the FIXED model, NOT a swap.
                    # gemini-3.5-flash runs automatic thinking by default; the
                    # thinking-tokens dominated decode latency (36–121s observed).
                    # budget 0 = DISABLED (Context7 /googleapis/python-genai). The
                    # decision is a structured selection over a fenced enum, not a
                    # free chain-of-thought task — the audit-only scratch_reasoning
                    # field carries the "reason first" requirement instead.
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            return resp.text

        text = await asyncio.to_thread(_call)
        return json.loads(text)

    async def plan_goal(
        self,
        goal: str,
        orient: PageReadout,
        affordances: list[Fact],
    ) -> list[Subgoal]:
        self.plan_calls.append(goal)
        prompt = _plan_prompt(goal, orient, affordances)
        data = await self._generate_json(_PLAN_SYSTEM, prompt, _plan_schema())
        if not isinstance(data, list):
            data = []
        subgoals: list[Subgoal] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            done = item.get("done_check", "")
            if done not in SUCCESS_CHECKS:
                done = ""
            subgoals.append(
                Subgoal(description=str(item.get("description", "")), done_check=done)
            )
        return subgoals

    async def decide_step(
        self,
        goal: str,
        ranked_slice: SelectorMap,
        facts: list[Fact],
        history: list[StepProposal],
    ) -> StepProposal:
        """Decide the next grounded step. Structured output → post-decode guard →
        single re-ask on reject → fail-closed (``ReasonerError``). NEVER returns an
        invalid (off-page / dangling) proposal."""
        self.decide_calls.append(goal)
        import time

        live_indices = sorted(ranked_slice.nodes)
        fact_ids = [f.id for f in facts]
        schema = _step_schema(live_indices, fact_ids)

        t0 = time.perf_counter()
        data = await self._generate_json(
            _DECIDE_SYSTEM,
            _decide_prompt(goal, ranked_slice, facts, history),
            schema,
        )
        proposal = _ground_say(_decode_step(data), facts)
        # Post-decode fence #1.
        verdict = validate_step_proposal(proposal, ranked_slice, facts)
        if not verdict.ok:
            # Re-ask ONCE with the guard's reason, then fail closed.
            data = await self._generate_json(
                _DECIDE_SYSTEM,
                _decide_prompt(
                    goal, ranked_slice, facts, history, retry_error=verdict.reason
                ),
                schema,
            )
            proposal = _ground_say(_decode_step(data), facts)
            verdict = validate_step_proposal(proposal, ranked_slice, facts)
            if not verdict.ok:
                self.last_decide_ms = (time.perf_counter() - t0) * 1000.0
                raise ReasonerError(
                    "GeminiReasoner could not produce a guard-valid step after "
                    f"a re-ask: {verdict.reason}"
                )
        self.last_decide_ms = (time.perf_counter() - t0) * 1000.0
        return proposal


__all__ = ["GeminiReasoner", "ReasonerError", "SUCCESS_CHECKS"]
