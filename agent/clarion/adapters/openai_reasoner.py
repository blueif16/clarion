"""The ``OpenAIReasoner`` adapter — a SECOND ``Reasoner`` impl behind the frozen
port, against any OpenAI-compatible endpoint (here: Qwen via Nebius Token
Factory). The A/B counterpart to ``GeminiReasoner`` (latency vs structured-output
fidelity — the Reasoner is the load-bearing epistemic fence).

Provider config (the user's request):
  - ``base_url = "https://api.tokenfactory.us-central1.nebius.com/v1/"``
  - ``model    = "Qwen/Qwen3.5-397B-A17B-fast"``  (env ``NEBIUS_MODEL`` override)
  - key        ``NEBIUS_API_KEY``  (env; NEVER invented)

The ``openai`` SDK import lives ONLY here (foundation §6 / project CLAUDE.md);
``contracts/`` + ``kernel/`` stay SDK-free. Construction is pure config — the SDK
client is built LAZILY on first use, so the adapter is importable / constructible
without a key (the unit tests construct it without one and assert no client is
built).

**Same StepProposal shape as GeminiReasoner** — to keep the A/B honest this REUSES
the Gemini adapter's shared, provider-neutral building blocks (the per-call schema
with ``scratch_reasoning`` FIRST + live-index / Fact.id enums, the prompt builders,
the decode + verbatim-``say`` grounding, and the SAME ``kernel.reasoner_guard``
post-decode fence: one re-ask then fail-closed). Only the TRANSPORT differs.

Structured-output strategy (adaptive — Qwen may not honor strict json_schema):
  1. Try ``response_format={"type":"json_schema", json_schema:{... strict:true}}``.
  2. On a 4xx that names json_schema / response_format / strict (the endpoint
     can't do it), fall back ONCE to ``{"type":"json_object"}`` with the schema
     embedded in the prompt, and CACHE that the endpoint needs the fallback so we
     don't re-pay the failed round-trip on every call.
Either way the GUARD is the truth: ``validate_step_proposal`` fences off-page
indices / dangling value_refs regardless of how well the model honored the enum.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

from clarion.adapters.gemini_reasoner import (
    SUCCESS_CHECKS,
    ReasonerError,
    _decide_prompt,
    _PROMPT_LOG_PATH,
    _decode_step,
    _ground_say,
    _log_decide,
    _log_prompt,
    _plan_prompt,
    _plan_schema,
    _step_schema,
)
from clarion.contracts.ports import Reasoner
from clarion.contracts.state import (
    DecideContext,
    Fact,
    PageReadout,
    SelectorMap,
    StepProposal,
    Subgoal,
)
from clarion.kernel.reasoner_guard import validate_step_proposal

__all__ = ["OpenAIReasoner", "SUCCESS_CHECKS", "ReasonerError"]

_DEFAULT_BASE_URL = "https://api.tokenfactory.us-central1.nebius.com/v1/"
_DEFAULT_MODEL = "Qwen/Qwen3.5-397B-A17B-fast"

_DECIDE_SYSTEM = (
    "You are the reasoning core of a voice co-pilot that lets a blind person finish "
    "a private web task themselves. You NEVER act on a page directly — you only "
    "propose the single next step as STRICT JSON matching the given schema, and a "
    "deterministic kernel enforces consent + grounding. Two hard rules:\n"
    "  1. NO FACT WITHOUT A SOURCE. The 'say' line and any filled value MUST be "
    "copied verbatim from one of the grounded facts listed. Never invent or "
    "paraphrase a value; if nothing grounded answers, leave 'say' empty.\n"
    "  2. NO ACTION WITHOUT A YES. Judge irreversibility honestly; never downgrade "
    "a risky control (submit/send/pay/confirm/off-site nav) to 'reversible'.\n"
    "CHOOSE THE action_kind BY WHAT THE STEP ACTUALLY NEEDS:\n"
    "  - 'read': ONLY when the user wants to KNOW something the grounded facts / "
    "current page already answer, or you must report what is there. A read NEVER "
    "changes the page.\n"
    "  - 'navigate' / 'click': when the user wants to GO somewhere or OPEN / SELECT "
    "a control (a link, button, tab). If the request is to open / go to / show / "
    "find a section and a matching link or button is in the numbered items, you "
    "MUST click or navigate it — do NOT merely read its label back. Reading the "
    "name of the thing they asked to open does NOT satisfy the goal.\n"
    "  - 'fill': to enter a value into an input field.\n"
    "Use the CURRENT PHASE's done-check as your target: if it is 'navigated' you "
    "must move the page, not read. If WHAT JUST HAPPENED shows the previous step "
    "was a read and the subgoal is still not done, do NOT read again — act on the "
    "matching control.\n"
    "Put scratch_reasoning FIRST (ONE short clause only, no paragraphs — it is a "
    "latency budget), then choose. target_index MUST be one of the "
    "numbered live items; value_ref MUST be one of the listed fact ids (or the "
    "string 'null'). Pick success_check by name from the allowed set. "
    "If the goal plausibly matches MORE THAN ONE distinct control on the page, set "
    "'alternatives' to the OTHER plausible target indices (besides target_index) "
    "and PREFER ASKING the user which they meant over guessing; otherwise leave "
    "'alternatives' empty. "
    "Set 'asserts_absence' TRUE only when your 'say' asserts that something is NOT "
    "present / a negative ('no late fee', 'no autopay enrolled'); FALSE for any "
    "positive read-back — a flagged negative is hedged unless the absence was "
    "actually read off the page, so report this polarity honestly. "
    "Output ONLY the JSON object — no prose, no markdown fence."
)

_PLAN_SYSTEM = (
    "You are the planning core of a voice co-pilot for blind users on the open web. "
    "Given the user's ACTUAL request and what a screen reader sees on the CURRENT "
    "page, produce the SPECIFIC plan to accomplish exactly what they asked, as a "
    "STRICT JSON array of subgoals. Be concrete: name the real target the user "
    "referred to and the real controls/sections you can see (e.g. 'open the Food "
    "assistance section'), not a vague paraphrase; do not strip the user's "
    "specifics. Stay generic ONLY about page structure you have not seen yet — "
    "never invent steps for a page you cannot observe. RIGHT-SIZE the plan: a "
    "question answerable from the current page is ONE read subgoal; a form is ONE "
    "subgoal (its fields are steps, not subgoals) unless a field itself needs "
    "multiple steps. Each subgoal names a registered done_check from the allowed "
    "set. Output ONLY the JSON array — no prose, no markdown fence."
)


def _strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI strict json_schema requires ``additionalProperties:false`` on every
    object. The shared ``_step_schema`` / ``_plan_schema`` are OpenAPI-subset dicts
    (Gemini's flavor); add the OpenAI strict-mode key recursively. Pure, non-mutating."""
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    t = out.get("type")
    if t in ("OBJECT", "object"):
        out["additionalProperties"] = False
        out["properties"] = {
            k: _strictify(v) for k, v in (out.get("properties") or {}).items()
        }
    if t in ("ARRAY", "array") and "items" in out:
        out["items"] = _strictify(out["items"])
    return out


def _json_schema_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": _strictify(schema)},
    }


def _looks_like_format_unsupported(exc: Exception) -> bool:
    """Heuristic: does this error mean the endpoint can't do strict json_schema (so
    we should fall back to json_object), vs. a real failure to re-raise?"""
    msg = str(exc).lower()
    needles = (
        "response_format",
        "json_schema",
        "strict",
        "schema",
        "not supported",
        "unsupported",
        "invalid_request",
    )
    # Only treat 4xx-ish bad-request signals as a fallback trigger.
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    bad_request = status in (400, 422) or "400" in msg or "422" in msg
    return bad_request and any(n in msg for n in needles)


class OpenAIReasoner(Reasoner):
    """A ``Reasoner`` over an OpenAI-compatible chat endpoint (Qwen via Nebius).

    Lazy client; key from env (``NEBIUS_API_KEY``); ``load_dotenv`` on construct.
    Emits the SAME ``StepProposal`` shape as ``GeminiReasoner`` via the shared
    builders, and runs the SAME post-decode guard fence."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:  # noqa: BLE001 - dotenv optional; env may be exported
            pass
        self._api_key = api_key or os.environ.get("NEBIUS_API_KEY")
        self._model = model or os.environ.get("NEBIUS_MODEL", _DEFAULT_MODEL)
        self._base_url = base_url or os.environ.get(
            "NEBIUS_BASE_URL", _DEFAULT_BASE_URL
        )
        self._client = None  # built lazily
        # Provider-specific request body merged into every create() call (e.g.
        # MiniMax-M3's thinking-disable switch). Subclasses set this; None = omit.
        self._extra_body: dict[str, Any] | None = None
        # Once a strict-json_schema call is rejected, remember to use json_object.
        self._use_json_object_fallback = False
        # Observability parity with the other reasoners.
        self.plan_calls: list[str] = []
        self.decide_calls: list[str] = []
        self.last_decide_ms: float | None = None
        # True if the LAST decode used the json_object fallback (A/B caveat signal).
        self.last_used_fallback: bool = False

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    def _ensure_client(self):
        if self._client is None:
            if not self._api_key:
                raise RuntimeError(
                    "NEBIUS_API_KEY is not set; cannot construct the OpenAIReasoner "
                    "client."
                )
            from openai import OpenAI

            self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    def _build_messages(
        self, system: str, prompt: str, schema: dict, *, embed_schema: bool
    ) -> list[dict[str, str]]:
        user = prompt
        if embed_schema:
            # json_object mode: the model isn't constrained by the API to the
            # schema, so we embed it + restate the enum discipline in the prompt.
            user = (
                f"{prompt}\n\nReturn a JSON object that conforms EXACTLY to this "
                f"JSON schema (same field names, only the listed enum values):\n"
                f"{json.dumps(schema)}"
            )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    async def _generate_json(
        self, system: str, prompt: str, schema: dict, *, kind: str = "llm", seq: int = 0
    ) -> Any:
        """One structured-output round-trip → parsed JSON. Tries strict
        json_schema; falls back ONCE to json_object on an unsupported-format 4xx
        and caches the fallback. The blocking call runs in a worker thread.

        ``kind``/``seq`` only label the exact-prompt dump (``_log_prompt``) so you
        can read what THIS call received + how its context was composed."""
        client = self._ensure_client()
        model = self._model

        def _call(use_fallback: bool) -> tuple[str, bool]:
            if use_fallback:
                resp = client.chat.completions.create(
                    model=model,
                    messages=self._build_messages(
                        system, prompt, schema, embed_schema=True
                    ),
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    extra_body=self._extra_body,
                )
                return resp.choices[0].message.content or "", True
            resp = client.chat.completions.create(
                model=model,
                messages=self._build_messages(
                    system, prompt, schema, embed_schema=False
                ),
                response_format=_json_schema_format("reasoner_output", schema),
                temperature=0.0,
                extra_body=self._extra_body,
            )
            return resp.choices[0].message.content or "", False

        if self._use_json_object_fallback:
            text, used = await asyncio.to_thread(_call, True)
        else:
            try:
                text, used = await asyncio.to_thread(_call, False)
            except Exception as exc:  # noqa: BLE001
                if not _looks_like_format_unsupported(exc):
                    raise
                # Endpoint can't do strict json_schema — fall back + remember.
                self._use_json_object_fallback = True
                text, used = await asyncio.to_thread(_call, True)
        self.last_used_fallback = used
        # Observability: dump the EXACT system+composed-user prompt and the raw
        # response to the prompt log, and drop a one-line pointer in the worker log.
        written, n = _log_prompt(kind, seq, system, prompt, text, embedded_schema=used)
        if written:
            print(
                f"  [prompt] {kind} #{seq} → {_PROMPT_LOG_PATH} "
                f"(sys+user {n} chars{', schema embedded' if used else ''})",
                flush=True,
            )
        return json.loads(_strip_fence(_strip_think(text)))

    async def plan_goal(
        self,
        goal: str,
        orient: PageReadout,
        affordances: list[Fact],
    ) -> list[Subgoal]:
        self.plan_calls.append(goal)
        data = await self._generate_json(
            _PLAN_SYSTEM,
            _plan_prompt(goal, orient, affordances),
            _plan_schema(),
            kind="plan",
            seq=len(self.plan_calls),
        )
        # json_object mode may wrap an array in an object — accept either.
        if isinstance(data, dict):
            data = data.get("subgoals") or data.get("plan") or next(
                (v for v in data.values() if isinstance(v, list)), []
            )
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
        context: DecideContext | None = None,
    ) -> StepProposal:
        """Decide the next grounded step. Structured output → SAME post-decode
        guard fence → single re-ask on reject → fail-closed (``ReasonerError``).
        ``context`` is the rich situational frame the decision is made inside."""
        self.decide_calls.append(goal)
        seq = len(self.decide_calls)
        live_indices = sorted(ranked_slice.nodes)
        fact_ids = [f.id for f in facts]
        schema = _step_schema(live_indices, fact_ids)

        t0 = time.perf_counter()
        data = await self._generate_json(
            _DECIDE_SYSTEM,
            _decide_prompt(goal, ranked_slice, facts, history, context=context),
            schema,
            kind="decide",
            seq=seq,
        )
        proposal = _ground_say(_decode_step(data), facts)
        verdict = validate_step_proposal(proposal, ranked_slice, facts)
        if not verdict.ok:
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
                kind="decide-reask",
                seq=seq,
            )
            proposal = _ground_say(_decode_step(data), facts)
            verdict = validate_step_proposal(proposal, ranked_slice, facts)
            if not verdict.ok:
                self.last_decide_ms = (time.perf_counter() - t0) * 1000.0
                raise ReasonerError(
                    "OpenAIReasoner could not produce a guard-valid step after a "
                    f"re-ask: {verdict.reason}"
                )
        self.last_decide_ms = (time.perf_counter() - t0) * 1000.0
        _log_decide(context, proposal, len(ranked_slice.nodes), len(facts), self.last_decide_ms)
        return proposal


def _strip_think(text: str) -> str:
    """Strip reasoning blocks some models (MiniMax M-series, Qwen) inline in the
    content as ``<think>…</think>`` before the JSON. A no-op when absent. Defensive
    even when the provider's thinking switch is off (``self._extra_body``)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Unclosed <think> with no terminator: jump to the first JSON bracket.
    if "<think>" in text.lower():
        for i, ch in enumerate(text):
            if ch in "{[":
                return text[i:]
    return text


def _strip_fence(text: str) -> str:
    """Some OpenAI-compatible models wrap JSON in a ```json fence despite
    response_format; strip it so json.loads succeeds."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: -3]
        # Drop a leading 'json' language tag line if present.
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    return s.strip()
