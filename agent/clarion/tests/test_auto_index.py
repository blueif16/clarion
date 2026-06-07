"""AUTO-INDEX — the background crawl-on-activation trigger (no network).

Covers the orchestration contract (gate / throttle / fail-open / non-blocking
scheduling) and the planner WIRING (the injected ``on_orient`` hook fires with the
live page url during PLAN). All against fakes — no Playwright, no Moss, no creds.
"""

from __future__ import annotations

import uuid

import clarion.app.auto_index as ai
from clarion.app.auto_index import (
    auto_index_enabled,
    auto_index_host,
    schedule_auto_index,
)
from clarion.contracts.ports import Actuator
from clarion.contracts.state import (
    Action,
    AxNode,
    Fact,
    Observation,
    PageDiff,
    SelectorMap,
    StepProposal,
    Subgoal,
)
from clarion.fakes import FakeRetriever
from clarion.stages.graph import build_stage_graph, seed_stage_state


# --- the orchestration contract -------------------------------------------


async def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CLARION_AUTO_INDEX", raising=False)
    ai._reset_seen()
    calls = []

    async def _crawl(url, **kw):
        calls.append(url)

    assert auto_index_enabled() is False
    assert await auto_index_host("https://usa.gov/x", crawl=_crawl) is False
    assert calls == []  # gated off → nothing crawled


async def test_runs_once_per_host_then_throttles(monkeypatch):
    monkeypatch.setenv("CLARION_AUTO_INDEX", "1")
    ai._reset_seen()
    calls = []

    async def _crawl(url, **kw):
        calls.append(url)

    # first activation on the host → crawls
    assert await auto_index_host("https://usa.gov/benefits", crawl=_crawl) is True
    # a later activation on the SAME host (any path) → throttled, no second crawl
    assert await auto_index_host("https://usa.gov/other", crawl=_crawl) is False
    # a different host → crawls
    assert await auto_index_host("https://weather.gov/", crawl=_crawl) is True
    assert calls == ["https://usa.gov/benefits", "https://weather.gov/"]


async def test_blank_host_is_skipped(monkeypatch):
    monkeypatch.setenv("CLARION_AUTO_INDEX", "1")
    ai._reset_seen()
    assert await auto_index_host("not-a-url", crawl=lambda *a, **k: None) is False


async def test_denylisted_seed_is_skipped_without_consuming_throttle(monkeypatch):
    monkeypatch.setenv("CLARION_AUTO_INDEX", "1")
    ai._reset_seen()
    calls = []

    async def _crawl(url, **kw):
        calls.append(url)

    # the user is sitting on a /logout page → do NOT auto-crawl it
    assert await auto_index_host("https://usa.gov/logout", crawl=_crawl) is False
    assert calls == []
    # …and the throttle slot was NOT consumed → a normal page of the same host crawls
    assert await auto_index_host("https://usa.gov/benefits", crawl=_crawl) is True
    assert calls == ["https://usa.gov/benefits"]


async def test_fail_open_and_retryable(monkeypatch):
    monkeypatch.setenv("CLARION_AUTO_INDEX", "1")
    ai._reset_seen()
    attempts = []

    async def _boom(url, **kw):
        attempts.append(url)
        raise RuntimeError("no playwright here")

    # a crawl error is swallowed (fail-open) and the host is released for retry
    assert await auto_index_host("https://usa.gov/", crawl=_boom) is False
    assert await auto_index_host("https://usa.gov/", crawl=_boom) is False
    assert len(attempts) == 2  # retried, not stuck-throttled after a failure


async def test_schedule_is_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("CLARION_AUTO_INDEX", raising=False)
    ai._reset_seen()
    assert schedule_auto_index("https://usa.gov/") is None


async def test_schedule_fires_background_task_when_enabled(monkeypatch):
    monkeypatch.setenv("CLARION_AUTO_INDEX", "1")
    ai._reset_seen()
    seen = []

    async def _fake_crawl(url, **kw):
        seen.append(url)

    # default crawl resolves to site_indexer.crawl_and_index — patch it so the
    # background task runs without Playwright.
    monkeypatch.setattr(
        "clarion.app.site_indexer.crawl_and_index", _fake_crawl, raising=True
    )
    task = schedule_auto_index("https://usa.gov/apply")
    assert task is not None  # non-blocking: a task was scheduled, not awaited inline
    await task  # let the background task finish
    assert seen == ["https://usa.gov/apply"]


# --- the planner wiring (a terminating fast-mode graph run) ----------------


class _FormActuator(Actuator):
    """One fillable field + a button; fill populates so the generic done-check
    (field_nonempty) passes and the graph terminates cleanly."""

    def __init__(self) -> None:
        self.act_calls: list[Action] = []
        self._filled = False

    def _map(self) -> SelectorMap:
        amount = "Amount: $42.00" if self._filled else ""
        return SelectorMap(
            nodes={
                0: AxNode(index=0, role="textbox", name=amount,
                          state={"required": True}, node_id="n-amount"),
                1: AxNode(index=1, role="button", name="Continue", node_id="n-c"),
            },
            token_estimate=24,
        )

    async def perceive(self) -> SelectorMap:
        return self._map()

    async def act(self, action: Action) -> Observation:
        self.act_calls.append(action)
        if action.kind == "fill":
            self._filled = True
        return Observation(selector_map=self._map(), success=True)

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        return PageDiff()


class _FillReasoner:
    async def plan_goal(self, goal, orient, affordances):  # noqa: ANN001, ARG002
        return [Subgoal(description="enter the amount", done_check="field_nonempty")]

    async def decide_step(self, goal, ranked_slice, facts, history):  # noqa: ANN001, ARG002
        target = next(iter(sorted(ranked_slice.nodes)), None)
        return StepProposal(
            scratch_reasoning="fill the amount field",
            action_kind="fill",
            target_index=target,
            value_ref=facts[0].id if facts else None,
            irreversibility="reversible",
            success_check="field_nonempty",
            say=facts[0].value if facts else "",
        )


async def test_planner_invokes_on_orient_hook():
    """The injected hook fires during PLAN, with the page url — proving the
    auto-index trigger is wired regardless of the app-side gate."""
    seen: list[str] = []
    actuator = _FormActuator()
    facts = [Fact(value="$42.00", source_node_id="n-amount", verified=True)]
    graph = build_stage_graph(
        _FillReasoner(),
        FakeRetriever(corpus={"": facts, "amount": facts, "fill": facts}),
        actuator,
        mode="fast",
        max_replans=1,
        on_orient=seen.append,  # spy: record the url the planner hands us
    )
    seed = seed_stage_state(
        goal="enter the amount", mode="fast", page_index=await actuator.perceive()
    )
    await graph.ainvoke(seed, {"configurable": {"thread_id": str(uuid.uuid4())}})
    assert len(seen) == 1  # planner called the hook exactly once, at orient
    assert isinstance(seen[0], str)  # handed the (possibly empty) page url
