"""ExtensionActuator — transport-parity + act-correctness tests (extension-build #3).

The point of this suite is to prove the §4 perception pipeline is
**transport-independent**: the same pipeline that runs over Playwright's
``CDPSession`` produces the *same* numbered ``SelectorMap`` when driven over the
``chrome.debugger`` CDP relay — without any browser extension existing yet.

  B. **Transport parity** — record the raw CDP dicts a live
     ``PlaywrightActuator.perceive()`` reads on the overlay fixture, replay them
     into ``ExtensionActuator`` via a ``FakeRelay``, and assert an identical
     sequence of ``(index, role, name, bbox)``.
  C. **Act correctness** — ``FakeRelay`` records the exact CDP traffic each act
     produces (native-setter ``Runtime.evaluate``; press+release
     ``Input.dispatchMouseEvent``; ``Page.navigate``; read ``Runtime.evaluate``),
     and ``perceive`` over the fake stays under the 2000-token budget.
  D. **Live wire round-trip** (``@pytest.mark.live``) — stand up the real
     ``WebSocketCdpRelay`` loopback server, connect a tiny in-test fake
     "extension" WS client that answers ``cdp`` requests per the FROZEN protocol,
     and assert ``perceive()`` completes over the socket and matches the parity map.

Run: ``.venv/bin/python -m pytest clarion/tests/test_extension_actuator.py -v``
(the live test additionally needs the ``websockets`` lib: ``-m live``).
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import json
import socket
import threading
from pathlib import Path
from typing import Optional

import pytest
import pytest_asyncio

from clarion.actuator.extension_actuator import ExtensionActuator
from clarion.actuator.relay import CdpError, FakeRelay
from clarion.contracts.state import Action

_FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Local HTTP server serving the fixtures dir (module-scoped, started once).
# Mirrors test_actuator.py — http matches production (file:// has CDP quirks).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fixture_server() -> str:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(_FIXTURES)
    )
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


# ---------------------------------------------------------------------------
# Record the raw CDP responses from a live PlaywrightActuator.perceive(), and
# capture the SelectorMap it produces — the parity reference.
# ---------------------------------------------------------------------------


async def _record_playwright_perceive(url: str):
    """Run a live ``PlaywrightActuator.perceive`` while recording every raw CDP
    ``(method, params) -> result`` it reads, plus the SelectorMap it produced.

    Returns ``(reference_map, triple_fetch_results)`` where ``triple_fetch_results``
    maps the three perception methods to the exact dicts Chrome returned — those
    are the inputs the ExtensionActuator must reproduce parity from.
    """
    from clarion.actuator.actuator import PlaywrightActuator

    act = await PlaywrightActuator.create(url, headless=True)
    captured: dict[str, dict] = {}
    real_send = act._cdp.send

    async def recording_send(method: str, params: Optional[dict] = None):
        result = await real_send(method, params)
        # Only the three triple-fetch reads carry the perception inputs; record
        # the latest result per method (perceive issues each exactly once).
        if method in (
            "DOM.getDocument",
            "Accessibility.getFullAXTree",
            "DOMSnapshot.captureSnapshot",
        ):
            captured[method] = result
        return result

    act._cdp.send = recording_send  # type: ignore[method-assign]
    try:
        reference_map = await act.perceive()
    finally:
        await act.close()
    return reference_map, captured


def _make_replay_relay(captured: dict[str, dict]) -> FakeRelay:
    """A ``FakeRelay`` that replays the recorded triple-fetch and answers the
    enable / stamp CDP calls the ExtensionActuator's perceive issues."""
    counter = {"n": 0}

    def canned(method: str, params: Optional[dict]) -> dict:
        if method in captured:
            return captured[method]
        if method.endswith(".enable"):
            return {}
        if method == "DOM.pushNodesByBackendIdsToFrontend":
            # Hand back a synthetic frontend nodeId per stamped backend id.
            counter["n"] += 1
            return {"nodeIds": [counter["n"]]}
        if method == "DOM.setAttributeValue":
            return {}
        # Act-path methods (fill/click/navigate/read re-perceive after acting);
        # canned so an act over the replay relay doesn't raise. The native-setter
        # reports ok so fill is observed as successful.
        if method == "Runtime.evaluate":
            return {"result": {"value": {"ok": True, "value": "x"}}}
        if method == "Input.dispatchMouseEvent":
            return {}
        if method == "Page.navigate":
            return {"frameId": "frame-1"}
        raise CdpError(f"replay relay: unexpected method {method!r}")

    return FakeRelay(canned)


def _signature(sm) -> list[tuple]:
    """The transport-independent identity of a SelectorMap: the ordered
    ``(index, role, name, bbox)`` tuples. clarion-ids / node-ids may differ
    between transports, so they are deliberately excluded."""
    return [
        (i, n.role, n.name, tuple(n.bbox) if n.bbox else None)
        for i, n in sorted(sm.nodes.items())
    ]


@pytest_asyncio.fixture
async def parity(fixture_server: str):
    """Record the Playwright reference perceive once and build a replay relay."""
    reference_map, captured = await _record_playwright_perceive(
        f"{fixture_server}/overlay.html"
    )
    relay = _make_replay_relay(captured)
    return reference_map, captured, relay


# ---------------------------------------------------------------------------
# B. Transport PARITY — the core proof.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parity_same_selector_map(parity):
    """(B) ExtensionActuator over the recorded CDP dicts yields the SAME ordered
    (index, role, name, bbox) sequence PlaywrightActuator produced live."""
    reference_map, _captured, relay = parity
    ext = ExtensionActuator(relay)
    ext_map = await ext.perceive()

    assert _signature(ext_map) == _signature(reference_map), (
        "ExtensionActuator's SelectorMap diverged from the Playwright reference — "
        "the §4 pipeline is NOT transport-independent.\n"
        f"  playwright: {_signature(reference_map)}\n"
        f"  extension : {_signature(ext_map)}"
    )
    # Sanity: the page does have interactive nodes (parity over an empty map is
    # vacuous), and the occluded 'Pay now' button is excluded under BOTH.
    assert ext_map.nodes, "expected interactive nodes on the overlay fixture"
    names = [n.name.lower() for n in ext_map.nodes.values()]
    assert not any("pay now" in nm for nm in names)


@pytest.mark.asyncio
async def test_parity_triple_fetch_issued(parity):
    """The ExtensionActuator issues the exact §4 triple-fetch over the relay (and
    enables the five domains once)."""
    _reference_map, _captured, relay = parity
    ext = ExtensionActuator(relay)
    await ext.perceive()

    methods = [m for (m, _p) in relay.sent]
    for domain in ("DOM", "Accessibility", "DOMSnapshot", "Runtime", "Page"):
        assert f"{domain}.enable" in methods
    assert methods.count("DOM.getDocument") == 1
    assert methods.count("Accessibility.getFullAXTree") == 1
    assert methods.count("DOMSnapshot.captureSnapshot") == 1
    # The triple-fetch params match the Playwright transport's exactly.
    getdoc_params = next(p for (m, p) in relay.sent if m == "DOM.getDocument")
    assert getdoc_params == {"depth": -1, "pierce": True}
    snap_params = next(
        p for (m, p) in relay.sent if m == "DOMSnapshot.captureSnapshot"
    )
    assert snap_params["includePaintOrder"] is True
    assert snap_params["includeDOMRects"] is True


# ---------------------------------------------------------------------------
# C. ACT correctness (FakeRelay records the exact CDP traffic).
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def acted(parity):
    """A perceived ExtensionActuator + its relay (so act helpers have a map)."""
    _reference_map, _captured, relay = parity
    ext = ExtensionActuator(relay)
    sm = await ext.perceive()
    return ext, relay, sm


@pytest.mark.asyncio
async def test_act_fill_sends_native_setter(acted):
    """(C) fill issues a Runtime.evaluate whose expression embeds the native
    setter and the {clarionId, value} payload."""
    ext, relay, sm = acted
    textbox_idx = next(i for i, n in sm.nodes.items() if n.role == "textbox")
    relay.sent.clear()
    await ext.act(Action(kind="fill", index=textbox_idx, value="4242 4242"))

    evals = [
        (m, p) for (m, p) in relay.sent if m == "Runtime.evaluate"
    ]
    assert evals, "fill did not issue a Runtime.evaluate"
    expr = evals[0][1]["expression"]
    assert evals[0][1]["returnByValue"] is True
    # The native setter's signature line + the dispatched events prove it's the
    # shared _NATIVE_SETTER_JS, and the payload is baked in.
    assert "getOwnPropertyDescriptor" in expr
    assert "data-clarion-id" in expr
    assert '"value": "4242 4242"' in expr
    assert "clarionId" in expr


@pytest.mark.asyncio
async def test_act_click_sends_press_release_at_center(acted):
    """(C) click issues mousePressed then mouseReleased at the node's bbox center."""
    ext, relay, sm = acted
    idx = next(iter(sm.nodes))
    bbox = sm.nodes[idx].bbox
    cx = bbox[0] + bbox[2] / 2.0
    cy = bbox[1] + bbox[3] / 2.0
    relay.sent.clear()
    await ext.act(Action(kind="click", index=idx))

    mouse = [p for (m, p) in relay.sent if m == "Input.dispatchMouseEvent"]
    assert len(mouse) >= 2, "click should press AND release"
    assert mouse[0]["type"] == "mousePressed"
    assert mouse[1]["type"] == "mouseReleased"
    for ev in mouse[:2]:
        assert ev["x"] == cx and ev["y"] == cy


@pytest.mark.asyncio
async def test_act_navigate_sends_page_navigate(acted):
    """(C) navigate issues Page.navigate with the url."""
    ext, relay, _sm = acted
    relay.sent.clear()
    await ext.act(Action(kind="navigate", value="https://example.test/next"))

    navs = [p for (m, p) in relay.sent if m == "Page.navigate"]
    assert navs, "navigate did not issue a Page.navigate"
    assert navs[0]["url"] == "https://example.test/next"


@pytest.mark.asyncio
async def test_act_read_sends_read_js(acted):
    """(C) read issues a Runtime.evaluate running the shared read JS."""
    ext, relay, sm = acted
    textbox_idx = next(i for i, n in sm.nodes.items() if n.role == "textbox")
    relay.sent.clear()
    await ext.act(Action(kind="read", index=textbox_idx))

    evals = [p for (m, p) in relay.sent if m == "Runtime.evaluate"]
    assert evals, "read did not issue a Runtime.evaluate"
    expr = evals[0]["expression"]
    # _READ_JS reads .value / textContent by clarion id.
    assert "data-clarion-id" in expr
    assert "textContent" in expr


@pytest.mark.asyncio
async def test_perceive_token_budget(parity):
    """(C) perceive over the FakeRelay stays under the 2000-token budget."""
    _reference_map, _captured, relay = parity
    ext = ExtensionActuator(relay)
    sm = await ext.perceive()
    assert sm.token_estimate < 2000, f"token_estimate too high: {sm.token_estimate}"


# ---------------------------------------------------------------------------
# D. Live wire round-trip over a real loopback WebSocket (no browser).
# ---------------------------------------------------------------------------


async def _fake_extension_client(uri: str, captured: dict[str, dict]) -> None:
    """A tiny stand-in for the MV3 extension service-worker: connect to the relay
    and answer ``cdp`` commands with canned results per the FROZEN protocol.

    It is a *dumb relay* exactly like the real extension — it reads the command
    envelope, looks up a canned ``result`` for the method, and replies with
    ``cdp.result`` (or ``cdp.error``). It also emits the ``session.start``
    lifecycle frame on connect."""
    from websockets.asyncio.client import connect

    counter = {"n": 0}

    def result_for(method: str, params: dict) -> dict:
        if method in captured:
            return captured[method]
        if method.endswith(".enable"):
            return {}
        if method == "DOM.pushNodesByBackendIdsToFrontend":
            counter["n"] += 1
            return {"nodeIds": [counter["n"]]}
        if method == "DOM.setAttributeValue":
            return {}
        raise KeyError(method)

    async with connect(uri) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "session.start",
                    "tabId": 1,
                    "url": "http://localhost/overlay.html",
                    "title": "fixture",
                }
            )
        )
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") != "cdp":
                continue
            req_id = msg["id"]
            try:
                result = result_for(msg["method"], msg.get("params") or {})
                reply = {"id": req_id, "type": "cdp.result", "result": result}
            except KeyError as exc:
                reply = {
                    "id": req_id,
                    "type": "cdp.error",
                    "error": f"no canned result for {exc}",
                }
            await ws.send(json.dumps(reply))


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_websocket_round_trip(fixture_server: str):
    """(D) ExtensionActuator.perceive() completes over a REAL loopback WebSocket
    relay (Python server + in-test fake-extension client), matching the parity
    map. Marked live — it needs the websockets lib."""
    from clarion.actuator.relay import WebSocketCdpRelay

    reference_map, captured = await _record_playwright_perceive(
        f"{fixture_server}/overlay.html"
    )

    relay = WebSocketCdpRelay(host="127.0.0.1", port=0)
    await relay.start()
    uri = f"ws://127.0.0.1:{relay.port}"

    client_task = asyncio.create_task(_fake_extension_client(uri, captured))
    try:
        await relay.wait_connected(timeout=5.0)
        # The lifecycle frame surfaced through the relay.
        await asyncio.sleep(0.05)
        assert relay.session is not None
        assert relay.session.get("type") == "session.start"

        ext = ExtensionActuator(relay)
        ext_map = await asyncio.wait_for(ext.perceive(), timeout=10.0)

        assert _signature(ext_map) == _signature(reference_map), (
            "live-WS ExtensionActuator map diverged from the Playwright reference"
        )
    finally:
        client_task.cancel()
        try:
            await client_task
        except (asyncio.CancelledError, Exception):
            pass
        await relay.close()
