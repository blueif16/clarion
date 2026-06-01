"""A1 — actuator acceptance tests (execution §4 / §15 A1).

All five accept conditions, run against a real headless chromium over a real
local HTTP server (file:// has CDP quirks; http matches production):

  1. the occluded button (#pay-now, under the modal scrim) is NOT in the
     selector_map on the overlay fixture;
  2. all genuinely-interactive elements are numbered;
  3. token_estimate < 2000 for a viewport;
  4. native-setter fills the controlled input and the value PERSISTS (the
     fixture reverts any value not committed via a real 'input' event);
  5. a click produces a non-empty diff.

Run: ``.venv/bin/python -m pytest clarion/tests/test_actuator.py -v``
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import socket
import threading
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from clarion.actuator.actuator import (
    PaintOrderRemover,
    PlaywrightActuator,
    _bbox_containment,
    _LayoutRect,
)
from clarion.contracts.state import Action

_FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Local HTTP server serving the fixtures dir (module-scoped, started once).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fixture_server() -> str:
    """Serve clarion/tests/fixtures over http on an ephemeral port."""
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(_FIXTURES)
    )
    # Pick a free port.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest_asyncio.fixture
async def overlay_actuator(fixture_server: str) -> AsyncIterator[PlaywrightActuator]:
    """A live actuator on the overlay fixture."""
    act = await PlaywrightActuator.create(
        f"{fixture_server}/overlay.html", headless=True
    )
    try:
        yield act
    finally:
        await act.close()


# ---------------------------------------------------------------------------
# Pure-unit tests for the two hard filters (no browser needed).
# ---------------------------------------------------------------------------


def test_bbox_containment_full():
    # inner entirely inside outer → 1.0
    assert _bbox_containment([10, 10, 20, 20], [0, 0, 100, 100]) == 1.0


def test_bbox_containment_partial():
    # inner half inside outer → 0.5
    frac = _bbox_containment([90, 0, 20, 10], [0, 0, 100, 100])
    assert abs(frac - 0.5) < 1e-9


def test_bbox_containment_disjoint():
    assert _bbox_containment([200, 200, 10, 10], [0, 0, 100, 100]) == 0.0


def test_paint_order_remover_occludes_lower():
    # A small button (paint order 1) covered by a big scrim (paint order 5).
    button = _LayoutRect(backend_id=1, x=100, y=100, w=80, h=30, paint_order=1)
    scrim = _LayoutRect(backend_id=2, x=0, y=0, w=1280, h=800, paint_order=5)
    remover = PaintOrderRemover([button, scrim])
    assert remover.is_occluded(button) is True
    assert remover.is_occluded(scrim) is False


def test_paint_order_remover_ancestor_does_not_occlude():
    # A container (parent) with higher paint order must NOT occlude its child.
    child = _LayoutRect(backend_id=10, x=100, y=100, w=80, h=30, paint_order=1)
    container = _LayoutRect(backend_id=20, x=0, y=0, w=400, h=400, paint_order=9)
    container.is_ancestor_of[child.backend_id] = True
    remover = PaintOrderRemover([child, container])
    assert remover.is_occluded(child) is False


def test_containment_filter_folds_nested_interactive_child():
    """A button's nested interactive child (e.g. a role=button icon ~99% inside)
    does NOT get a separate index — the container survives, the child is folded
    (execution §4.1.4)."""
    act = PlaywrightActuator.__new__(PlaywrightActuator)
    candidates = [
        {"role": "button", "name": "Submit", "bbox": [100, 100, 120, 40],
         "backend_id": 1, "node_id": "1", "state": {}},
        # Nested icon with role=button, ~99% inside the Submit button.
        {"role": "button", "name": "", "bbox": [104, 108, 24, 24],
         "backend_id": 2, "node_id": "2", "state": {}},
        {"role": "textbox", "name": "Email", "bbox": [100, 200, 200, 30],
         "backend_id": 3, "node_id": "3", "state": {}},
    ]
    kept = act._containment_filter(candidates)
    kept_ids = {c["backend_id"] for c in kept}
    assert 2 not in kept_ids, "nested icon button should be folded into its parent"
    assert kept_ids == {1, 3}, f"expected Submit + Email survive, got {kept_ids}"


# ---------------------------------------------------------------------------
# The five live accept conditions (browser).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept1_occluded_button_excluded(
    overlay_actuator: PlaywrightActuator,
):
    """(1) #pay-now is occluded by the modal scrim → NOT in the selector_map."""
    sm = await overlay_actuator.perceive()
    names = [n.name.lower() for n in sm.nodes.values()]
    assert not any("pay now" in nm for nm in names), (
        f"occluded 'Pay now' button leaked into selector_map: {names}"
    )


@pytest.mark.asyncio
async def test_accept2_interactables_numbered(
    overlay_actuator: PlaywrightActuator,
):
    """(2) The genuinely-interactive, visible elements ARE numbered: the card
    textbox and the visible 'Accept cookies' button."""
    sm = await overlay_actuator.perceive()
    names = [n.name.lower() for n in sm.nodes.values()]
    roles = [n.role for n in sm.nodes.values()]
    # Indices are sequential 0..N-1.
    assert sorted(sm.nodes.keys()) == list(range(len(sm.nodes)))
    # The visible cookie button is present and numbered.
    assert any("accept cookies" in nm for nm in names), (
        f"visible 'Accept cookies' not numbered: {names}"
    )
    # The card textbox is present.
    assert "textbox" in roles, f"card textbox not numbered: {roles}"


@pytest.mark.asyncio
async def test_accept3_token_budget(overlay_actuator: PlaywrightActuator):
    """(3) token_estimate < 2000 for a viewport."""
    sm = await overlay_actuator.perceive()
    assert sm.token_estimate < 2000, f"token_estimate too high: {sm.token_estimate}"


@pytest.mark.asyncio
async def test_accept4_native_setter_persists(
    overlay_actuator: PlaywrightActuator,
):
    """(4) The native-setter fills the controlled input AND the value persists.

    The fixture's reflector reverts any value not committed via a real 'input'
    event (within ~30ms). A bare ``.value =`` would be wiped; the native-setter
    dispatches 'input', so it sticks. We re-read after a delay to prove it."""
    sm = await overlay_actuator.perceive()
    card_idx = next(
        i for i, n in sm.nodes.items() if n.role == "textbox"
    )
    obs = await overlay_actuator.act(
        Action(kind="fill", index=card_idx, value="4242 4242 4242 4242")
    )
    assert obs.success, f"native-setter fill reported failure: {obs.detail}"
    # Let the reflector run several cycles — a non-input write would be reverted.
    await asyncio.sleep(0.2)
    persisted = await overlay_actuator.read_value(card_idx)
    assert persisted == "4242 4242 4242 4242", (
        f"native-setter value did not persist (controlled input reverted it): "
        f"{persisted!r}"
    )


@pytest.mark.asyncio
async def test_accept5_click_produces_diff(
    overlay_actuator: PlaywrightActuator,
):
    """(5) A click yields a non-empty page-diff.

    Clicking 'Accept cookies' dismisses the modal: the scrim/cookie button are
    removed and the previously-occluded 'Pay now' button is revealed → the diff
    has both removed and added nodes."""
    before = await overlay_actuator.perceive()
    cookie_idx = next(
        i
        for i, n in before.nodes.items()
        if "accept cookies" in n.name.lower()
    )
    obs = await overlay_actuator.act(Action(kind="click", index=cookie_idx))
    after = obs.selector_map
    diff = await overlay_actuator.diff(before, after)
    assert not diff.is_empty, (
        f"click produced an empty diff; before={list(before.nodes.values())} "
        f"after={list(after.nodes.values())}"
    )
    # The revealed 'Pay now' button should now be visible/numbered.
    after_names = [n.name.lower() for n in after.nodes.values()]
    assert any("pay now" in nm for nm in after_names), (
        f"'Pay now' not revealed after dismissing the modal: {after_names}"
    )


@pytest.mark.asyncio
async def test_perceive_vision_is_named_stub(
    overlay_actuator: PlaywrightActuator,
):
    """The vision fallback is named honestly and deferred (execution §4.2)."""
    with pytest.raises(NotImplementedError, match="vision fallback"):
        await overlay_actuator.perceive_vision()
