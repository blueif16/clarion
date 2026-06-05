"""Deterministic in-memory fakes implementing every Clarion port.

Pure-python, no I/O, no provider SDKs. Behaviour is fully deterministic so the
contract smoke test and the seam spike can assert exact outputs.
"""

from __future__ import annotations

from typing import AsyncIterator, Callable

from clarion.contracts.ports import (
    Actuator,
    Ingest,
    Memory,
    Reasoner,
    Retriever,
    Synthesizer,
    VoiceTransport,
)
from clarion.contracts.state import (
    Action,
    AxNode,
    Fact,
    Observation,
    PageDiff,
    PageReadout,
    Passage,
    Profile,
    SelectorMap,
    StepProposal,
    Subgoal,
)


class FakeSpeechHandle:
    """Structurally satisfies the ``SpeechHandle`` Protocol. Deterministic:
    ``interrupted`` is whatever you pass in; ``wait_if_not_interrupted`` is a
    no-op (the fake voice plane never actually overlaps real audio)."""

    def __init__(self, *, interrupted: bool = False) -> None:
        self._interrupted = interrupted

    @property
    def interrupted(self) -> bool:
        return self._interrupted

    async def wait_if_not_interrupted(self, tasks: list) -> None:  # noqa: ARG002
        return None


class FakeVoiceTransport(VoiceTransport):
    """Records callbacks and spoken lines so tests can assert what the agent
    said and replay observer / barge-in events deterministically."""

    def __init__(self) -> None:
        self.started = False
        self.spoken: list[tuple[str, bool]] = []  # (text, interruptible)
        self.fillers: list[str] = []
        self._on_partial: list[Callable[[str], None]] = []
        self._on_final: list[Callable[[str], None]] = []
        self._on_barge_in: list[Callable[[], None]] = []

    async def start(self) -> None:
        self.started = True

    def on_partial(self, cb: Callable[[str], None]) -> None:
        self._on_partial.append(cb)

    def on_final(self, cb: Callable[[str], None]) -> None:
        self._on_final.append(cb)

    def on_barge_in(self, cb: Callable[[], None]) -> None:
        self._on_barge_in.append(cb)

    async def say(self, text: str, *, interruptible: bool = True) -> FakeSpeechHandle:
        self.spoken.append((text, interruptible))
        return FakeSpeechHandle(interrupted=False)

    async def play_filler(self, key: str) -> None:
        self.fillers.append(key)

    # --- test/spike drivers (not part of the port) -------------------------
    def emit_partial(self, text: str) -> None:
        for cb in self._on_partial:
            cb(text)

    def emit_final(self, text: str) -> None:
        for cb in self._on_final:
            cb(text)

    def emit_barge_in(self) -> None:
        for cb in self._on_barge_in:
            cb()


class FakeRetriever(Retriever):
    """Returns deterministic, source-referenced facts. Seeded from a dict of
    ``query substring -> facts``; falls back to a single echo fact so the
    grounding clause always has a source ref to cite."""

    def __init__(self, corpus: dict[str, list[Fact]] | None = None) -> None:
        self.corpus = corpus or {}
        self.calls: list[str] = []

    async def query(self, q: str, *, k: int = 5) -> list[Fact]:
        self.calls.append(q)
        for needle, facts in self.corpus.items():
            if needle.lower() in q.lower():
                return facts[:k]
        # Deterministic fallback: a grounded echo fact.
        return [
            Fact(
                value=f"result for: {q}",
                source_node_id=f"fake-doc::{abs(hash(q)) % 10_000}",
                verified=True,
                retrieved_at=0.0,
            )
        ][:k]


class FakeSynthesizer(Synthesizer):
    """text → audio: deterministically yields one UTF-8 chunk per word."""

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        for word in text.split():
            yield word.encode("utf-8")


class FakeActuator(Actuator):
    """A tiny scripted page. ``perceive`` returns a fixed two-node SelectorMap
    (one text input, one submit button). ``act`` mutates that page
    deterministically: a ``fill`` writes the value into the input's name, a
    ``click`` on submit appends a confirmation node. ``diff`` compares maps by
    node identity/content."""

    def __init__(self) -> None:
        # node_id -> AxNode (the "real page"); index is assigned on perceive.
        self._page: dict[str, AxNode] = {
            "node-input-amount": AxNode(
                index=0, role="textbox", name="Amount", node_id="node-input-amount"
            ),
            "node-submit": AxNode(
                index=0, role="button", name="Submit", node_id="node-submit"
            ),
        }
        self.acted: list[Action] = []

    def _snapshot(self) -> SelectorMap:
        nodes: dict[int, AxNode] = {}
        for i, node_id in enumerate(self._page):
            n = self._page[node_id]
            nodes[i] = AxNode(
                index=i,
                role=n.role,
                name=n.name,
                state=dict(n.state),
                bbox=list(n.bbox) if n.bbox is not None else None,
                node_id=n.node_id,
            )
        # Deterministic, cheap token estimate.
        token_estimate = sum(len(n.role) + len(n.name) for n in nodes.values())
        return SelectorMap(nodes=nodes, token_estimate=token_estimate)

    async def perceive(self) -> SelectorMap:
        return self._snapshot()

    async def act(self, action: Action) -> Observation:
        self.acted.append(action)
        before = self._snapshot()

        if action.kind == "fill" and action.index is not None:
            node_id = before.nodes[action.index].node_id
            # Native-setter analogue: record the value into the node name.
            self._page[node_id].name = f"Amount: {action.value}"
            return Observation(selector_map=self._snapshot(), success=True)

        if action.kind == "click" and action.index is not None:
            node_id = before.nodes[action.index].node_id
            if node_id == "node-submit" and "node-confirmation" not in self._page:
                self._page["node-confirmation"] = AxNode(
                    index=0,
                    role="status",
                    name="Confirmation #12345",
                    node_id="node-confirmation",
                )
                return Observation(selector_map=self._snapshot(), success=True)
            return Observation(selector_map=self._snapshot(), success=True)

        # read / navigate / unknown: no-op observation.
        return Observation(selector_map=self._snapshot(), success=True)

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        before_by_id = {n.node_id: n for n in before.nodes.values()}
        after_by_id = {n.node_id: n for n in after.nodes.values()}
        added_ids = set(after_by_id) - set(before_by_id)
        removed_ids = set(before_by_id) - set(after_by_id)
        changed_ids = {
            nid
            for nid in set(before_by_id) & set(after_by_id)
            if before_by_id[nid].name != after_by_id[nid].name
            or before_by_id[nid].state != after_by_id[nid].state
        }
        # Report indices in terms of the relevant map.
        added = [n.index for n in after.nodes.values() if n.node_id in added_ids]
        removed = [n.index for n in before.nodes.values() if n.node_id in removed_ids]
        changed = [n.index for n in after.nodes.values() if n.node_id in changed_ids]
        return PageDiff(added=added, removed=removed, changed=changed)


class FakeReasoner(Reasoner):
    """Deterministic, scriptable ``Reasoner`` — unblocks network-free CI for the
    kernel rewire (it stands in for the real ``GeminiReasoner`` exactly as the
    other fakes stand in for their providers).

    Two scripting modes:
      - SEEDED: pass ``subgoals`` and/or ``steps`` (a list of ``StepProposal``);
        ``plan_goal`` returns the seeded subgoals, ``decide_step`` returns the
        seeded steps in order (then repeats the last).
      - DEFAULT (no seed): ``decide_step`` deterministically points at the FIRST
        node in the ``ranked_slice`` and, if any grounded fact is present, sets
        ``value_ref`` to the first fact's REAL id — so a guard-validated step is
        produced from whatever live map/facts it is handed (the spike relies on
        this to exercise real usa.gov indices + Fact ids with no network)."""

    def __init__(
        self,
        *,
        subgoals: list[Subgoal] | None = None,
        steps: list[StepProposal] | None = None,
    ) -> None:
        self._subgoals = subgoals
        self._steps = list(steps) if steps else None
        # Observability parity with the other fakes (tests assert what was asked).
        self.plan_calls: list[str] = []
        self.decide_calls: list[str] = []

    async def plan_goal(
        self,
        goal: str,
        orient: PageReadout,  # noqa: ARG002 - part of the port surface
        affordances: list[Fact],
    ) -> list[Subgoal]:
        self.plan_calls.append(goal)
        if self._subgoals is not None:
            return list(self._subgoals)
        # Default: one generic subgoal naming the goal + a generic done check.
        return [Subgoal(description=f"accomplish: {goal}", done_check="navigated")]

    async def decide_step(
        self,
        goal: str,
        ranked_slice: SelectorMap,
        facts: list[Fact],
        history: list[StepProposal],  # noqa: ARG002 - part of the port surface
    ) -> StepProposal:
        self.decide_calls.append(goal)
        if self._steps:
            # Return the seeded steps in order; repeat the last once exhausted.
            idx = min(len(self.decide_calls) - 1, len(self._steps) - 1)
            return self._steps[idx]
        # Default: point at the first live index; reference the first grounded fact.
        target = next(iter(sorted(ranked_slice.nodes)), None)
        value_ref = facts[0].id if facts else None
        return StepProposal(
            scratch_reasoning=f"first candidate for: {goal}",
            action_kind="read",
            target_index=target,
            value_ref=value_ref,
            irreversibility="reversible",
            success_check="status-fact-appeared",
            say=facts[0].value if facts else "",
        )


class FakeIngest(Ingest):
    """doc → passages: deterministically splits text on blank lines (or decodes
    bytes first), one citable Passage per chunk."""

    async def ingest(self, doc: bytes | str) -> list[Passage]:
        text = doc.decode("utf-8") if isinstance(doc, bytes) else doc
        chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
        if not chunks:
            chunks = [text.strip()] if text.strip() else []
        return [
            Passage(text=chunk, ref=f"fake-passage::{i}", score=1.0 - i * 0.01)
            for i, chunk in enumerate(chunks)
        ]


class FakeMemory(Memory):
    """In-memory profile store. ``write`` appends a fact; ``read_profile`` returns
    the accumulated facts for a user (or an empty profile)."""

    def __init__(self) -> None:
        self._facts: dict[str, list[Fact]] = {}
        # All writes that did not carry a user id (the kernel writes facts; the
        # user binding happens at the call site in real adapters).
        self.written: list[Fact] = []

    async def write(self, fact: Fact) -> None:
        self.written.append(fact)
        # Bucket by source for deterministic read-back in single-user tests.
        self._facts.setdefault("default", []).append(fact)

    async def read_profile(self, user_id: str) -> Profile:
        facts = self._facts.get(user_id) or self._facts.get("default") or []
        return Profile(user_id=user_id, facts=list(facts))


__all__ = [
    "FakeSpeechHandle",
    "FakeVoiceTransport",
    "FakeRetriever",
    "FakeSynthesizer",
    "FakeReasoner",
    "FakeActuator",
    "FakeIngest",
    "FakeMemory",
]
