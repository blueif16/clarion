"""Always-on CDP relay BROKER — the tab bridge, decoupled from voice.

Why this exists (the coupling bug it fixes): the ``chrome.debugger`` tab bridge
(port 8771) must be reachable the instant the user presses the shortcut — BEFORE
any voice dispatch or mic grant. But the LiveKit worker runs each job in a
SUBPROCESS (verified: job pid ≠ worker pid), so a relay bound *inside* a job
can't be bound at boot and can't be reached by a sibling process. Nesting the
relay in the job is exactly what gated the port behind voice: 8771 only opened
when a participant joined the room, which needed the mic. The human could never
trust the port was open.

This broker breaks that coupling. It is a standalone, ALWAYS-ON process started
by ``clarion-up`` that owns 8771 independently of voice. The MV3 extension
connects to 8771 exactly as before — the Relay-protocol-v1 wire is UNTOUCHED
(``docs/extension-build.md``). The agent's ``ExtensionActuator`` connects to a
second loopback port (8773) as a CLIENT (see ``relay.BrokerCdpRelay``), and the
broker pipes CDP frames between the two.

Topology (loopback only)::

    extension SW  ──ws:8771──▶  ┌──────────┐  ◀──ws:8773──  agent actuator
    (FROZEN v1 wire)            │  BROKER  │                (BrokerCdpRelay)
                                └──────────┘

The broker is a DUMB bidirectional frame pipe with ONE piece of state: it caches
the extension's last ``session.start`` and replays it to a freshly-connected
agent. A new job dispatch reconnects mid-session and must learn the tab is
already attached — otherwise ``wait_for_session`` would hang waiting for a frame
that already flew by. It never interprets CDP: it forwards ``method``/``params``
and ``id``s verbatim, so the v1 protocol stays the single source of truth.

Run:  python -m clarion.actuator.relay_broker
Env:  CLARION_RELAY_HOST       (default 127.0.0.1)
      CLARION_RELAY_PORT       (extension side, default 8771)
      CLARION_RELAY_AGENT_PORT (agent side,     default 8773)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Optional

from clarion.actuator.relay import (
    DEFAULT_AGENT_PORT,
    DEFAULT_BROKER_HOST,
    DEFAULT_EXT_PORT,
)


def _log(msg: str) -> None:
    print(f"  [relay-broker] {msg}", flush=True)


def _peek_type(text: str) -> Optional[str]:
    """The frame's ``type`` (for session caching) — never the payload."""
    try:
        msg = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(msg, dict) and isinstance(msg.get("type"), str):
        return msg["type"]
    return None


def _as_text(raw: Any) -> str:
    return raw if isinstance(raw, str) else raw.decode("utf-8", "replace")


class RelayBroker:
    """Bridges the extension socket (8771) and the agent socket (8773).

    One extension connection and one agent connection at a time (the relay is 1:1
    with a tab and there is a single voice session). A new connection on either
    side replaces the old one. The only retained state is ``_cached_session_start``
    so a late-joining agent learns an already-attached tab.
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_BROKER_HOST,
        ext_port: int = DEFAULT_EXT_PORT,
        agent_port: int = DEFAULT_AGENT_PORT,
    ) -> None:
        self._host = host
        self._ext_port = ext_port
        self._agent_port = agent_port
        self._ext_conn: Any = None
        self._agent_conn: Any = None
        self._cached_session_start: Optional[str] = None
        self._ext_server: Any = None
        self._agent_server: Any = None

    async def start(self) -> "RelayBroker":
        """Bind BOTH server sockets. Returns once they are listening — so the
        caller can prove the port is open before anything else runs."""
        from websockets.asyncio.server import serve

        self._ext_server = await serve(self._handle_ext, self._host, self._ext_port)
        self._agent_server = await serve(
            self._handle_agent, self._host, self._agent_port
        )
        _log(
            f"listening — extension ws://{self._host}:{self._ext_port} "
            f"· agent ws://{self._host}:{self._agent_port}"
        )
        return self

    async def serve_forever(self) -> None:
        await self.start()
        _log("ready — waiting for the extension (8771) and the agent (8773)")
        await asyncio.Event().wait()  # park forever; servers run in the background

    async def close(self) -> None:
        for srv in (self._ext_server, self._agent_server):
            if srv is not None:
                srv.close()
                await srv.wait_closed()

    # --- extension side (8771; the FROZEN v1 wire) --------------------------

    async def _handle_ext(self, conn: Any) -> None:
        if self._ext_conn is not None:
            await _safe_close(self._ext_conn)  # 1:1 with a tab — replace the old
        self._ext_conn = conn
        _log("extension connected")
        try:
            async for raw in conn:
                await self._on_ext_frame(_as_text(raw))
        except Exception:  # noqa: BLE001 - peer often drops without a close frame
            pass
        finally:
            if self._ext_conn is conn:
                self._ext_conn = None
                self._cached_session_start = None
                _log("extension disconnected — telling the agent the tab is gone")
                # Synthesize a session.end so the agent's relay drops its session
                # state (and any in-flight CDP futures fail fast, not hang).
                await self._to_agent(
                    json.dumps({"type": "session.end", "reason": "extension-gone"})
                )

    async def _on_ext_frame(self, text: str) -> None:
        mtype = _peek_type(text)
        if mtype == "session.start":
            self._cached_session_start = text
            _log("session.start cached + forwarded to the agent")
        elif mtype == "session.end":
            self._cached_session_start = None
        # Every extension frame (cdp.result / cdp.error / cdp.event / lifecycle)
        # is forwarded to the agent verbatim — the broker never reads the payload.
        await self._to_agent(text)

    # --- agent side (8773; BrokerCdpRelay) ----------------------------------

    async def _handle_agent(self, conn: Any) -> None:
        if self._agent_conn is not None:
            await _safe_close(self._agent_conn)
        self._agent_conn = conn
        _log("agent connected")
        # Replay the cached session.start so a job that dispatched AFTER the tab
        # was attached still learns about it (otherwise wait_for_session hangs).
        if self._cached_session_start is not None:
            await _safe_send(conn, self._cached_session_start)
            _log("replayed cached session.start to the agent")
        try:
            async for raw in conn:
                await self._on_agent_frame(_as_text(raw))
        except Exception:  # noqa: BLE001 - peer often drops without a close frame
            pass
        finally:
            if self._agent_conn is conn:
                self._agent_conn = None
                _log("agent disconnected (tab stays attached for the next job)")

    async def _on_agent_frame(self, text: str) -> None:
        # Agent → extension: CDP commands, forwarded verbatim by id.
        if self._ext_conn is None:
            _log("agent CDP frame dropped — no extension attached yet")
            return
        await _safe_send(self._ext_conn, text, on_fail="forward to extension failed")

    async def _to_agent(self, text: str) -> None:
        if self._agent_conn is None:
            return
        await _safe_send(self._agent_conn, text, on_fail="forward to agent failed")


async def _safe_send(conn: Any, text: str, *, on_fail: str = "send failed") -> None:
    try:
        await conn.send(text)
    except Exception as exc:  # noqa: BLE001 - the broker must never crash on a peer
        _log(f"{on_fail}: {exc!r}")


async def _safe_close(conn: Any) -> None:
    try:
        await conn.close()
    except Exception:  # noqa: BLE001
        pass


async def main() -> int:
    host = os.environ.get("CLARION_RELAY_HOST", DEFAULT_BROKER_HOST)
    ext_port = int(os.environ.get("CLARION_RELAY_PORT", str(DEFAULT_EXT_PORT)))
    agent_port = int(os.environ.get("CLARION_RELAY_AGENT_PORT", str(DEFAULT_AGENT_PORT)))

    print("=" * 72, flush=True)
    print("CLARION — RELAY BROKER (always-on tab bridge; independent of voice)", flush=True)
    print("=" * 72, flush=True)
    broker = RelayBroker(host=host, ext_port=ext_port, agent_port=agent_port)
    try:
        await broker.serve_forever()
    except (KeyboardInterrupt, asyncio.CancelledError):  # pragma: no cover
        _log("stopped")
    finally:
        await broker.close()
    return 0


__all__ = ["RelayBroker"]


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
