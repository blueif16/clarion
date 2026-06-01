"""POL1 — demo-mode fallback tests (execution §9).

Proves the ``CachedActuator`` replays the recorded fixture honestly AND that the
REAL K1 kernel consent HARD-STOP fires from cached perception (no browser, no
network) — the load-bearing "judge-proof live run" claim. Only perception is
cached; the kernel/consent/policy are exercised for real here.
"""

from __future__ import annotations

import pytest

from clarion.app.demo_mode import CachedActuator, demo_mode_enabled
from clarion.contracts.events import ConsentDecision, ConsentRequest
from clarion.contracts.state import Action
from clarion.kernel.graph import build_kernel, seed_state
from clarion.stages.predicates import needs_rescue

_URL = "http://localhost:8770/"


def test_demo_mode_flag_parsing(monkeypatch):
    monkeypatch.delenv("CLARION_DEMO_MODE", raising=False)
    assert demo_mode_enabled() is False
    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv("CLARION_DEMO_MODE", truthy)
        assert demo_mode_enabled() is True
    monkeypatch.setenv("CLARION_DEMO_MODE", "0")
    assert demo_mode_enabled() is False


@pytest.mark.asyncio
async def test_cached_perceive_login_has_rescue_trigger():
    """The recorded login tree carries the unlabeled-password textbox → the
    RESCUE trigger survives caching (perception is faithful, not flattened)."""
    act = await CachedActuator.create(_URL)
    sm = await act.perceive()
    assert needs_rescue(sm) is True
    # The unlabeled textbox (empty accessible name) is the choke.
    assert any(n.role == "textbox" and not n.name.strip() for n in sm.nodes.values())
    await act.close()


@pytest.mark.asyncio
async def test_cached_state_machine_advances_deterministically():
    """navigate → upsell-occluded form → dismiss → fillable form → submit, all
    from the cache (no browser)."""
    act = await CachedActuator.create(_URL)

    # Sign in advances to the account page + sets the session flag (read via _page).
    sm = await act.perceive()
    sign = next(i for i, n in sm.nodes.items() if "sign in" in n.name.lower())
    await act.act(Action(kind="click", index=sign))
    assert await act._page.evaluate("sessionStorage.getItem('nw_auth')") == "1"

    # The pay page opens with the autopay upsell occluding the form.
    await act.act(Action(kind="navigate", value=_URL.rstrip("/") + "/account/pay"))
    sm = await act.perceive()
    assert {n.name for n in sm.nodes.values()} == {
        "Enable AutoPay", "No thanks, close this offer"
    }

    # Dismiss the upsell → the payment form is reachable.
    dismiss = next(i for i, n in sm.nodes.items() if "no thanks" in n.name.lower())
    await act.act(Action(kind="click", index=dismiss))
    sm = await act.perceive()
    card = next(i for i, n in sm.nodes.items() if n.name == "Card number")

    # Native-setter fill is replayed as a read-backable value.
    await act.act(Action(kind="fill", index=card, value="4242 4242 4242 4242"))
    assert await act.read_value(card) == "4242 4242 4242 4242"

    await act.close()


@pytest.mark.asyncio
async def test_cached_pay_consent_hard_stop_is_real():
    """THE honest core: with perception served from the fixture, the REAL K1
    kernel still HARD-STOPS at the irreversible PAY (interrupt) and only acts on
    an approved Command(resume=) — the consent gate is NOT faked."""
    act = await CachedActuator.create(_URL)
    # Drive to the pay form (dismiss the upsell) so 'Submit payment' is perceived.
    await act.act(Action(kind="navigate", value=_URL.rstrip("/") + "/account/pay"))
    sm = await act.perceive()
    dismiss = next(i for i, n in sm.nodes.items() if "no thanks" in n.name.lower())
    await act.act(Action(kind="click", index=dismiss))

    from clarion.app.runtime import HeroRetriever

    # The retriever is exercised by GROUND but the PAY proposal is the irreversible
    # submit click (no fillable textbox in the scoped page_index) — so the kernel
    # forms the hard-stop regardless of the grounded facts.
    kernel = build_kernel(HeroRetriever(), act, mode="fast")
    cfg = {"configurable": {"thread_id": "test-pay"}}
    seed = seed_state(goal="Submit the payment", mode="fast")
    sm = await act.perceive()
    submit_idx, submit_node = next(
        (i, n) for i, n in sm.nodes.items() if n.role == "button"
        and "submit payment" in n.name.lower()
    )
    from clarion.contracts.state import SelectorMap

    seed["page_index"] = SelectorMap(nodes={submit_idx: submit_node}, token_estimate=10)

    # First invoke → the kernel must INTERRUPT at the irreversible step (hard-stop).
    result = await kernel.ainvoke(seed, cfg)
    assert "__interrupt__" in result, "kernel did NOT hard-stop at the irreversible PAY"
    (intr,) = result["__interrupt__"]
    req = ConsentRequest.model_validate(intr.value)
    assert req.irreversible is True

    # PROOF: nothing acted before consent — the cached submit button is unchanged.
    mid = await act.perceive()
    assert any("submit payment" in n.name.lower() for n in mid.nodes.values())
    assert not any(n.name == "Submitted" for n in mid.nodes.values())

    # Approve → the kernel acts; the cached state machine advances to submitted.
    from langgraph.types import Command

    final = await kernel.ainvoke(
        Command(resume=ConsentDecision(decision="approve").model_dump()), cfg
    )
    assert any(c.decision == "approve" for c in final["consent_log"])
    after = await act.perceive()
    assert any(n.name == "Submitted" for n in after.nodes.values())

    # The side-channel confirmation read is served only AFTER the submit.
    assert await act._page.evaluate("document.getElementById('conf-num')?.textContent")
    await act.close()
