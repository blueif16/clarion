"""ExtensionRuntime — the live-session entrypoint, headless (extension-build #6).

This proves the seam the runbook needs: ``ExtensionRuntime`` stands up the
``WebSocketCdpRelay`` server, waits for the extension's ``session.start``, builds
``ExtensionActuator`` over the relay, assembles the SAME ``HeroRuntime`` stage/
perceive path the hero flow uses (only the actuator transport differs), and drives
a read-only ``perceive`` → ``PanelState`` publish — all without a real Chrome or a
real LiveKit room.

The in-test FAKE extension is a *dumb relay* exactly like the MV3 service-worker:
it connects to the relay as the client, emits ``session.start``, and answers each
``cdp`` command with a canned ``result`` per the FROZEN protocol. The perception
inputs are the real triple-fetch CDP a live ``PlaywrightActuator.perceive`` reads
off ``overlay.html`` (recorded once) — so the runtime's perceive produces a
non-empty, parity-correct ``SelectorMap`` over the socket.

Marked ``@pytest.mark.live``: it binds a loopback port and needs the ``websockets``
lib + a real ``PlaywrightActuator`` to record the perception CDP. Run:
  ``.venv/bin/python -m pytest clarion/tests/test_extension_runtime.py -m live -v``
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import json
import socket
import threading
from pathlib import Path

import pytest

from clarion.app.extension_runtime import ExtensionRuntime
from clarion.app.runtime import HeroRetriever

# Reuse the #3 harness's live recorder + fake-extension client verbatim — ONE
# implementation of the fake extension, shared across the actuator + runtime tests.
from clarion.tests.test_extension_actuator import (
    _fake_extension_client,
    _record_playwright_perceive,
    _signature,
)

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def fixture_server() -> str:
    """Serve the fixtures dir over loopback http (file:// has CDP quirks)."""
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


@pytest.mark.live
@pytest.mark.asyncio
async def test_extension_runtime_drives_perceive_over_relay(fixture_server: str):
    """ExtensionRuntime: relay server up → fake extension session.start → build
    ExtensionActuator → perceive() yields a NON-EMPTY, parity-correct selector_map,
    and a PanelState is published — no real Chrome, no LiveKit."""
    # The perception inputs: the real triple-fetch a live PlaywrightActuator reads.
    reference_map, captured = await _record_playwright_perceive(
        f"{fixture_server}/overlay.html"
    )

    # A recording panel sink so we can assert the runtime published a PanelState.
    published: list[dict] = []

    def sink(panel, payload: str) -> None:
        published.append(json.loads(payload))

    # Demo URL is only the nominal target seeded into the PanelState; no request.
    # The KB retriever is injected (a deterministic offline stand-in) so the
    # runtime never reaches for live Moss creds in this headless test.
    ext = ExtensionRuntime(
        host="127.0.0.1",
        port=0,
        demo_url="http://localhost:8770/",
        panel_sink=sink,
        kb_retriever=HeroRetriever(),
    )
    await ext.start_relay()
    assert ext.relay is not None
    uri = f"ws://127.0.0.1:{ext.relay.port}"

    client_task = asyncio.create_task(_fake_extension_client(uri, captured))
    try:
        # The runtime waits for the socket + the session.start lifecycle frame.
        session = await asyncio.wait_for(ext.wait_for_session(timeout=5.0), timeout=6.0)
        assert session.get("type") == "session.start"
        assert session.get("tabId") == 1

        # Build the ExtensionActuator over the started relay + the HeroRuntime.
        runtime = await ext.build_runtime()
        # The injected actuator IS the extension transport (no browser spawned).
        from clarion.actuator.extension_actuator import ExtensionActuator

        assert isinstance(runtime.actuator, ExtensionActuator)

        # Read-only perceive over the socket → a non-empty, parity-correct map.
        sm = await asyncio.wait_for(ext.perceive_once(), timeout=10.0)
        assert sm.nodes, "ExtensionRuntime.perceive produced an EMPTY selector_map"
        assert _signature(sm) == _signature(reference_map), (
            "ExtensionRuntime perceive diverged from the Playwright reference map"
        )
        # The occluded 'Pay now' button is excluded (the §4 paint-order filter).
        names = [n.name.lower() for n in sm.nodes.values()]
        assert not any("pay now" in nm for nm in names)

        # A PanelState was published through the SAME wire the hero flow uses.
        assert published, "ExtensionRuntime did not publish a PanelState"
        assert "stage" in published[-1]
    finally:
        client_task.cancel()
        try:
            await client_task
        except (asyncio.CancelledError, Exception):
            pass
        await ext.aclose()


@pytest.mark.live
@pytest.mark.asyncio
async def test_extension_runtime_run_readonly_single_perceive(fixture_server: str):
    """The operator entrypoint loop with interval=0 perceives ONCE, publishes, and
    returns 0 — the default read-only single-shot the runbook starts with."""
    _reference_map, captured = await _record_playwright_perceive(
        f"{fixture_server}/overlay.html"
    )
    published: list[dict] = []
    ext = ExtensionRuntime(
        host="127.0.0.1",
        port=0,
        panel_sink=lambda panel, payload: published.append(json.loads(payload)),
        kb_retriever=HeroRetriever(),
    )
    await ext.start_relay()
    assert ext.relay is not None
    uri = f"ws://127.0.0.1:{ext.relay.port}"
    client_task = asyncio.create_task(_fake_extension_client(uri, captured))
    try:
        await asyncio.wait_for(ext.wait_for_session(timeout=5.0), timeout=6.0)
        await ext.build_runtime()
        rc = await asyncio.wait_for(ext.run_readonly(interval=0.0), timeout=10.0)
        assert rc == 0
        assert published, "run_readonly(interval=0) published no PanelState"
    finally:
        client_task.cancel()
        try:
            await client_task
        except (asyncio.CancelledError, Exception):
            pass
        await ext.aclose()
