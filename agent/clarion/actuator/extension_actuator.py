"""The §4 Actuator over the ``chrome.debugger`` CDP relay (the user's real tab).

This is the **second transport** for the same §4 perception pipeline. Where
``PlaywrightActuator`` issues CDP over Playwright's ``CDPSession`` against a
spawned browser, ``ExtensionActuator`` issues the *identical* CDP calls over a
``CdpRelay`` (the MV3 extension's ``chrome.debugger.sendCommand`` relay), so the
brain drives the user's own authenticated tab. The pure pipeline
(``actuator/pipeline.py``) is shared verbatim — only the transport and the per-node
id stamp differ — which is what the transport-parity test proves.

Pipeline (execution §4.1 — same as the Playwright transport):
  1. Enable ``DOM`` / ``Accessibility`` / ``DOMSnapshot`` / ``Runtime`` / ``Page``
     once, then **triple-fetch** ``DOM.getDocument`` +
     ``Accessibility.getFullAXTree`` + ``DOMSnapshot.captureSnapshot`` via the relay.
  2. ``parse_snapshot`` → ``PaintOrderRemover`` → ``build_candidates`` →
     ``containment_filter`` → ``order_reading`` (all shared, transport-free).
  3. Assign sequential indices, ``_stamp`` each kept node over the relay
     (``DOM.pushNodesByBackendIdsToFrontend`` + ``DOM.setAttributeValue``), and
     return a ``SelectorMap`` with ``estimate_tokens``.

Acting (execution §4.3):
  - ``fill``     → ``Runtime.evaluate`` of the shared ``_NATIVE_SETTER_JS`` with
    ``{clarionId, value}`` (``returnByValue: true``).
  - ``click``    → ``Input.dispatchMouseEvent`` press + release at the bbox center.
  - ``navigate`` → ``Page.navigate`` with the url.
  - ``read``     → ``Runtime.evaluate`` of the shared ``_READ_JS`` by clarion id.
  - After every act we re-perceive (CONFIRM reads the new tree).

NO Playwright import lives here — only the relay, the shared pipeline, and the
contracts. The kernel sees only the ``Actuator`` ABC.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from clarion.actuator.pipeline import (
    PaintOrderRemover,
    _NATIVE_SETTER_JS,
    _READ_JS,
    build_candidates,
    containment_filter,
    diff_maps,
    estimate_tokens,
    order_reading,
    parse_snapshot,
    summarize_ax_tree,
)
from clarion.actuator.relay import CdpRelay
from clarion.contracts.ports import Actuator
from clarion.contracts.state import (
    Action,
    AxNode,
    Observation,
    PageDiff,
    PageReadout,
    SelectorMap,
)

# The five CDP domains the triple-fetch + act path read/write — enabled once.
_DOMAINS = ("DOM", "Accessibility", "DOMSnapshot", "Runtime", "Page")


class ExtensionActuator(Actuator):
    """The a11y-tree Actuator over a ``CdpRelay`` (chrome.debugger / extension).

    Construct with any ``CdpRelay`` — the in-memory ``FakeRelay`` for tests, or
    the live ``WebSocketCdpRelay`` the MV3 extension connects to. ``perceive`` /
    ``act`` / ``diff`` mirror ``PlaywrightActuator`` exactly; the only difference
    is that CDP goes through ``relay.send(method, params)`` instead of a
    ``CDPSession``.
    """

    def __init__(self, relay: CdpRelay) -> None:
        self._relay = relay
        # index -> the clarion id we stamp on the real element for exact resolve.
        self._index_to_clarion_id: dict[int, str] = {}
        # index -> bbox [x,y,w,h] for coordinate clicks.
        self._index_to_bbox: dict[int, list[float]] = {}
        self._clarion_counter = 0
        self._domains_enabled = False

    # --- lifecycle ----------------------------------------------------------

    async def _enable_domains(self) -> None:
        """Enable the CDP domains once (idempotent across perceive calls)."""
        if self._domains_enabled:
            return
        await asyncio.gather(
            *(self._relay.send(f"{d}.enable") for d in _DOMAINS)
        )
        self._domains_enabled = True

    # --- Actuator port ------------------------------------------------------

    async def perceive(self) -> SelectorMap:
        """Run the §4.1 pipeline over the relay and return the numbered map."""
        await self._enable_domains()

        # 1. Parallel CDP triple-fetch — the exact same three calls (and params)
        #    PlaywrightActuator issues, just over the relay transport.
        _dom_doc, ax_tree, snapshot = await asyncio.gather(
            self._relay.send("DOM.getDocument", {"depth": -1, "pierce": True}),
            self._relay.send("Accessibility.getFullAXTree"),
            self._relay.send(
                "DOMSnapshot.captureSnapshot",
                {
                    "computedStyles": [],
                    "includePaintOrder": True,
                    "includeDOMRects": True,
                },
            ),
        )

        # 2-4. Shared §4 pipeline (parse → paint-order → candidates → containment).
        layout_by_backend, all_rects = parse_snapshot(snapshot)
        remover = PaintOrderRemover(all_rects)
        candidates = build_candidates(ax_tree, layout_by_backend, remover)
        kept = containment_filter(candidates)

        # 5. Sequential indices → SelectorMap, stamping each kept node via the
        #    relay so act/read resolve it exactly.
        nodes: dict[int, AxNode] = {}
        self._index_to_clarion_id = {}
        self._index_to_bbox = {}
        order_reading(kept)
        for index, c in enumerate(kept):
            clarion_id = await self._stamp(c["backend_id"])
            nodes[index] = AxNode(
                index=index,
                role=c["role"],
                name=c["name"],
                state=c["state"],
                bbox=c["bbox"],
                node_id=c["node_id"],
            )
            self._index_to_clarion_id[index] = clarion_id
            self._index_to_bbox[index] = c["bbox"]

        return SelectorMap(nodes=nodes, token_estimate=estimate_tokens(nodes))

    async def describe_page(self) -> PageReadout:
        """ORIENT readout over the relay transport — the SAME shared summarizer as
        the Playwright path. Fetches the full AXTree via the relay and reads the
        live tab's title/url with a small ``Runtime.evaluate``; every surfaced item
        is sourced to a real AX ``nodeId`` (foundation §1)."""
        await self._enable_domains()
        ax_tree = await self._relay.send("Accessibility.getFullAXTree")
        title = await self._eval_string("document.title")
        url = await self._eval_string("location.href")
        return summarize_ax_tree(ax_tree, title=title, url=url)

    async def _eval_string(self, expression: str) -> str:
        """Evaluate a string-returning JS expression over the relay (title/url for
        the readout). Best-effort: returns "" on any failure so ORIENT never breaks
        on a page that blocks evaluation."""
        try:
            res = await self._relay.send(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True},
            )
            value = (res.get("result") or {}).get("value")
            return str(value) if value is not None else ""
        except Exception:  # noqa: BLE001 - readout metadata is best-effort
            return ""

    async def act(self, action: Action) -> Observation:
        """Execute the action over the relay, then re-perceive (§4.3)."""
        if action.kind == "fill":
            return await self._do_fill(action)
        if action.kind == "click":
            return await self._do_click(action)
        if action.kind == "navigate":
            return await self._do_navigate(action)
        if action.kind == "read":
            return await self._do_read(action)
        return Observation(
            selector_map=await self.perceive(),
            success=False,
            detail=f"unknown action kind {action.kind!r}",
        )

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        """Page-diff by stable node identity — shared verbatim with the
        Playwright transport (``pipeline.diff_maps``)."""
        return diff_maps(before, after)

    async def perceive_vision(self) -> SelectorMap:
        """Vision fallback for AX-blind widgets — deferred (execution §4.2)."""
        raise NotImplementedError("vision fallback — deferred")

    # --- act helpers --------------------------------------------------------

    async def _do_fill(self, action: Action) -> Observation:
        if action.index is None or action.value is None:
            return Observation(
                selector_map=await self.perceive(),
                success=False,
                detail="fill requires index and value",
            )
        clarion_id = self._index_to_clarion_id.get(action.index)
        if clarion_id is None:
            return Observation(
                selector_map=await self.perceive(),
                success=False,
                detail=f"no element for index {action.index}",
            )
        # The native setter is an arrow fn taking one arg; over CDP we invoke it
        # inline with the {clarionId,value} payload baked in as a JSON literal.
        arg = json.dumps({"clarionId": clarion_id, "value": action.value})
        expr = f"({_NATIVE_SETTER_JS})({arg})"
        res = await self._relay.send(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True},
        )
        value = (res.get("result") or {}).get("value") or {}
        ok = bool(value.get("ok"))
        after = await self.perceive()
        return Observation(
            selector_map=after,
            success=ok,
            detail="" if ok else f"native-setter failed: {value.get('reason')}",
        )

    async def _do_click(self, action: Action) -> Observation:
        if action.index is None:
            return Observation(
                selector_map=await self.perceive(),
                success=False,
                detail="click requires index",
            )
        bbox = self._index_to_bbox.get(action.index)
        if bbox is None:
            return Observation(
                selector_map=await self.perceive(),
                success=False,
                detail=f"no element for index {action.index}",
            )
        # Click the bbox center — a paint-order-honest, real user click (press +
        # release), matching PlaywrightActuator's mouse.click but over raw CDP.
        cx = bbox[0] + bbox[2] / 2.0
        cy = bbox[1] + bbox[3] / 2.0
        common = {"x": cx, "y": cy, "button": "left", "clickCount": 1}
        await self._relay.send(
            "Input.dispatchMouseEvent", {"type": "mousePressed", **common}
        )
        await self._relay.send(
            "Input.dispatchMouseEvent", {"type": "mouseReleased", **common}
        )
        return Observation(selector_map=await self.perceive(), success=True)

    async def _do_navigate(self, action: Action) -> Observation:
        if not action.value:
            return Observation(
                selector_map=await self.perceive(),
                success=False,
                detail="navigate requires value (url)",
            )
        await self._relay.send("Page.navigate", {"url": action.value})
        return Observation(selector_map=await self.perceive(), success=True)

    async def _do_read(self, action: Action) -> Observation:
        if action.index is None:
            return Observation(
                selector_map=await self.perceive(),
                success=False,
                detail="read requires index",
            )
        clarion_id = self._index_to_clarion_id.get(action.index)
        if clarion_id is None:
            return Observation(
                selector_map=await self.perceive(),
                success=False,
                detail=f"no element for index {action.index}",
            )
        value = await self._read_clarion(clarion_id)
        return Observation(
            selector_map=await self.perceive(),
            success=value is not None,
            detail="" if value is None else str(value),
        )

    async def read_value(self, index: int) -> Optional[str]:
        """Live ``.value`` read-back for an input by index (test/assert utility).

        Not part of the ``Actuator`` port — the honest way to prove a field was
        actually filled (vs merely that we *called* fill). Mirrors the Playwright
        transport's read-back utility."""
        clarion_id = self._index_to_clarion_id.get(index)
        if clarion_id is None:
            return None
        value = await self._read_clarion(clarion_id)
        return None if value is None else str(value)

    async def _read_clarion(self, clarion_id: str) -> Optional[object]:
        """Run the shared ``_READ_JS`` for one clarion id over the relay."""
        arg = json.dumps(clarion_id)
        expr = f"({_READ_JS})({arg})"
        res = await self._relay.send(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True},
        )
        return (res.get("result") or {}).get("value")

    # --- perception internals (transport-specific) -------------------------
    # The pure §4 pipeline lives in clarion.actuator.pipeline and is shared
    # verbatim with PlaywrightActuator. Only the CDP transport + the id stamp
    # differ per actuator.

    async def _stamp(self, backend_id: int) -> str:
        """Stamp a stable ``data-clarion-id`` on the real element so act/read can
        resolve it exactly. Resolves the backendNodeId → a frontend nodeId via
        ``DOM.pushNodesByBackendIdsToFrontend`` then sets the attribute with
        ``DOM.setAttributeValue`` — identical CDP to the Playwright transport."""
        self._clarion_counter += 1
        clarion_id = f"cl-{self._clarion_counter}"
        try:
            pushed = await self._relay.send(
                "DOM.pushNodesByBackendIdsToFrontend",
                {"backendNodeIds": [backend_id]},
            )
            node_ids = pushed.get("nodeIds", [])
            if node_ids:
                await self._relay.send(
                    "DOM.setAttributeValue",
                    {
                        "nodeId": node_ids[0],
                        "name": "data-clarion-id",
                        "value": clarion_id,
                    },
                )
        except Exception:
            pass
        return clarion_id


__all__ = ["ExtensionActuator"]
