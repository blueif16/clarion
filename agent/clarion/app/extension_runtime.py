"""I1 — the EXTENSION runtime: drive the §4 stage/perceive path over the user's
own authenticated tab via the chrome.debugger CDP relay (extension-build #6).

The hero flow (``hero_harness`` / ``voice_entry``) drives the §4 pipeline over a
``PlaywrightActuator`` against a spawned browser. THIS entrypoint drives the
*identical* stage/perceive path over an ``ExtensionActuator`` whose CDP rides a
``WebSocketCdpRelay`` — so the brain perceives + acts on the user's real,
already-authenticated tab. The kernel / stages / Moss / voice plane are unchanged:
the extension is only a second ``Actuator`` transport behind the frozen port.

What this runtime does (Relay protocol v1 — FROZEN, ``docs/extension-build.md``):
  1. Bind ``WebSocketCdpRelay`` on ``127.0.0.1:8771`` (Python is the SERVER) and
     wait for the MV3 extension service-worker to connect + emit ``session.start``.
  2. Build ``ExtensionActuator(relay)`` and assemble a ``HeroRuntime`` with that
     actuator injected (the SAME stage graph + retrievers + ``PanelPublisher`` the
     hero flow uses — only the actuator transport differs).
  3. Run a READ-ONLY perceive → readback loop over the live tab: number the
     interactive AXTree, print a perceived-node summary so the live operator sees
     it working, and publish a ``PanelState`` (the same wire the U1 panel reads).
     No act / no fill / no click — §9 recording rules: read-only up to the wall.

It coexists with the voice worker unchanged: the extension's offscreen LiveKit
client joins the room as the human; the ``voice_entry`` worker joins as the agent
(``docs/extension-build.md`` Live runbook). This process owns the actuator side.

Selected by ``CLARION_ACTUATOR=extension`` (so ``voice_entry`` / ``runtime`` keep
``PlaywrightActuator`` by default and ``CLARION_DEMO_MODE=1`` keeps the
``CachedActuator``). Build the actuator seam once, here.

Run:  CLARION_ACTUATOR=extension .venv/bin/python -m clarion.app.extension_runtime
      (or:  .venv/bin/python -m clarion.app.extension_runtime)
Env:  CLARION_RELAY_HOST (default 127.0.0.1), CLARION_RELAY_PORT (default 8771),
      DEMO_SITE_URL (only seeds the PanelState's nominal target; no request made),
      CLARION_EXT_PERCEIVE_INTERVAL (read-only re-perceive seconds; default 0 = once).
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Optional

from clarion.actuator.extension_actuator import ExtensionActuator
from clarion.actuator.relay import (
    DEFAULT_AGENT_PORT,
    BrokerCdpRelay,
    WebSocketCdpRelay,
)
from clarion.app.runtime import HeroRuntime

DEFAULT_RELAY_HOST = "127.0.0.1"
DEFAULT_RELAY_PORT = 8771

# The flag that routes the actuator seam to the extension transport.
ACTUATOR_ENV = "CLARION_ACTUATOR"


def extension_actuator_selected() -> bool:
    """True when ``CLARION_ACTUATOR=extension`` (the chrome.debugger path)."""
    return os.environ.get(ACTUATOR_ENV, "").strip().lower() == "extension"


def _log(msg: str) -> None:
    print(f"  [extension-runtime] {msg}", flush=True)


def _summarize_nodes(sm) -> str:
    """A compact, operator-readable line for a perceived ``SelectorMap``."""
    head = [
        f"[{i}] {n.role}:{n.name!r}"
        for i, n in sorted(sm.nodes.items())[:8]
    ]
    more = "" if len(sm.nodes) <= 8 else f"  …(+{len(sm.nodes) - 8} more)"
    return ", ".join(head) + more


class ExtensionRuntime:
    """Owns the relay server + the ``HeroRuntime`` (extension actuator) for one
    live session. Drives the read-only perceive → readback loop and publishes the
    PanelState; the same ``build_stage_graph`` the hero flow uses is reachable via
    ``self.runtime`` for the (consensual) act path once the operator opts in."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_RELAY_HOST,
        port: int = DEFAULT_RELAY_PORT,
        agent_port: int = DEFAULT_AGENT_PORT,
        demo_url: str = "http://localhost:8770/",
        mode: str = "fast",
        room: Optional[Any] = None,
        panel_sink: Optional[Any] = None,
        kb_retriever: Optional[Any] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._agent_port = agent_port
        self._demo_url = demo_url
        self._mode = mode
        self._room = room
        self._panel_sink = panel_sink
        # Optional KB (Moss) retriever override — injected for the offline test so
        # ``HeroRuntime.create`` does not reach for live Moss creds. None keeps the
        # live default (LIVE Moss, or CachedRetriever under CLARION_DEMO_MODE=1).
        self._kb_retriever = kb_retriever
        # Either a server relay (start_relay; standalone + tests) or a broker
        # client (attach_broker; the live voice path). Both expose the CdpRelay
        # surface + .session + wait_connected, so everything downstream is shared.
        self.relay: Optional[Any] = None
        self.actuator: Optional[ExtensionActuator] = None
        self.runtime: Optional[HeroRuntime] = None

    async def start_relay(self) -> WebSocketCdpRelay:
        """Bind the WebSocket relay server and begin accepting the extension."""
        relay = WebSocketCdpRelay(host=self._host, port=self._port)

        def _on_start(msg: dict) -> None:
            _log(
                f"session.start — tabId={msg.get('tabId')} "
                f"url={msg.get('url')!r} title={msg.get('title')!r}"
            )

        def _on_end(msg: dict) -> None:
            _log(f"session.end — reason={msg.get('reason')!r}")

        relay.on_session_start = _on_start
        relay.on_session_end = _on_end
        await relay.start()
        self.relay = relay
        _log(f"relay listening on ws://{self._host}:{relay.port} — waiting for the extension")
        return relay

    async def attach_broker(self) -> BrokerCdpRelay:
        """Connect to the ALWAYS-ON relay broker as a client (the live voice path).

        The broker owns the FROZEN 8771 extension wire independently of voice; we
        dial its agent port (8773) and learn the tab's state from the broker's
        (live or replayed) ``session.start``. This is what decouples the tab
        bridge from voice dispatch: the port is already bound at boot, so we never
        bind it here and never gate it on the mic."""
        relay = BrokerCdpRelay(host=self._host, port=self._agent_port)

        def _on_start(msg: dict) -> None:
            _log(
                f"session.start (via broker) — tabId={msg.get('tabId')} "
                f"url={msg.get('url')!r} title={msg.get('title')!r}"
            )

        def _on_end(msg: dict) -> None:
            _log(f"session.end (via broker) — reason={msg.get('reason')!r}")

        relay.on_session_start = _on_start
        relay.on_session_end = _on_end
        await relay.connect()
        self.relay = relay
        _log(f"connected to relay broker at {relay.uri} — waiting for the tab")
        return relay

    async def wait_for_session(self, *, timeout: Optional[float] = None) -> dict:
        """Block until the extension connects AND emits its ``session.start`` frame.

        Returns the ``session`` dict (tabId/url/title). The relay surfaces the
        latest lifecycle state on ``relay.session``; we wait for the socket then
        for the frame (the extension emits it on attach)."""
        assert self.relay is not None, "start_relay() or attach_broker() first"
        await self.relay.wait_connected(timeout=timeout)
        # The lifecycle frame arrives just after the socket; poll briefly for it.
        deadline = None if timeout is None else asyncio.get_event_loop().time() + timeout
        while self.relay.session is None:
            if deadline is not None and asyncio.get_event_loop().time() > deadline:
                raise asyncio.TimeoutError("extension connected but sent no session.start")
            await asyncio.sleep(0.02)
        return self.relay.session

    async def build_runtime(self) -> HeroRuntime:
        """Build the ``ExtensionActuator`` over the started relay and assemble the
        ``HeroRuntime`` with it injected — the SAME stage graph + retrievers +
        PanelPublisher the hero flow uses, only the actuator transport differs."""
        assert self.relay is not None, "start_relay() or attach_broker() first"
        self.actuator = ExtensionActuator(self.relay)
        self.runtime = await HeroRuntime.create(
            self._demo_url,
            mode=self._mode,  # type: ignore[arg-type]
            room=self._room,
            actuator=self.actuator,
            panel_sink=self._panel_sink,
            kb_retriever=self._kb_retriever,
        )
        return self.runtime

    async def perceive_once(self) -> Any:
        """Read-only: number the live tab's interactive AXTree, print a summary,
        and publish a PanelState. Returns the perceived ``SelectorMap``."""
        assert self.actuator is not None and self.runtime is not None, "build_runtime() first"
        sm = await self.actuator.perceive()
        _log(
            f"perceived {len(sm.nodes)} interactive nodes "
            f"(~{sm.token_estimate} tokens): {_summarize_nodes(sm)}"
        )
        await self._publish(sm)
        return sm

    async def _publish(self, sm) -> None:
        """Publish a PanelState reflecting the perceived tree (the U1 panel wire).

        We seed a minimal ClarionState with the live page_index so the same
        ``to_panel_state`` → set_attributes path runs; publishing must never break
        the read-only loop."""
        from clarion.stages.graph import seed_stage_state

        try:
            state = seed_stage_state(
                goal="read this page", mode=self._mode, page_index=sm  # type: ignore[arg-type]
            )
            await self.runtime.publisher.publish(state)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 - publish must not break the loop
            _log(f"panel publish skipped: {exc!r}")

    async def run_readonly(self, *, interval: float = 0.0) -> int:
        """The read-only operator loop: perceive once (interval=0) or re-perceive
        every ``interval`` seconds until the session ends / the process is killed.
        No act, no fill, no click — §9 recording rules."""
        await self.perceive_once()
        if interval <= 0:
            _log("read-only single-perceive complete (set CLARION_EXT_PERCEIVE_INTERVAL>0 to loop)")
            return 0
        _log(f"read-only loop every {interval:.1f}s — Ctrl-C to stop")
        try:
            while self.relay is not None and self.relay.session is not None:
                await asyncio.sleep(interval)
                await self.perceive_once()
        except asyncio.CancelledError:  # pragma: no cover - operator Ctrl-C
            pass
        return 0

    async def aclose(self) -> None:
        """Tear down the relay (the ExtensionActuator holds no browser of its own;
        do NOT call HeroRuntime.close — that would close an actuator with no
        ``close``; the relay IS the resource to release)."""
        if self.relay is not None:
            await self.relay.close()


async def main() -> int:
    from dotenv import load_dotenv

    agent_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    load_dotenv(os.path.join(agent_root, ".env"))

    host = os.environ.get("CLARION_RELAY_HOST", DEFAULT_RELAY_HOST)
    port = int(os.environ.get("CLARION_RELAY_PORT", str(DEFAULT_RELAY_PORT)))
    demo_url = os.environ.get("DEMO_SITE_URL", "http://localhost:8770/")
    interval = float(os.environ.get("CLARION_EXT_PERCEIVE_INTERVAL", "0") or "0")

    print("=" * 72, flush=True)
    print("CLARION — EXTENSION RUNTIME (chrome.debugger / user's real tab)", flush=True)
    print("=" * 72, flush=True)
    if not extension_actuator_selected():
        _log(
            "note: CLARION_ACTUATOR is not 'extension' — this entrypoint always "
            "uses the extension transport; the flag only routes voice_entry/runtime."
        )

    def sink(panel, payload: str) -> None:
        print(
            f"  [PANEL->set_attributes] stage={panel.stage} step={panel.step} "
            f"consent={panel.consent_state}",
            flush=True,
        )

    ext = ExtensionRuntime(host=host, port=port, demo_url=demo_url, panel_sink=sink)
    try:
        await ext.start_relay()
        _log("press the extension shortcut (Ctrl/Cmd+Shift+Y) on your tab to attach…")
        session = await ext.wait_for_session(timeout=None)
        _log(f"attached to tab: url={session.get('url')!r} title={session.get('title')!r}")
        await ext.build_runtime()
        return await ext.run_readonly(interval=interval)
    finally:
        await ext.aclose()


__all__ = [
    "ExtensionRuntime",
    "extension_actuator_selected",
    "ACTUATOR_ENV",
    "DEFAULT_RELAY_HOST",
    "DEFAULT_RELAY_PORT",
]


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
