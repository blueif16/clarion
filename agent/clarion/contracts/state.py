"""Clarion contracts — durable goal-state and the value objects that flow through
the kernel loop.

This module is **pure**: pydantic v2 + typing + the stdlib only. It imports ZERO
provider SDKs (foundation §6 invariant). Everything here is the freeze artifact
that Wave-1 builds against (execution §18.3).

The single source of truth for the agent's progress is ``ClarionState`` — a
``TypedDict`` that lives in the LangGraph checkpointer (NOT loose LLM context),
so it survives an ``interrupt()`` round-trip (execution §2.1).
"""

from __future__ import annotations

import hashlib
import operator
from typing import Annotated, Literal, Optional, TypedDict

from pydantic import BaseModel, Field, computed_field

# ---------------------------------------------------------------------------
# Perception value objects (the merged, numbered a11y tree — execution §4)
# ---------------------------------------------------------------------------


class AxNode(BaseModel):
    """One node in the merged, numbered accessibility tree.

    ``node_id`` is the stable handle back into the real CDP/Playwright node;
    ``index`` is the LLM-facing sequential number that is *also* the thing we
    say out loud ("item 5, the Submit button" — execution §4.1c).
    """

    index: int
    role: str
    name: str = ""
    # Arbitrary a11y state flags (checked / expanded / disabled / focused / ...).
    state: dict[str, bool] = Field(default_factory=dict)
    # [x, y, width, height] in CSS pixels; None when geometry is unavailable.
    bbox: Optional[list[float]] = None
    node_id: str


class SelectorMap(BaseModel):
    """The current merged AXTree: index -> AxNode, plus a token-budget estimate.

    The kernel reasons over ``nodes`` by integer index; the actuator resolves an
    index back to the real node when it acts (execution §4.3).
    """

    nodes: dict[int, AxNode] = Field(default_factory=dict)
    token_estimate: int = 0


# ---------------------------------------------------------------------------
# Grounding value objects (the epistemic clause — foundation §1)
# ---------------------------------------------------------------------------


class Fact(BaseModel):
    """A retrieved fact. The invariant: a fact may NOT be spoken unless it has a
    ``source_node_id`` (it is grounded). ``polarity`` carries negative
    verification — "there is **no** late fee" is a first-class, sourced fact
    (foundation §1, execution §2.2 VERIFY).
    """

    value: str
    # AXTree node (or retriever doc ref) the fact was read from.
    # None => ungrounded => MUST NOT be spoken.
    source_node_id: Optional[str] = None
    polarity: Literal["present", "absent"] = "present"
    verified: bool = False
    # Unix epoch seconds at retrieval — drives the live latency meter (§8).
    retrieved_at: float = 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        """A stable, deterministic content id — ``<polarity>:<source_node_id>:<value>``
        hashed (sha1, 16 hex chars). It is the handle a ``StepProposal.value_ref``
        points at: an *enum over real Fact ids*, NOT a free-text value the model
        can fabricate (architecture Components / killer-closer #1).

        - Deterministic: equal ``(value, source_node_id, polarity)`` → equal ``id``
          (no timestamp / object identity in the digest, so ``retrieved_at`` and
          ``verified`` don't perturb it — the same page value re-read still resolves).
        - Computed (not a stored field), so every existing ``Fact(...)`` call is
          unchanged and the 100 frozen tests stay green. It DOES serialize (pydantic
          ``computed_field``) so ``value_ref`` resolution survives a checkpoint
          round-trip; deserialization ignores the extra key (it recomputes).
        """
        digest = hashlib.sha1(
            f"{self.polarity}\x00{self.source_node_id or ''}\x00{self.value}".encode()
        ).hexdigest()
        return f"fact-{digest[:16]}"


class PairedFact(BaseModel):
    """A first-class grounded label↔value pairing (architecture killer-closer #1).

    The worst epistemic failure is a *clean citation on the wrong number* — reading
    the past-due row's ``$142.10`` as the amount due. A bare pair of ``Fact``s does
    not protect against that: two facts can be true yet mis-associated. A
    ``PairedFact`` makes the *association itself* grounded: the label half and the
    value half EACH carry their real AX ``source_node_id``, and ``method`` records
    HOW the pairing was geometrically established — **never reading-order**.

    The geometric EXTRACTION that builds these from a live page is a LATER agent's
    job (the ContextRanker / PairedFact extractor). This contract only fixes the
    SHAPE + the membership helper VERIFY uses: an "X is Y" sentence is speakable
    iff a single ``PairedFact`` backs both halves (``backs(label, value)``).
    """

    label: Fact
    value: Fact
    # HOW the label↔value association was established — a structural/geometric
    # signal, NOT 8px reading-order proximity (the thing that mis-pairs).
    method: Literal["aria-labelledby", "for", "shared-row", "dom-ancestry"]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        """Stable id over the two real Fact ids + the pairing method."""
        digest = hashlib.sha1(
            f"{self.label.id}\x00{self.value.id}\x00{self.method}".encode()
        ).hexdigest()
        return f"pair-{digest[:16]}"

    def backs(self, label_text: str, value_text: str) -> bool:
        """Does THIS single pairing ground both halves of an "X is Y" claim?

        VERIFY's pairing-correctness fence (architecture invariant fence #3): an
        "X is Y" claim is speakable ONLY if one ``PairedFact`` has ``label.value ==
        X`` AND ``value.value == Y`` — byte-identical (extract-don't-generate), both
        halves sourced. Two separate true facts that no single pairing joins return
        ``False`` (the mis-pairing is ungroundable → refused)."""
        return (
            self.label.value == label_text
            and self.value.value == value_text
            and self.label.source_node_id is not None
            and self.value.source_node_id is not None
        )


class PageReadout(BaseModel):
    """ORIENT — a grounded, spoken-ready description of the current page.

    This is the screen-reader baseline the co-pilot reads back BEFORE any goal is
    set (the goal-formation on-ramp): what the page IS and what the user can DO on
    it. The invariant holds here too (foundation §1): every ``Fact`` in
    ``headings`` / ``affordances`` carries the real AX ``source_node_id`` it was
    read from — nothing in a readout is ungrounded.

    ``affordances`` is the recommend step (the controls the page actually offers);
    ``summary`` is the single readback string the voice plane speaks, ending on an
    open prompt so the user states their goal (which is then confirmed — never
    assumed).
    """

    title: str = ""
    url: str = ""
    headings: list[Fact] = Field(default_factory=list)
    affordances: list[Fact] = Field(default_factory=list)
    summary: str = ""


class Passage(BaseModel):
    """A chunk produced by Ingest (Unsiloed) and consumed by the Retriever.

    ``ref`` is the citable source handle that becomes ``Fact.source_node_id``
    when a passage is surfaced as a spoken fact.
    """

    text: str
    ref: str
    score: float = 0.0
    metadata: dict[str, str] = Field(default_factory=dict)


class Profile(BaseModel):
    """User profile read back from Memory (Moss/Atlas write-back).

    ``preferences`` and ``episodes`` are the de-hardcoded knowledge layer's
    additions (the memory design): remembered standing traits and completed-
    workflow records. Both default-empty, so every existing ``Profile(...)`` call is
    unchanged. NEITHER is a ``Fact`` — a recalled preference/episode carries no
    ``source_node_id`` and is never spoken as a fresh grounded fact.
    """

    user_id: str
    facts: list[Fact] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)
    preferences: dict[str, str] = Field(default_factory=dict)
    episodes: list["WorkflowEpisode"] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Action / proposal value objects (the agentic clause — foundation §1)
# ---------------------------------------------------------------------------


class Action(BaseModel):
    """A single primitive the actuator can execute.

    ``index`` points into the current ``SelectorMap``. ``irreversible`` is the
    Fast-mode hard-stop gate (foundation §5): reversible actions auto-resume,
    irreversible ones always hit ``⟨CONSENT⟩``.
    """

    kind: Literal["click", "fill", "navigate", "read"]
    index: Optional[int] = None
    value: Optional[str] = None
    irreversible: bool = False
    # For a ``fill`` ONLY: press Enter after typing to SUBMIT the field (a search
    # box with no separate search button — the live recreation.gov home). A
    # submitting fill is consequential (it commits the query), so the gate treats
    # it like a click, never like a bare re-typable fill. Additive, default False
    # (backward-compatible with every existing caller/checkpoint).
    submit: bool = False


class Proposal(BaseModel):
    """What the kernel is about to do/say — formed by PROPOSE, surfaced at the
    consent gate (execution §2.2 PROPOSE / ⟨CONSENT⟩)."""

    id: str
    # The spoken readback — what the user hears before they say "yes".
    utterance: str
    action: Optional[Action] = None
    irreversible: bool = False


# ---------------------------------------------------------------------------
# Reasoner I/O value objects (the de-hardcoding boundary — architecture
# Components: the `Reasoner` port outputs; the `GeminiReasoner` adapter emits
# these via structured output. Pure models; ZERO SDK.)
# ---------------------------------------------------------------------------


class Subgoal(BaseModel):
    """One generic step in the plan the ``Reasoner.plan_goal`` derives from the
    goal + the ORIENT readout + the page affordances — the replacement for the
    hardcoded ``_hero_plan`` stage topology (architecture migration Step 3).

    Generic and site-agnostic: no AUTH→…→CONFIRM names baked in. ``done_check``
    names a *registered* generic success check (a SELECTION, never model say-so —
    killer-closer #3); CODE evaluates it against the re-perceived tree.
    """

    # A short, generic, human-readable intent ("find the amount due").
    description: str
    # The registered generic check that certifies this subgoal is done (a
    # SELECTION — e.g. "field-now-nonempty" / "navigated" / "status-fact-appeared").
    done_check: str = ""


class StepProposal(BaseModel):
    """The ``Reasoner.decide_step`` output — the next single grounded action, with
    every field the ``GeminiReasoner`` structured-output schema must carry
    (architecture Components / GeminiReasoner).

    Structured output is NOT a logit mask: the model can still emit an off-page
    ``target_index`` or a dangling ``value_ref``. ``kernel.reasoner_guard`` is the
    code-side post-decode fence that rejects those before they can act.
    """

    # Drafted FIRST (the model reasons before it points) — never spoken; audit only.
    scratch_reasoning: str = ""
    action_kind: Literal["click", "fill", "navigate", "read"]
    # Integer index into the LIVE SelectorMap (validated vs the live map by the guard).
    target_index: Optional[int] = None
    # A reference to a REAL ``Fact.id`` (the value to fill/speak), or None when the
    # action carries no value (a click). Validated vs live Fact ids by the guard.
    value_ref: Optional[str] = None
    # A LITERAL string to TYPE for a ``fill`` — the USER'S OWN input (a search query,
    # a date or name THEY gave), which has NO page source so it can't be a
    # ``value_ref``. The "no fact without a source" fence governs values READ OFF THE
    # PAGE (``value_ref``) and what we SPEAK — NOT what the user instructs us to enter,
    # so typing their own words is safe. PROPOSE prefers a resolved ``value_ref`` and
    # falls back to ``fill_text`` ONLY for a fill into a free-text entry control
    # (textbox/searchbox/combobox). Additive, default-None (backward-compatible).
    fill_text: Optional[str] = None
    # The model's grounded judgement, paired with the independent code structural
    # pre-screen at the gate (killer-closer #2). The model can ESCALATE, never
    # downgrade past the structural net; UNKNOWN routes through CONSENT in Fast mode.
    irreversibility: Literal["reversible", "irreversible", "unknown"] = "unknown"
    irreversibility_rationale: str = ""
    # A SELECTION: the name of a registered generic success check CODE evaluates
    # against the re-perceived tree (killer-closer #3 — never the model self-grading).
    success_check: str = ""
    # The verbatim grounded string the voice plane speaks — extracted from grounded
    # spans, never generated. Empty for a silent step.
    say: str = ""
    # Other live target indices the goal ALSO plausibly matches (besides
    # ``target_index``). The model self-reports its own ambiguity here: a non-empty
    # list means "I see more than one distinct control this goal could mean" — the
    # kernel MUST clarify (a safe read-back-and-ask), never act on a guess. Additive,
    # default-empty (backward-compatible): an unset value is the unambiguous case.
    alternatives: list[int] = Field(default_factory=list)
    # For a ``fill`` ONLY: press Enter after typing to SUBMIT the field. The model
    # sets this when a typed query must be committed and NO search/submit control is
    # in the numbered items (the live recreation.gov home: the only path to running
    # the search is Enter — the decider knew it, "need to submit via Enter on
    # field", and had no verb for it). Consequential: the gate treats a submitting
    # fill like a click. Additive, default-False (backward-compatible).
    submit: bool = False
    # The model's self-reported polarity metacognition: True when the proposed
    # ``say`` ASSERTS AN ABSENCE / negative ("no late fee", "no autopay enrolled")
    # instead of reading back a present value. The kernel routes ONLY a flagged
    # negative through the closed-world ``NegativeVerifier`` (honest-decline, fence
    # #5); an unflagged say is a positive read-back, already fenced by membership.
    # Additive, default-False (backward-compatible). This replaces the banned
    # lexical ``is_negative_claim`` keyword list — the model does the metacognition,
    # not a stopword table (the de-hardcoding thesis). SAFE either way: the
    # membership fence (#2) already bars speaking any non-grounded line, so this
    # flag only selects the hedge / sourced-negative UX, never whether a false
    # negative could be spoken.
    asserts_absence: bool = False


class DecideContext(BaseModel):
    """The rich, situational context handed to ``Reasoner.decide_step`` — the
    single most important agent in the loop is the step-decider, so it must be the
    MOST informed: it gets the user's VERBATIM intent (never genericized), where we
    are in the plan, what the live page is, and what just happened.

    The de-hardcoding thesis is intact: this carries MEANING (the real goal, the
    real page), never a keyword/topology table — the model still does all the
    judgement. Pure model; ZERO SDK. Built transiently in PROPOSE and passed to
    ``decide_step``; it is NOT a checkpointed state channel.
    """

    # The user's confirmed request, VERBATIM — what they actually asked for. Never
    # the planner's genericized restatement (that loss is what made the reasoner
    # read "Food assistance" instead of navigating to it).
    user_intent: str = ""
    # Where we are in the plan (the "current phase").
    subgoal_index: int = 0
    subgoal_total: int = 1
    subgoal_description: str = ""
    # The registered code-checkable milestone this subgoal completes on.
    subgoal_done_check: str = ""
    # The whole plan (subgoal descriptions), for situational awareness.
    plan: list[str] = Field(default_factory=list)
    # The CURRENT live page at the moment of the decision (fresh ORIENT).
    page_title: str = ""
    page_url: str = ""
    page_summary: str = ""
    # What happened on the prior loop / why this subgoal is not done yet (the
    # replan signal — so a retry changes strategy instead of repeating itself).
    last_outcome: str = ""
    # Advisory recalled hint (knowledge layer). NEVER binding — re-ground on the
    # live page; a recalled value is never spoken without being re-grounded.
    recall_hint: str = ""


# ---------------------------------------------------------------------------
# Memory / knowledge-layer value objects (the user-memory design — backlog #4).
# Pure models; ZERO SDK. The INVARIANT FIREWALL lives in ``Recall``: it is NOT a
# ``Fact`` and has NO ``source_node_id`` field, so the VERIFY membership fence
# cannot admit a remembered value as speakable — a recalled plan/preference must
# be re-grounded on the live page before anything is spoken or acted on.
# ---------------------------------------------------------------------------


class ConsentRecord(BaseModel):
    """A lean, contract-pure projection of one consent decision, for the episode
    record — what was proposed, whether it was irreversible, and how it was
    decided. (``gov_proof``'s ``ConsentEvent`` is an app-layer dataclass; THIS is
    the frozen value object the memory layer persists and recalls.) Advisory only:
    a recalled decision NEVER auto-consents — every irreversible step still hits a
    fresh live ``interrupt()`` and a fresh per-step "yes" (foundation §1)."""

    proposal_id: str = ""
    utterance: str = ""
    irreversible: bool = False
    decision: str = ""


class WorkflowEpisode(BaseModel):
    """A persisted completed-workflow record — the reasoned ``Subgoal`` plan +
    consent decisions + timings of a finished run. Stored by the ``Memory`` port
    and recalled to warm-start the NEXT run's plan on the same/similar goal.

    It carries the PLAN and the consent SHAPE, never a grounded page value: there
    is no ``Fact`` and no ``source_node_id`` anywhere here, so a recalled episode
    can never be spoken as a fresh grounded fact (the invariant holds by
    construction). The store side deliberately omits the run's ``grounded_facts``.
    """

    goal: str = ""
    url_host: str = ""
    subgoals: list[Subgoal] = Field(default_factory=list)
    plan_utterance: str = ""
    outcome: Literal["completed", "declined", "error"] = "completed"
    consent: list[ConsentRecord] = Field(default_factory=list)
    hard_stops: int = 0
    approvals: int = 0
    decide_ms_mean: float = 0.0
    perceive_ms_mean: float = 0.0
    completed_at: float = 0.0
    # How many fields the run filled — the "complicated form" effort signal for
    # ``is_workflow`` (a form is often ONE subgoal with many fields, so subgoal
    # count alone misses it). Never a value, just a count.
    n_filled: int = 0

    def is_workflow(self) -> bool:
        """Is this finished run substantial enough to remember + offer to repeat?

        TWO independent axes, OR'd — a run qualifies on EITHER:
          - CONSEQUENCE: it committed an irreversible step the user approved
            (``transactional``) — a one-click bill-pay counts even though it is
            short; or
          - EFFORT: it ran a multi-step process (``>= 3`` subgoals) OR filled out a
            real form (``>= 3`` fields).
        A trivial single-surface read (one subgoal, no fills, no commit) is NOT a
        workflow — there is nothing to repeat (the answer re-grounds every time).
        Derived purely from the run's own record — structural counts, never a
        lexical read of the goal text (the de-hardcoding thesis)."""
        transactional = any(
            c.irreversible and c.decision == "approve" for c in self.consent
        )
        substantial = len(self.subgoals) >= 3 or self.n_filled >= 3
        return transactional or substantial


class Recall(BaseModel):
    """What ``Memory.recall`` returns to the planner to make the NEXT run faster +
    recall preferences. THE INVARIANT FIREWALL: a ``Recall`` is structurally NOT a
    ``Fact`` and has NO ``source_node_id`` field, so a remembered value cannot be
    routed through VERIFY as speakable. ``plan_hint`` warm-starts the plan,
    ``preferences`` supply fill CANDIDATES, ``consent_recall`` surfaces a spoken
    reminder at the gate — all advisory, all re-grounded/re-consented live."""

    plan_hint: Optional["WorkflowEpisode"] = None
    preferences: dict[str, str] = Field(default_factory=dict)
    consent_recall: list[ConsentRecord] = Field(default_factory=list)
    similarity: float = 0.0


# ---------------------------------------------------------------------------
# Actuator observation value objects (execution §4.3)
# ---------------------------------------------------------------------------


class Observation(BaseModel):
    """The result of executing an Action: the freshly re-perceived tree plus a
    success signal the done-predicate / silent-fail check reads (execution §4.3,
    §3.3)."""

    selector_map: SelectorMap
    success: bool = True
    # Free-form note (e.g. validation error text the screen reader never announced).
    detail: str = ""


class PageDiff(BaseModel):
    """Delta between two SelectorMaps — how CONFIRM detects a silently-failed
    step (execution §4.3). Indices refer to the *after* map for added nodes and
    the *before* map for removed nodes."""

    added: list[int] = Field(default_factory=list)
    removed: list[int] = Field(default_factory=list)
    changed: list[int] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


# ---------------------------------------------------------------------------
# Stage / consent / trace value objects (execution §2.1, §3)
# ---------------------------------------------------------------------------


class Stage(BaseModel):
    """One specialized node in the stage graph (execution §3). Carries its own
    tool subset, a machine-checkable done-predicate (by registered name, never
    model say-so — §3.3), and a negative-verification list."""

    id: str
    goal: str
    tools: list[str] = Field(default_factory=list)
    # Name of a registered checker fn — resolved by ST1, not here.
    done_predicate: str = ""
    negative_checks: list[str] = Field(default_factory=list)


class Consent(BaseModel):
    """One entry in the consent audit trail (the glass-box trace, execution
    §2.1). Also the idempotency guard: ACT checks the consent_log for a prior
    record of ``proposal_id`` before re-executing a side-effect on resume
    (execution §2.3)."""

    proposal_id: str
    decision: Literal["approve", "reject", "edit", "respond"]
    value: Optional[str] = None
    # Unix epoch seconds.
    at: float = 0.0


class TraceEvent(BaseModel):
    """A node entry/exit (or instrumented timing) event. Drives the demo UI's
    glass-box trace and the latency meter (execution §6, §8)."""

    node: str
    event: Literal["enter", "exit", "info"] = "info"
    # Unix epoch seconds.
    at: float = 0.0
    # Optional structured payload (e.g. {"retrieval_ms": 6}).
    data: dict[str, object] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# The durable goal-state (execution §2.1 / §18.3)
# ---------------------------------------------------------------------------


class ClarionState(TypedDict):
    """The durable, checkpointed goal-state. Lives in the LangGraph checkpointer
    (AsyncPostgresSaver in prod; InMemorySaver in the spike/tests), NOT in loose
    LLM context — so it survives an ``interrupt()`` (execution §2.1).

    NOTE: ``step`` is a ``(k, n)`` pair meaning "k of n steps within the current
    stage". It is declared ``tuple[int, int]`` here, but be aware that the
    JsonPlusSerializer round-trips tuples as lists; consumers that need a real
    tuple should coerce on read. (Flagged for the orchestrator — see report.)
    """

    goal: str
    mode: Literal["normal", "fast"]
    plan: list[Stage]
    stage_idx: int
    step: tuple[int, int]
    page_index: SelectorMap
    grounded_facts: list[Fact]
    pending_proposal: Optional[Proposal]
    # Append-only audit channels. LangGraph channels are last-value-wins by
    # default, so without an ``operator.add`` reducer a node that writes
    # consent_log/trace silently OVERWRITES it — which breaks §2.3 idempotency
    # (ACT reads prior approve/act markers out of these on an interrupt resume).
    # With the reducer, every node returns ONLY its NEW entries and LangGraph
    # concatenates. (Contract re-freeze 2026-05-31, validated by S1 + K1.)
    consent_log: Annotated[list[Consent], operator.add]
    trace: Annotated[list[TraceEvent], operator.add]


# Resolve Profile's forward reference to ``WorkflowEpisode`` (defined after Profile).
Profile.model_rebuild()


__all__ = [
    "AxNode",
    "SelectorMap",
    "Fact",
    "PairedFact",
    "PageReadout",
    "Passage",
    "Profile",
    "Action",
    "Proposal",
    "Subgoal",
    "StepProposal",
    "ConsentRecord",
    "WorkflowEpisode",
    "Recall",
    "Observation",
    "PageDiff",
    "Stage",
    "Consent",
    "TraceEvent",
    "ClarionState",
]
