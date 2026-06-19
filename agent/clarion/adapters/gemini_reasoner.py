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
import time
from typing import Any, Optional

from clarion.contracts.ports import Reasoner
from clarion.contracts.state import (
    DecideContext,
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

# A plan subgoal's description arrives under different JSON KEYS depending on the
# model: the schema asks for "description", but MiniMax-M3 (which doesn't honor the
# strict schema) returns "subgoal". These are alternate keys for the SAME field —
# accepting them is parse-robustness, NOT a semantic keyword heuristic (the banned
# anti-pattern). Without it a real plan parsed to EMPTY strings and the decider ran
# with no plan. Ordered by likelihood; first non-empty wins. Shared by both adapters.
_SUBGOAL_DESC_KEYS: tuple[str, ...] = (
    "description",
    "subgoal",
    "goal",
    "step",
    "task",
    "text",
)


def _subgoal_text(item: dict) -> str:
    """The subgoal description from a plan item, tolerant of the key the model used
    (see ``_SUBGOAL_DESC_KEYS``). Returns ``''`` if none carries usable text — the
    caller then DROPS the item rather than appending an empty subgoal."""
    for key in _SUBGOAL_DESC_KEYS:
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


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
            "alternatives",
            "value_ref",
            "irreversibility",
            "success_check",
            "say",
            "asserts_absence",
        ],
        "required": [
            "scratch_reasoning",
            "action_kind",
            "target_index",
            "value_ref",
            "irreversibility",
            "success_check",
            "say",
        ],
        "properties": {
            "scratch_reasoning": {
                "type": "STRING",
                "description": (
                    "Reason FIRST, before choosing — but in ONE short clause "
                    "(~15 words MAX): which numbered item serves the goal next and "
                    "whether it is reversible. Audit only; never spoken. Do NOT write "
                    "paragraphs — brevity here is a hard latency budget."
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
            "alternatives": {
                "type": "ARRAY",
                "items": {"type": "STRING", "enum": [str(i) for i in live_indices]},
                "description": (
                    "OTHER live numbered items (besides target_index) the goal ALSO "
                    "plausibly matches — each MUST be one of the listed live indices. "
                    "Leave EMPTY when only one control fits; list the rivals when the "
                    "goal could mean more than one distinct control, so the user can "
                    "be asked which they meant rather than guessed at."
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
            "success_check": {"type": "STRING", "enum": list(SUCCESS_CHECKS)},
            "say": {
                "type": "STRING",
                "description": (
                    "What the voice plane speaks — copied VERBATIM from a grounded "
                    "fact value, or empty for a silent step. Never paraphrase."
                ),
            },
            "asserts_absence": {
                "type": "BOOLEAN",
                "description": (
                    "TRUE only when 'say' ASSERTS THAT SOMETHING IS ABSENT / a "
                    "negative ('no late fee', 'no autopay enrolled', 'nothing is "
                    "due') rather than reading back a present value. Such a claim "
                    "is routed through a closed-world check that hedges unless the "
                    "absence was actually read off the page — so flag it honestly. "
                    "FALSE for any positive read-back."
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
    "CHOOSE THE action_kind BY WHAT THE STEP ACTUALLY NEEDS:\n"
    "  - 'read': ONLY when the user wants to KNOW something that the grounded facts "
    "/ current page already answer, or you must report what is there. A read NEVER "
    "changes the page.\n"
    "  - 'navigate' / 'click': when the user wants to GO somewhere or OPEN / SELECT "
    "a control (a link, button, or tab). If the user's request is to open / go to / "
    "show / find a section and a matching link or button is in the numbered items, "
    "you MUST click or navigate it — do NOT merely read its name back. Reading the "
    "label of the thing they asked to open does NOT satisfy the goal.\n"
    "  - 'fill': to enter a value into an input field.\n"
    "Use the CURRENT PHASE's done-check as your target: if it is 'navigated' you "
    "must move the page (click/navigate), not read. If WHAT JUST HAPPENED shows the "
    "previous step was a read and the subgoal is still not done, do NOT read again "
    "— act on the matching control.\n"
    "Reason FIRST in scratch_reasoning — but ONE short clause only, no paragraphs "
    "(it is a latency budget) — THEN choose. target_index must be one of "
    "the numbered live items; value_ref must be one of the listed fact ids (or "
    "null). Pick success_check by name from the allowed set.\n"
    "If the goal plausibly matches MORE THAN ONE distinct control on the page, set "
    "'alternatives' to the OTHER plausible target indices (besides target_index) "
    "and PREFER ASKING the user which they meant over guessing; otherwise leave "
    "'alternatives' empty.\n"
    "Set 'asserts_absence' TRUE only when your 'say' asserts that something is NOT "
    "present / a negative ('no late fee', 'no autopay enrolled'); FALSE for any "
    "positive read-back. A flagged negative is hedged unless the absence was "
    "actually read off the page, so report this polarity honestly."
)

_PLAN_SYSTEM = (
    "You are the planning core of a voice co-pilot for blind users on the open web. "
    "Given the user's ACTUAL request and what a screen reader sees on the CURRENT "
    "page, produce the SPECIFIC plan to accomplish exactly what they asked — an "
    "ordered list of subgoals. Be concrete: name the real target the user referred "
    "to and the real controls/sections you can see on the page (e.g. 'open the Food "
    "assistance section'), not a vague paraphrase. Do not strip the user's "
    "specifics. The only thing to stay generic about is page structure you have NOT "
    "seen yet — never invent steps for a page you cannot observe.\n"
    "RIGHT-SIZE THE PLAN to the request:\n"
    "  - A question you can answer from the current page → ONE subgoal that reads "
    "the answer.\n"
    "  - A request to go to / open something → one subgoal per real navigation "
    "milestone.\n"
    "  - Filling a form → ONE subgoal for the whole form ('complete the form'); the "
    "individual fields are steps inside it, NOT separate subgoals. Only split out a "
    "field that itself needs multiple steps (a date-picker, a searchable dropdown, a "
    "multi-page wizard).\n"
    "Each subgoal names a registered done_check from the allowed set — the "
    "code-checkable milestone that proves it is done."
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


def _render_context(ctx: Optional[DecideContext]) -> str:
    """Render the rich situational frame the step-decider reasons inside: the
    user's VERBATIM request, the plan phase, the live page, and what just happened.
    Empty string when no context is supplied (a bare unit-test fake)."""
    if ctx is None:
        return ""
    lines = [
        f"THE USER ACTUALLY ASKED (verbatim — this is the real intent): "
        f"{ctx.user_intent!r}",
    ]
    if ctx.plan:
        plan = "; ".join(f"{i + 1}. {d}" for i, d in enumerate(ctx.plan))
        lines.append(f"FULL PLAN: {plan}")
    lines.append(
        f"CURRENT PHASE: subgoal {ctx.subgoal_index + 1} of {ctx.subgoal_total} — "
        f"{ctx.subgoal_description!r} "
        f"(this subgoal is DONE when the check '{ctx.subgoal_done_check or 'n/a'}' "
        f"passes)"
    )
    if ctx.last_outcome:
        lines.append(f"WHAT JUST HAPPENED: {ctx.last_outcome}")
    if ctx.page_title or ctx.page_url:
        lines.append(f"CURRENT PAGE: {ctx.page_title!r} ({ctx.page_url})")
    if ctx.page_summary:
        lines.append(f"WHAT A SCREEN READER SEES NOW: {ctx.page_summary}")
    if ctx.recall_hint:
        lines.append(
            f"MEMORY (advisory only — re-ground on the live page, never trust "
            f"blindly): {ctx.recall_hint}"
        )
    return "\n".join(lines)


def _decide_prompt(
    goal: str,
    ranked_slice: SelectorMap,
    facts: list[Fact],
    history: list[StepProposal],
    *,
    retry_error: str | None = None,
    context: Optional[DecideContext] = None,
) -> str:
    parts: list[str] = []
    ctx_block = _render_context(context)
    if ctx_block:
        parts += [ctx_block, ""]
    parts += [
        f"IMMEDIATE STEP GOAL: {goal}",
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


def _coerce_alternatives(raw: Any) -> list[int]:
    """Coerce the model's ``alternatives`` (a JSON array of string-encoded live
    indices) back to a list of ints, dropping anything that isn't an int. Liberal —
    the kernel re-filters against the live map; this only shapes."""
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for item in raw:
        coerced = _coerce_index(item)
        if coerced is not None:
            out.append(coerced)
    return out


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
        # The model's self-reported ambiguity: other plausible target indices.
        # Default empty (the unambiguous case); the kernel re-filters to live ids.
        alternatives=_coerce_alternatives(payload.get("alternatives")),
        # The model's self-reported polarity: does 'say' assert an absence/negative?
        # Default False (a positive read-back); routes negatives to the verifier.
        asserts_absence=bool(payload.get("asserts_absence", False)),
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


# The exact-prompt dump goes to its OWN file (default /tmp/clarion-prompts.log) so
# the full system + composed-context message per LLM call is easy to read without
# burying the worker log. Set CLARION_PROMPT_LOG='' to disable.
_PROMPT_LOG_PATH = os.environ.get("CLARION_PROMPT_LOG", "/tmp/clarion-prompts.log")


def _log_prompt(
    kind: str,
    seq: int,
    system: str,
    user: str,
    response: Optional[str] = None,
    *,
    embedded_schema: bool = False,
) -> tuple[bool, int]:
    """Append the EXACT prompt one LLM call received — the system prompt + the
    fully-composed user/context message (and its raw response) — to a dedicated,
    human-readable file (``CLARION_PROMPT_LOG``). This is the "show me what the
    model actually got + how the context was composed" log: full fidelity, clearly
    delimited, kept OUT of the worker log so neither clutters the other. Returns
    ``(written, sys+user char count)`` so the caller can drop a one-line pointer
    into the worker log. Best-effort — never raises, never blocks a decision."""
    if not _PROMPT_LOG_PATH:
        return (False, 0)
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        bar = "=" * 80
        tag = f"[{ts}] {kind.upper()} #{seq}"
        if embedded_schema:
            tag += "  (schema embedded in user msg)"
        block = [
            bar,
            tag,
            "---------------------------------- SYSTEM ----------------------------------",
            system,
            "-------------------------- USER (composed context) -------------------------",
            user,
        ]
        if response is not None:
            block += [
                "----------------------------- RESPONSE (raw) -------------------------------",
                response,
            ]
        block += [bar, ""]
        with open(_PROMPT_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write("\n".join(block) + "\n")
        return (True, len(system) + len(user))
    except Exception:  # noqa: BLE001 - observability must never break a decision
        return (False, 0)


def _log_decide(
    ctx: Optional[DecideContext],
    proposal: StepProposal,
    n_items: int,
    n_facts: int,
    ms: Optional[float],
) -> None:
    """Print the FULL decide trace to stdout (→ /tmp/clarion-worker.log): exactly
    what the step-decider was given and what it chose. This is the no-truncation
    behavioural trace — the worker log is the dev log, so it is always on. The
    adapters only run live (tests use ``FakeReasoner``), so this never touches the
    network-free gate. Best-effort — never breaks a decision."""
    try:
        intent = (ctx.user_intent if ctx else "") or ""
        phase = (
            f"{ctx.subgoal_index + 1}/{ctx.subgoal_total}:{ctx.subgoal_description}"
            if ctx
            else ""
        )
        page = (ctx.page_title if ctx else "") or ""
        check = (ctx.subgoal_done_check if ctx else "") or ""
        ms_s = f"{ms:.0f}" if ms is not None else "?"
        print(
            f"  [decide-ctx] intent={intent!r} phase={phase!r} "
            f"done_check={check!r} page={page!r} items={n_items} facts={n_facts}",
            flush=True,
        )
        print(
            f"  [decide-out] action={proposal.action_kind} "
            f"target={proposal.target_index} value_ref={proposal.value_ref} "
            f"check={proposal.success_check} irrev={proposal.irreversibility} "
            f"ms={ms_s}",
            flush=True,
        )
        if proposal.scratch_reasoning:
            print(f"  [decide-why] {proposal.scratch_reasoning}", flush=True)
    except Exception:  # noqa: BLE001 - tracing must never break a decision
        pass


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
            # Accept bare description STRINGS as well as objects — a model that
            # returns ["search for X", …] should not collapse to the one-subgoal
            # fallback (parity with OpenAIReasoner.plan_goal).
            if isinstance(item, str):
                desc = item.strip()
                if desc:
                    subgoals.append(Subgoal(description=desc, done_check=""))
                continue
            if not isinstance(item, dict):
                continue
            done = item.get("done_check", "")
            if done not in SUCCESS_CHECKS:
                done = ""
            # Accept "subgoal"/synonyms as well as "description" — an alternate JSON
            # key for the SAME field (a model that ignores the schema must not parse
            # to empty subgoals). Parity with OpenAIReasoner.plan_goal.
            desc = _subgoal_text(item)
            if desc:
                subgoals.append(Subgoal(description=desc, done_check=done))
        return subgoals

    async def decide_step(
        self,
        goal: str,
        ranked_slice: SelectorMap,
        facts: list[Fact],
        history: list[StepProposal],
        context: DecideContext | None = None,
    ) -> StepProposal:
        """Decide the next grounded step. Structured output → post-decode guard →
        single re-ask on reject → fail-closed (``ReasonerError``). NEVER returns an
        invalid (off-page / dangling) proposal. ``context`` is the rich situational
        frame (verbatim intent, plan phase, live page) the decision is made inside."""
        self.decide_calls.append(goal)
        import time

        live_indices = sorted(ranked_slice.nodes)
        fact_ids = [f.id for f in facts]
        schema = _step_schema(live_indices, fact_ids)

        t0 = time.perf_counter()
        data = await self._generate_json(
            _DECIDE_SYSTEM,
            _decide_prompt(goal, ranked_slice, facts, history, context=context),
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
                    goal,
                    ranked_slice,
                    facts,
                    history,
                    retry_error=verdict.reason,
                    context=context,
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
        _log_decide(context, proposal, len(ranked_slice.nodes), len(facts), self.last_decide_ms)
        return proposal


__all__ = ["GeminiReasoner", "ReasonerError", "SUCCESS_CHECKS"]
