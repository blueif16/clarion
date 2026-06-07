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
    PageReadout,
    SelectorMap,
    StepProposal,
    Subgoal,
)
from clarion.contracts.events import ConsentDecision
from clarion.fakes import FakeRetriever
from clarion.stages.graph import build_stage_graph, seed_stage_state
from langgraph.types import Command


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


async def test_runs_once_per_url_then_throttles(monkeypatch):
    monkeypatch.setenv("CLARION_AUTO_INDEX", "1")
    ai._reset_seen()
    calls = []

    async def _crawl(url, **kw):
        calls.append(url)

    # first navigation to a page → crawls
    assert await auto_index_host("https://usa.gov/benefits", crawl=_crawl) is True
    # the SAME page again (fragment/trailing-slash normalized away) → throttled
    assert await auto_index_host("https://usa.gov/benefits/#x", crawl=_crawl) is False
    # a DIFFERENT page, even on the SAME host → crawls (index every page we visit)
    assert await auto_index_host("https://usa.gov/other", crawl=_crawl) is True
    # a different host → crawls
    assert await auto_index_host("https://weather.gov/", crawl=_crawl) is True
    assert calls == [
        "https://usa.gov/benefits",
        "https://usa.gov/other",
        "https://weather.gov/",
    ]


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

    async def decide_step(self, goal, ranked_slice, facts, history, context=None):  # noqa: ANN001, ARG002
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


class _NavActuator(Actuator):
    """A single link; a click 'navigates' (the ``describe_page`` url flips), so the
    generic ``navigated`` done-check passes — exercising the executor's
    fire-on-navigation auto-index trigger with the NEW page url."""

    def __init__(self) -> None:
        self.act_calls: list[Action] = []
        self._url = "https://nav.test/start"

    def _map(self) -> SelectorMap:
        return SelectorMap(
            nodes={0: AxNode(index=0, role="link", name="Go", node_id="n-go")},
            token_estimate=8,
        )

    async def perceive(self) -> SelectorMap:
        return self._map()

    async def describe_page(self) -> PageReadout:
        return PageReadout(title="t", url=self._url, summary="s")

    async def act(self, action: Action) -> Observation:
        self.act_calls.append(action)
        if action.kind in ("click", "navigate"):
            self._url = "https://nav.test/dest"
        return Observation(selector_map=self._map(), success=True)

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        return PageDiff()


class _ClickReasoner:
    async def plan_goal(self, goal, orient, affordances):  # noqa: ANN001, ARG002
        return [Subgoal(description="go to dest", done_check="navigated")]

    async def decide_step(self, goal, ranked_slice, facts, history, context=None):  # noqa: ANN001, ARG002
        target = next(iter(sorted(ranked_slice.nodes)), None)
        return StepProposal(
            scratch_reasoning="click the link",
            action_kind="click",
            target_index=target,
            irreversibility="reversible",
            success_check="navigated",
            say="",
        )


async def test_executor_fires_on_orient_on_navigation():
    """Every NEW page the agent navigates to fires the auto-index hook with the new
    url — the greedy per-page indexing trigger (not just the first ORIENT)."""
    seen: list[str] = []
    actuator = _NavActuator()
    graph = build_stage_graph(
        _ClickReasoner(),
        FakeRetriever(corpus={}),
        actuator,
        mode="fast",
        max_replans=1,
        on_orient=seen.append,
    )
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    seed = seed_stage_state(
        goal="go to dest", mode="fast", page_index=await actuator.perceive()
    )
    out = await graph.ainvoke(seed, cfg)
    # Approve any consent gate so the click actually executes and the page navigates
    # (the executor's on-navigation trigger fires only after a real URL change).
    for _ in range(4):
        if "__interrupt__" not in out:
            break
        out = await graph.ainvoke(
            Command(resume=ConsentDecision(decision="approve").model_dump()), cfg
        )
    assert seen[0] == "https://nav.test/start"  # planner orient (start page)
    assert "https://nav.test/dest" in seen  # executor fired on the navigation
