"""CDP relay transports for ``ExtensionActuator`` (Relay protocol v1 — FROZEN).

The shared §4 pipeline (``actuator/pipeline.py``) is pure over the three raw CDP
responses, so a second ``Actuator`` transport only has to ship CDP commands and
return their ``result`` dicts. This module defines that seam — ``CdpRelay`` — and
two implementations:

  - ``WebSocketCdpRelay`` — Python is the **server**; the Chrome MV3 extension
    service-worker connects as the **client** and forwards each command to
    ``chrome.debugger.sendCommand``. Request/reply correlated by integer ``id``.
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

No provider SDK and no Playwright import lives here — only the websocket library
(kept entirely inside this file) plus the stdlib.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, Union


class CdpError(RuntimeError):
    """A ``cdp.error`` reply from the relay (the remote CDP call failed)."""


class CdpRelay(ABC):
    """Async CDP transport: send a CDP ``method``+``params``, get its ``result``.

    The contract mirrors ``CDPSession.send`` — a single call returns the CDP
    command's ``result`` object (NOT the envelope) and raises ``CdpError`` on a
    ``cdp.error``. ``ExtensionActuator`` depends only on this surface, so it is
    transport-agnostic across the live websocket and the in-memory fake.
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
# WebSocket-server relay (Python is the server; the extension is the client).
# ---------------------------------------------------------------------------


class WebSocketCdpRelay(CdpRelay):
    """A ``CdpRelay`` backed by a loopback WebSocket **server** (Relay v1).

    Python binds ``ws://127.0.0.1:8771`` and waits for the extension
    service-worker to connect as the client. Each ``send`` allocates a monotone
    integer ``id``, frames the FROZEN ``{"id", "type":"cdp", "method", "params"}``
    command, and awaits the matching ``cdp.result`` / ``cdp.error`` by ``id``.

    ``session.start`` / ``session.end`` lifecycle frames are surfaced via
    ``on_session_start`` / ``on_session_end`` callbacks and the ``session`` dict;
    optional ``cdp.event`` frames go to ``on_cdp_event``. The websocket import is
    confined to this method body so the rest of the package (and the deterministic
    ``.[test]`` gate) never imports it.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8771,
    ) -> None:
        self._host = host
        self._port = port
        self._next_id = 0
        # id -> Future awaiting that reply.
        self._pending: dict[int, "asyncio.Future[dict]"] = {}
        # The single active extension connection (the relay is 1:1 with a tab).
        self._conn: Any = None
        self._server: Any = None
        self._reader_task: Optional[asyncio.Task] = None
        # An extension connected (the reader loop is live).
        self._connected: asyncio.Event = asyncio.Event()
        # Latest lifecycle state seen from the extension.
        self.session: Optional[dict] = None
        # Optional observer hooks (lifecycle + CDP events).
        self.on_session_start: Optional[Callable[[dict], Any]] = None
        self.on_session_end: Optional[Callable[[dict], Any]] = None
        self.on_cdp_event: Optional[Callable[[dict], Any]] = None

    # --- lifecycle ----------------------------------------------------------

    async def start(self) -> "WebSocketCdpRelay":
        """Bind the server socket and begin accepting the extension connection."""
        # Import kept INSIDE this file (and lazily) so neither the package nor the
        # deterministic test gate gains a hard dependency on the ws library.
        from websockets.asyncio.server import serve

        self._server = await serve(self._handle_conn, self._host, self._port)
        return self

    @property
    def port(self) -> int:
        """The bound port (useful when constructed with port 0 → ephemeral)."""
        if self._server is not None and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return self._port

    async def wait_connected(self, timeout: Optional[float] = None) -> None:
        """Block until the extension client has connected (the reader is live)."""
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)

    async def close(self) -> None:
        """Tear down the server and fail any in-flight requests."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._reader_task is not None:
            self._reader_task.cancel()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(CdpError("relay closed"))
        self._pending.clear()

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

    # --- CdpRelay -----------------------------------------------------------

    async def send(self, method: str, params: Optional[dict] = None) -> dict:
        import json

        if self._conn is None:
            # An extension may not have connected yet — wait briefly so a perceive
            # issued right after start() doesn't race the handshake.
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
            return await fut
        finally:
            self._pending.pop(req_id, None)


__all__ = ["CdpRelay", "CdpError", "FakeRelay", "WebSocketCdpRelay"]
