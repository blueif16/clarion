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

import operator
from typing import Annotated, Literal, Optional, TypedDict

from pydantic import BaseModel, Field

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
    """User profile read back from Memory (Moss/Atlas write-back)."""

    user_id: str
    facts: list[Fact] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)


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


class Proposal(BaseModel):
    """What the kernel is about to do/say — formed by PROPOSE, surfaced at the
    consent gate (execution §2.2 PROPOSE / ⟨CONSENT⟩)."""

    id: str
    # The spoken readback — what the user hears before they say "yes".
    utterance: str
    action: Optional[Action] = None
    irreversible: bool = False


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


__all__ = [
    "AxNode",
    "SelectorMap",
    "Fact",
    "PageReadout",
    "Passage",
    "Profile",
    "Action",
    "Proposal",
    "Observation",
    "PageDiff",
    "Stage",
    "Consent",
    "TraceEvent",
    "ClarionState",
]
