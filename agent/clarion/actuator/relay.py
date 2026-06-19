"""CDP relay transports for ``ExtensionActuator`` (Relay protocol v1 — FROZEN).

The shared §4 pipeline (``actuator/pipeline.py``) is pure over the three raw CDP
responses, so a second ``Actuator`` transport only has to ship CDP commands and
return their ``result`` dicts. This module defines that seam — ``CdpRelay`` — and
its implementations:

  - ``WebSocketCdpRelay`` — Python is the **server**; the Chrome MV3 extension
    service-worker connects as the **client** and forwards each command to
    ``chrome.debugger.sendCommand``. Used by the always-on broker and the
    standalone read-only operator entrypoint + the live tests.
  - ``BrokerCdpRelay`` — a **client** that dials OUT to the always-on relay
    broker's agent port (see ``relay_broker``). This is the live voice path: the
    broker owns the FROZEN 8771 wire independently of voice, and the actuator
    reaches the tab through the broker instead of binding the port itself.
  - ``FakeRelay`` — an in-memory relay for tests (canned ``method -> result``),
    recording every ``(method, params)`` it is asked to send.

Wire protocol (docs/extension-build.md — FROZEN, do not change):

  - Python → ext (command):
      ``{"id": <int>, "type": "cdp", "method": "<Domain.cmd>", "params": {…}}``
  - ext → Python (reply):
      ``{"id": <int>, "type": "cdp.result", "result": {…}}``  or
      ``{"id": <int>, "type": "cdp.error", "error": "<msg>"}``
  - ext → Python (lifecycle):
      ``{"type": "session.start", "tabId": <int>, "url": "…", "title": "…"}``  /
      ``{"type": "session.end", "reason": "…"}``
  - ext → Python (CDP event, optional):
      ``{"type": "cdp.event", "method": "…", "params": {…}}``

The two WebSocket relays speak this identical wire and share their entire
reply-correlation machinery (``_CorrelatedCdpRelay``); they differ ONLY in how
the socket is established (``serve`` vs ``connect``). No provider SDK and no
Playwright import lives here — only the websocket library (kept inside the method
bodies) plus the stdlib.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, Union

# Loopback ports for the relay topology. The extension side (8771) is the FROZEN
# v1 wire the MV3 service-worker connects to; the agent side (8773) is the broker
# ⇄ ExtensionActuator channel. Centralized here so the broker, the client relay,
# and ExtensionRuntime never drift.
DEFAULT_BROKER_HOST = "127.0.0.1"
DEFAULT_EXT_PORT = 8771
DEFAULT_AGENT_PORT = 8773

# The ``websockets`` library caps incoming messages at ``max_size`` — DEFAULT 1 MiB
# (2**20) — and CLOSES the socket (close code 1009, MESSAGE_TOO_BIG) on any larger
# frame. A single ``Accessibility.getFullAXTree`` / ``DOMSnapshot.captureSnapshot``
# reply for a heavy page (e.g. recreation.gov) exceeds 1 MiB, so the default
# SILENTLY dropped the relay mid-perceive (surfacing as ``session.end
# extension-gone`` at the broker) and the agent's in-flight CDP call then hung to
# the 30s ``SEND_TIMEOUT``. The relay is loopback-only and 1:1 with one trusted
# tab, so we lift the cap (``None`` = no limit) on EVERY read endpoint — both broker
# servers, this server, and the broker client — so a real perceive is never
# truncated. (Lighter sites fit under 1 MiB, which is why it "worked before".)
RELAY_MAX_MESSAGE_BYTES: "int | None" = None


class CdpError(RuntimeError):
    """A ``cdp.error`` reply from the relay (the remote CDP call failed)."""


class CdpRelay(ABC):
    """Async CDP transport: send a CDP ``method``+``params``, get its ``result``.

    The contract mirrors ``CDPSession.send`` — a single call returns the CDP
    command's ``result`` object (NOT the envelope) and raises ``CdpError`` on a
    ``cdp.error``. ``ExtensionActuator`` depends only on this surface, so it is
    transport-agnostic across the live websocket relays and the in-memory fake.
    """

    @abstractmethod
    async def send(self, method: str, params: Optional[dict] = None) -> dict:
        """Issue one CDP command and return its ``result`` dict (raise on error)."""
        ...


# ---------------------------------------------------------------------------
# In-memory relay for tests.
# ---------------------------------------------------------------------------

# A canned-result map is either a dict (method -> result) or a callable taking
# (method, params) and returning the result dict.
CannedMap = Union[
    dict[str, dict],
    Callable[[str, Optional[dict]], dict],
]


class FakeRelay(CdpRelay):
    """In-memory ``CdpRelay`` for tests.

    Construct with a ``method -> result`` dict (or a ``(method, params) ->
    result`` callable). Every ``send`` is recorded in ``.sent`` as an
    ``(method, params)`` tuple so a test can assert the exact CDP traffic an act
    produced. A missing method raises ``CdpError`` (the same surface a real
    ``cdp.error`` would) unless ``default`` is supplied.
    """

    def __init__(
        self,
        canned: Optional[CannedMap] = None,
        *,
        default: Optional[dict] = None,
    ) -> None:
        self._canned = canned if canned is not None else {}
        self._default = default
        # Recorded traffic — list of (method, params) in call order.
        self.sent: list[tuple[str, Optional[dict]]] = []

    async def send(self, method: str, params: Optional[dict] = None) -> dict:
        self.sent.append((method, params))
        if callable(self._canned):
            return self._canned(method, params)
        if method in self._canned:
            return self._canned[method]
        if self._default is not None:
            return self._default
        raise CdpError(f"FakeRelay: no canned result for {method!r}")


# ---------------------------------------------------------------------------
# Shared reply-correlation for the WebSocket relays (server + broker client).
# ---------------------------------------------------------------------------


class _CorrelatedCdpRelay(CdpRelay):
    """Everything the two WebSocket transports share: the monotone request id,
    the pending-future map keyed by ``id``, the inbound-frame dispatch, the
    lifecycle/``session`` state, and the ``send`` that frames a command and awaits
    its reply. Subclasses only establish ``self._conn`` (serve vs connect) and
    own teardown.

    The wire is identical for both, so the framing lives here ONCE — a single
    source of truth for the v1 protocol on the Python side.
    """

    # A single CDP command shouldn't take longer than this; if it does the
    # extension/relay has stalled and we fail loudly rather than hang a turn.
    SEND_TIMEOUT = 30.0

    def __init__(self) -> None:
        self._next_id = 0
        # id -> Future awaiting that reply.
        self._pending: dict[int, "asyncio.Future[dict]"] = {}
        # The single active socket (server: the extension; client: the broker).
        self._conn: Any = None
        # The socket is live (the reader loop is running).
        self._connected: asyncio.Event = asyncio.Event()
        # Latest lifecycle state seen from the extension (via the broker for the
        # client). ``None`` until a ``session.start`` arrives / after ``end``.
        self.session: Optional[dict] = None
        # Optional observer hooks (lifecycle + CDP events).
        self.on_session_start: Optional[Callable[[dict], Any]] = None
        self.on_session_end: Optional[Callable[[dict], Any]] = None
        self.on_cdp_event: Optional[Callable[[dict], Any]] = None

    async def wait_connected(self, timeout: Optional[float] = None) -> None:
        """Block until the socket is live (server: the extension connected;
        client: connected to the broker)."""
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)

    # --- inbound dispatch (identical for both transports) -------------------

    def _dispatch(self, raw: Any) -> None:
        """Route one inbound text frame by its FROZEN ``type``."""
        import json

        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        mtype = msg.get("type")
        if mtype == "cdp.result":
            fut = self._pending.pop(msg.get("id"), None)
            if fut is not None and not fut.done():
                fut.set_result(msg.get("result") or {})
        elif mtype == "cdp.error":
            fut = self._pending.pop(msg.get("id"), None)
            if fut is not None and not fut.done():
                fut.set_exception(CdpError(str(msg.get("error"))))
        elif mtype == "session.start":
            self.session = msg
            self._fire(self.on_session_start, msg)
        elif mtype == "session.end":
            self.session = None
            self._fire(self.on_session_end, msg)
        elif mtype == "cdp.event":
            self._fire(self.on_cdp_event, msg)

    def _fire(self, cb: Optional[Callable[[dict], Any]], msg: dict) -> None:
        if cb is None:
            return
        res = cb(msg)
        if asyncio.iscoroutine(res):
            asyncio.ensure_future(res)

    def _fail_pending(self, exc: BaseException) -> None:
        """Fail every in-flight request (called on close / socket drop) so a
        caller awaiting a CDP reply never hangs forever."""
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    # --- CdpRelay -----------------------------------------------------------

    async def send(self, method: str, params: Optional[dict] = None) -> dict:
        import json

        if self._conn is None:
            # The peer may not have connected yet — wait briefly so a perceive
            # issued right after start()/connect() doesn't race the handshake.
            await self.wait_connected(timeout=10.0)
        assert self._conn is not None
        self._next_id += 1
        req_id = self._next_id
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[dict]" = loop.create_future()
        self._pending[req_id] = fut
        frame = {
            "id": req_id,
            "type": "cdp",
            "method": method,
            "params": params or {},
        }
        await self._conn.send(json.dumps(frame))
        try:
            return await asyncio.wait_for(fut, timeout=self.SEND_TIMEOUT)
        except asyncio.TimeoutError:
            raise CdpError(
                f"CDP {method!r} timed out after {self.SEND_TIMEOUT:.0f}s "
                "(relay/extension stalled?)"
            )
        finally:
            self._pending.pop(req_id, None)


# ---------------------------------------------------------------------------
# WebSocket-server relay (Python is the server; the extension is the client).
# ---------------------------------------------------------------------------


class WebSocketCdpRelay(_CorrelatedCdpRelay):
    """A ``CdpRelay`` backed by a loopback WebSocket **server** (Relay v1).

    Python binds ``ws://127.0.0.1:8771`` and waits for the extension
    service-worker to connect as the client. Used by the always-on ``relay_broker``
    (which owns the live extension wire) and the standalone read-only operator
    entrypoint + the live tests. The live VOICE path uses ``BrokerCdpRelay``
    instead — it must not bind the port itself.
    """

    def __init__(
        self,
        host: str = DEFAULT_BROKER_HOST,
        port: int = DEFAULT_EXT_PORT,
    ) -> None:
        super().__init__()
        self._host = host
        self._port = port
        self._server: Any = None

    # --- lifecycle ----------------------------------------------------------

    async def start(self) -> "WebSocketCdpRelay":
        """Bind the server socket and begin accepting the extension connection."""
        # Import kept INSIDE this file (and lazily) so neither the package nor the
        # deterministic test gate gains a hard dependency on the ws library.
        from websockets.asyncio.server import serve

        # max_size=None: a large getFullAXTree reply must not trip the 1 MiB cap and
        # drop the extension socket mid-perceive (see RELAY_MAX_MESSAGE_BYTES).
        self._server = await serve(
            self._handle_conn, self._host, self._port, max_size=RELAY_MAX_MESSAGE_BYTES
        )
        return self

    @property
    def port(self) -> int:
        """The bound port (useful when constructed with port 0 → ephemeral)."""
        if self._server is not None and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return self._port

    async def close(self) -> None:
        """Tear down the server and fail any in-flight requests."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._connected.clear()
        self._fail_pending(CdpError("relay closed"))

    # --- connection handling ------------------------------------------------

    async def _handle_conn(self, conn: Any) -> None:
        """Per-connection handler: this relay tracks a single extension client."""
        self._conn = conn
        self._connected.set()
        try:
            async for raw in conn:
                self._dispatch(raw)
        finally:
            if self._conn is conn:
                self._conn = None
                self._connected.clear()


# ---------------------------------------------------------------------------
# WebSocket-client relay (dials out to the always-on broker's agent port).
# ---------------------------------------------------------------------------


class BrokerCdpRelay(_CorrelatedCdpRelay):
    """A ``CdpRelay`` **client** that connects to the relay broker's AGENT port.

    The broker (``clarion.actuator.relay_broker``) owns the FROZEN extension wire
    on 8771, always-on and independent of voice dispatch. The agent's
    ``ExtensionActuator`` reaches the tab through the broker via this client on
    8773. Because the broker is always up, ``connect()`` succeeds immediately and
    the actuator learns the tab is attached via the broker's (live or replayed)
    ``session.start``. Same v1 framing + reply correlation as the server relay —
    only the socket is dialed out instead of served.
    """

    def __init__(
        self,
        host: str = DEFAULT_BROKER_HOST,
        port: int = DEFAULT_AGENT_PORT,
    ) -> None:
        super().__init__()
        self._host = host
        self._port = port
        self._reader_task: Optional[asyncio.Task] = None

    @property
    def uri(self) -> str:
        return f"ws://{self._host}:{self._port}"

    async def connect(self, *, timeout: float = 10.0) -> "BrokerCdpRelay":
        """Dial the broker's agent port and start the reader loop. Raises if the
        broker isn't up (the caller logs it and keeps the voice plane alive)."""
        from websockets.asyncio.client import connect as ws_connect

        async def _dial() -> Any:
            # max_size=None: the broker forwards the big getFullAXTree reply onto this
            # client socket, so it must accept frames over 1 MiB too — else the cap
            # just moves the drop downstream from the broker to here.
            return await ws_connect(self.uri, max_size=RELAY_MAX_MESSAGE_BYTES)

        self._conn = await asyncio.wait_for(_dial(), timeout=timeout)
        self._connected.set()
        self._reader_task = asyncio.ensure_future(self._read_loop())
        return self

    async def _read_loop(self) -> None:
        conn = self._conn
        try:
            async for raw in conn:
                self._dispatch(raw)
        except Exception:  # noqa: BLE001 - a dropped broker is handled below
            pass
        finally:
            if self._conn is conn:
                self._conn = None
                self._connected.clear()
                self.session = None
            self._fail_pending(CdpError("broker connection closed"))

    async def close(self) -> None:
        """Cancel the reader, close the socket, and fail in-flight requests."""
        if self._reader_task is not None:
            self._reader_task.cancel()
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None
        self._connected.clear()
        self._fail_pending(CdpError("broker relay closed"))


__all__ = [
    "CdpRelay",
    "CdpError",
    "FakeRelay",
    "WebSocketCdpRelay",
    "BrokerCdpRelay",
    "DEFAULT_BROKER_HOST",
    "DEFAULT_EXT_PORT",
    "DEFAULT_AGENT_PORT",
    "RELAY_MAX_MESSAGE_BYTES",
]
