"""The user-memory invariant gate (no-network): a remembered value can NEVER
re-enter as a grounded, speakable fact.

This is the only mechanism that keeps the structural firewall from eroding across
future sessions — it needs no Moss creds (FakeMemory):
  - ``Recall`` (and ``WorkflowEpisode``) have NO ``source_node_id`` field, so the
    VERIFY membership fence cannot admit a remembered value as speakable.
  - ``recall`` returns a ``Recall``, never a ``Fact``: nothing on the recalled
    bundle carries a live source.
  - episodes upsert by ``(goal, host)`` so re-runs keep the latest good path.
"""

from __future__ import annotations

from clarion.contracts.state import (
    ConsentRecord,
    Fact,
    Recall,
    Subgoal,
    WorkflowEpisode,
)
from clarion.fakes.adapters import FakeMemory


def _flatten_keys(obj: object) -> set:
    """Every dict key anywhere in a nested model_dump() blob."""
    keys: set = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _flatten_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            keys |= _flatten_keys(v)
    return keys


def test_recall_and_episode_have_no_source_node_id_field():
    # The firewall is structural — if someone adds the field this fails loudly.
    assert "source_node_id" not in Recall.model_fields
    assert "source_node_id" not in WorkflowEpisode.model_fields


async def test_write_recall_round_trip():
    m = FakeMemory()
    ep = WorkflowEpisode(
        goal="Find my benefits",
        url_host="www.usa.gov",
        subgoals=[Subgoal(description="open the finder", done_check="navigated")],
        plan_utterance="first open the finder",
        outcome="declined",
        consent=[ConsentRecord(proposal_id="p1", irreversible=True, decision="reject")],
        hard_stops=1,
    )
    await m.write_episode("default", ep)
    await m.write_preference("default", "electric payee", "Northwind")

    r = await m.recall("default", goal="find my benefits", url_host="www.usa.gov")
    assert isinstance(r, Recall)
    assert r.plan_hint is not None
    assert r.plan_hint.subgoals[0].description == "open the finder"
    assert r.preferences["electric payee"] == "Northwind"
    assert r.consent_recall and r.consent_recall[0].decision == "reject"


async def test_recall_never_yields_a_source_node_id():
    m = FakeMemory()
    # A grounded fact was written, an episode + a value-shaped preference exist —
    # recall must STILL surface no live source anywhere on the bundle.
    await m.write(Fact(value="Amount due: $84.32", source_node_id="acct::balance", verified=True))
    await m.write_episode("default", WorkflowEpisode(goal="pay bill", url_host="x"))
    await m.write_preference("default", "amount", "$84.32")

    r = await m.recall("default", goal="pay bill", url_host="x")
    assert "source_node_id" not in _flatten_keys(r.model_dump())
    # Preferences are plain strings (candidates), not sourced Facts.
    assert all(isinstance(v, str) for v in r.preferences.values())


async def test_recall_output_never_enters_grounded_facts():
    # A remembered value re-enters only as a hint; simulate the planner using it
    # and assert it would not be admitted as a grounded fact (no source).
    m = FakeMemory()
    await m.write_preference("default", "payee", "Northwind")
    await m.write_episode(
        "default",
        WorkflowEpisode(goal="g", url_host="h", subgoals=[Subgoal(description="s")]),
    )
    r = await m.recall("default", goal="g", url_host="h")
    # The would-be "grounded_facts" never gains anything from recall: the hint is a
    # WorkflowEpisode/strings, none of which is a Fact with a source_node_id.
    grounded_facts: list[Fact] = []
    candidates = [r.plan_hint, *r.preferences.values(), *r.consent_recall]
    grounded_facts.extend(c for c in candidates if isinstance(c, Fact))
    assert grounded_facts == []


async def test_episode_upserts_latest_path():
    m = FakeMemory()
    await m.write_episode("default", WorkflowEpisode(goal="G", url_host="h", plan_utterance="v1"))
    await m.write_episode("default", WorkflowEpisode(goal="g  ", url_host="h", plan_utterance="v2"))
    prof = await m.read_profile("default")
    eps = [e for e in prof.episodes if e.url_host == "h"]
    assert len(eps) == 1 and eps[0].plan_utterance == "v2"


async def test_default_memory_is_noop_when_unused():
    # read_profile on an unknown user is empty; recall is an empty Recall.
    m = FakeMemory()
    prof = await m.read_profile("nobody")
    assert prof.facts == [] and prof.preferences == {} and prof.episodes == []
    r = await m.recall("nobody", goal="anything", url_host="x")
    assert r.plan_hint is None and r.preferences == {}
