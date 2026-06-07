"""Clarion contracts — the six ports (execution §18.2).

These are the ONLY things the kernel sees (foundation §6 / execution §1). Every
real provider — LiveKit, Moss, Minimax, Playwright, Unsiloed, Atlas — sits behind
one of these ABCs in a Wave-1 adapter. This module is **pure**: ``abc`` + typing
+ our own pure ``state`` value objects. It imports ZERO provider SDKs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Protocol, runtime_checkable

from clarion.contracts.state import (
    Action,
    DecideContext,
    Fact,
    Observation,
    PageDiff,
    PageReadout,
    Passage,
    Profile,
    Recall,
    SelectorMap,
    StepProposal,
    Subgoal,
    WorkflowEpisode,
)


@runtime_checkable
class SpeechHandle(Protocol):
    """The handle returned by ``VoiceTransport.say`` (execution §18.2 footnote).

    Mirrors LiveKit's ``SpeechHandle`` surface that the seam relies on, but is a
    pure structural Protocol so the kernel never imports livekit. ``advance_task``
    awaits ``wait_if_not_interrupted([task])`` to overlap a graph step with the
    spoken sentence; on barge-in ``interrupted`` flips True and the task is
    cancelled (execution §5).
    """

    @property
    def interrupted(self) -> bool: ...

    async def wait_if_not_interrupted(self, tasks: list) -> None: ...


class VoiceTransport(ABC):
    """The voice plane (LiveKit). Owns the <800ms turn budget; the kernel never
    reimplements STT/turn/TTS (execution §1)."""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    def on_partial(self, cb: Callable[[str], None]) -> None:
        """Register the observer hook → fires speculative retrieval on partial
        STT, while the user is still talking (execution §5)."""
        ...

    @abstractmethod
    def on_final(self, cb: Callable[[str], None]) -> None: ...

    @abstractmethod
    def on_barge_in(self, cb: Callable[[], None]) -> None: ...

    @abstractmethod
    async def say(self, text: str, *, interruptible: bool = True) -> SpeechHandle:
        """Speak ``text``. ``interruptible=False`` wraps an atomic act so a stray
        "um" can't fracture an irreversible step (execution §5)."""
        ...

    @abstractmethod
    async def play_filler(self, key: str) -> None:
        """Play timed dead-air cover while a web action runs (execution §5)."""
        ...


class Retriever(ABC):
    """The grounding source (Moss). ``query`` returns ranked, source-referenced
    facts — the epistemic clause's supply (foundation §1, execution §2.2 GROUND)."""

    @abstractmethod
    async def query(self, q: str, *, k: int = 5) -> list[Fact]: ...


class Synthesizer(ABC):
    """Text → audio stream (Minimax). Stub-swappable (execution §15 R2)."""

    @abstractmethod
    async def synthesize(self, text: str) -> AsyncIterator[bytes]: ...


class Reasoner(ABC):
    """The de-hardcoding boundary (architecture Components, the NEW frozen ABC).

    One generic LLM reasons the plan and the next grounded action behind THIS
    port; the deterministic kernel acts and enforces the invariants. The real
    ``GeminiReasoner`` adapter (a LATER agent) is the ONLY new LLM home — it lives
    in ``adapters/``, never here. Like every other port this keeps ``kernel/`` and
    ``contracts/`` SDK-free and lets a ``FakeReasoner`` drive network-free CI.

    Contract types (all pure ``clarion.contracts.state`` value objects):
      - ``orient``        : ``PageReadout`` — the ORIENT screen-reader readback.
      - ``affordances``   : ``list[Fact]`` — the controls the page offers (grounded).
      - ``ranked_slice``  : ``SelectorMap`` — a SUB-map (the top-K label-paired
                            candidate slice the ContextRanker pre-ranked); a real
                            ``SelectorMap`` keyed by the SAME live indices, so a
                            returned ``target_index`` resolves straight back into
                            the live map. A HINT, never the decider.
      - ``facts``         : ``list[Fact]`` — the live grounded facts; a
                            ``StepProposal.value_ref`` must resolve to one's ``id``.
      - ``history``       : ``list[StepProposal]`` — the prior decided steps (the
                            light history; no AXTree/HTML, keeps the checkpoint lean).
    """

    @abstractmethod
    async def plan_goal(
        self,
        goal: str,
        orient: PageReadout,
        affordances: list[Fact],
    ) -> list[Subgoal]:
        """Derive a generic, site-agnostic plan (a list of ``Subgoal``) from the
        goal + the ORIENT readout + the page affordances. Replaces ``_hero_plan``
        (architecture migration Step 3)."""
        ...

    @abstractmethod
    async def decide_step(
        self,
        goal: str,
        ranked_slice: SelectorMap,
        facts: list[Fact],
        history: list[StepProposal],
        context: DecideContext | None = None,
    ) -> StepProposal:
        """Decide the single next grounded action as a ``StepProposal``. The
        returned ``target_index`` / ``value_ref`` are then code-side validated by
        ``kernel.reasoner_guard`` against the live map + Fact ids (structured
        output is not a logit mask).

        ``context`` is the rich situational frame (the user's VERBATIM intent, the
        plan phase, the live page, what just happened) — the step-decider is the
        most consequential agent in the loop, so it is given the most context. It
        is optional only so a bare unit-test fake can omit it; the live kernel
        always supplies it."""
        ...


class ContextRanker(ABC):
    """Semantic pre-ranker for the candidate slice PROPOSE hands the Reasoner.

    The kernel CAN feed ``decide_step`` the full live map, but on a busy page that
    is a large ``target_index`` enum (slower constrained decode) + more prefill. A
    ``ContextRanker`` returns the top-``k`` most goal-relevant nodes as a SUB-map
    keyed by the SAME live indices (so a returned ``target_index`` still resolves
    into the full map). Ranking is by MEANING (embeddings behind this port) — NEVER
    a lexical keyword table: the deleted lexical ``_topk_slice`` pruned the
    goal-relevant control by string-overlap → untargetable → give-up. This is its
    de-hardcoded successor.

    Recall-first contract: an implementation MUST keep any node a grounded ``Fact``
    points at, and MUST fail OPEN (return the full map unchanged) on any embed
    error rather than risk pruning the target."""

    @abstractmethod
    async def rank(
        self, intent: str, page: SelectorMap, facts: list[Fact], k: int
    ) -> SelectorMap:
        """Return the top-``k`` goal-relevant sub-map (a real ``SelectorMap`` keyed
        by the SAME live indices). ``intent`` is the user's verbatim goal; the
        source nodes of ``facts`` are always retained (recall guarantee)."""
        ...


class Actuator(ABC):
    """The a11y-tree actuator (Playwright/CDP). The kernel sees only
    ``action -> observation`` (foundation §6, execution §4)."""

    @abstractmethod
    async def perceive(self) -> SelectorMap:
        """Build the merged, numbered AXTree for the current viewport (§4.1)."""
        ...

    @abstractmethod
    async def act(self, action: Action) -> Observation:
        """Execute click/fill/navigate/read, then re-perceive (§4.3)."""
        ...

    @abstractmethod
    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        """Page-diff to detect a silently-failed step (§4.3)."""
        ...

    async def destinations(self, indices: list[int]) -> dict[int, str]:
        """Best-effort: resolve each given live node index to its navigation
        DESTINATION (a link's absolute href). Used by PROPOSE's abstain-and-clarify
        to suppress a FALSE ambiguity — two controls that lead to the SAME
        destination (a nav/menu entry + its content card pointing at one page) are
        not a real choice, so the kernel acts instead of asking "which did you mean?".

        CONCRETE no-op default (returns ``{}``), NOT an ``@abstractmethod``, so every
        existing fake/adapter and any transport without DOM access stays valid and
        the kernel simply keeps its current behaviour (abstain) when destinations are
        unknown. Real CDP actuators override this. Returns ``{index: href}`` only for
        nodes that resolve to a non-empty destination; a non-link / unresolved node
        is omitted (never guessed)."""
        return {}

    async def highlight(self, source_index: int) -> None:
        """Outline the node at ``source_index`` on the live page — the epistemic-
        clause visual proof (the source / target node is shown on the page, synced
        to the spoken readback). Driven by the SAME node identity the actuator
        clicks (``index → backendDOMNodeId``), never a stored ``bbox``. Best-effort
        and for SIGHTED observers only — the product never depends on it.

        CONCRETE no-op default (like ``destinations``), NOT an ``@abstractmethod``,
        so ``FakeActuator`` and any transport without a live page stay valid. Real
        CDP actuators override this."""
        return None

    async def clear_highlight(self) -> None:
        """Remove the source-node outline (idempotent). Default no-op."""
        return None


class Ingest(ABC):
    """Parse company docs → indexed passages (Unsiloed). ``doc`` is raw bytes
    (PDF/binary) or text (execution §6, §15 R3)."""

    @abstractmethod
    async def ingest(self, doc: bytes | str) -> list[Passage]: ...


class Memory(ABC):
    """Durable write-back of verified facts + profile read (Moss/Atlas)
    (execution §2.2 CONFIRM, §6).

    The knowledge-layer additions (the user-memory design, backlog #4) are
    CONCRETE no-op defaults, NOT new ``@abstractmethod``s — so ``FakeMemory`` and
    every existing/future adapter stay valid without implementing them, and a
    runtime with memory disabled is a clean no-op. ``recall`` returns a ``Recall``,
    NEVER ``list[Fact]``: the return type itself prevents a remembered value from
    entering the kernel as a grounded fact (the invariant firewall — see
    ``state.Recall``)."""

    @abstractmethod
    async def write(self, fact: Fact) -> None: ...

    @abstractmethod
    async def read_profile(self, user_id: str) -> Profile: ...

    async def write_preference(
        self, user_id: str, key: str, value: str, *, origin: str = "stated"
    ) -> None:
        """Durably remember a user preference (a standing trait), captured ONLY via
        the consent-gated end-of-flow "remember?" offer — *no memory without a
        yes*. Default no-op."""
        return None

    async def write_episode(self, user_id: str, episode: WorkflowEpisode) -> None:
        """Persist a completed-workflow record (the reasoned plan + consent
        decisions + timings) for replay-assisted planning next time. Stores the
        plan SHAPE, never a grounded page value. Default no-op."""
        return None

    async def recall(
        self, user_id: str, goal: str, url_host: str, *, k: int = 3
    ) -> Recall:
        """Return a ``Recall`` (plan hint + preferences + consent reminder) to
        warm-start the next run on the same/similar goal. Advisory only — every
        remembered item is re-grounded / re-consented live. Default: empty
        ``Recall``."""
        return Recall()


__all__ = [
    "SpeechHandle",
    "VoiceTransport",
    "Retriever",
    "Synthesizer",
    "Reasoner",
    "Actuator",
    "Ingest",
    "Memory",
]
