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
  - ``click``    → ``cdp_click_by_backend`` (scroll into view → viewport quads →
    trusted ``Input.dispatchMouseEvent`` press+release at the quad centre).
  - ``navigate`` → ``Page.navigate`` with the url.
  - ``read``     → ``Runtime.evaluate`` of the shared ``_READ_JS`` by clarion id.
  - After every act we re-perceive (CONFIRM reads the new tree).

NO Playwright import lives here — only the relay, the shared pipeline, and the
contracts. The kernel sees only the ``Actuator`` ABC.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from clarion.actuator.pipeline import (
    PaintOrderRemover,
    _NATIVE_SETTER_JS,
    _NODE_STATE_JS,
    _READ_JS,
    ax_node_geometry,
    build_candidates,
    cdp_click_by_backend,
    containment_filter,
    diff_maps,
    estimate_tokens,
    extract_paired_facts,
    extract_text_facts,
    order_reading,
    parse_snapshot,
    summarize_ax_tree,
)
from clarion.actuator.relay import CdpRelay
from clarion.contracts.ports import Actuator
from clarion.contracts.state import (
    Action,
    AxNode,
    Fact,
    Observation,
    PageDiff,
    PageReadout,
    PairedFact,
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
        # Filled LAZILY: empty after perceive; an entry appears only once an index
        # is actually acted/read (``_ensure_stamped``).
        self._index_to_clarion_id: dict[int, str] = {}
        # index -> backend node id (in hand from perceive; the input to lazy stamp).
        self._index_to_backend_id: dict[int, int] = {}
        # index -> bbox [x,y,w,h] for perception/readout + the DeliveryGate's
        # freshness re-check. NOT a click target — clicks resolve by backend id.
        self._index_to_bbox: dict[int, list[float]] = {}
        # index -> stable node_id (so a fill can record WHICH node it filled).
        self._index_to_node_id: dict[int, str] = {}
        self._clarion_counter = 0
        self._domains_enabled = False
        # node_id of every input we have SUCCESSFULLY filled — re-stamped as
        # ``state["filled"]=True`` in later perceive maps (the AX tree drops the
        # typed value). Parity with the Playwright transport's same tracker.
        self._filled_node_ids: set[str] = set()

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
        """Run the §4.1 pipeline over the relay and return the numbered map.

        **Lazy stamping (migration step 1):** perceive stamps ZERO nodes. It runs
        the 3-call triple-fetch + the pure pipeline, then records only
        ``index -> backend_id`` (already in hand). The ``data-clarion-id`` for an
        index is written on the FIRST act/read/read_value on it (``_ensure_stamped``
        — one push + one setAttribute). This collapses the old ~2-round-trip-per-
        node loop (90 round-trips on a 45-node page) to 0 in perceive and ~2 only at
        act time. Parity with the Playwright transport is preserved: the returned
        SelectorMap's ``(index, role, name, bbox)`` are produced identically — only
        WHEN the id is stamped moved (perceive → first resolve)."""
        t0 = time.perf_counter()
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

        # 5. Sequential indices → SelectorMap. LAZY: record index -> backend_id and
        #    leave the clarion-id unstamped; the first act/read on an index stamps
        #    only that one node. NO per-node relay round-trip here.
        nodes: dict[int, AxNode] = {}
        self._index_to_clarion_id = {}
        self._index_to_backend_id = {}
        self._index_to_bbox = {}
        self._index_to_node_id = {}
        order_reading(kept)
        for index, c in enumerate(kept):
            state = dict(c["state"])
            # Re-stamp the actuator-known `filled` flag (the AX tree drops the typed
            # value; restore the signal the generic done-check needs). Parity with
            # the Playwright transport.
            if c["node_id"] in self._filled_node_ids:
                state["filled"] = True
            nodes[index] = AxNode(
                index=index,
                role=c["role"],
                name=c["name"],
                state=state,
                bbox=c["bbox"],
                node_id=c["node_id"],
            )
            self._index_to_backend_id[index] = c["backend_id"]
            self._index_to_bbox[index] = c["bbox"]
            self._index_to_node_id[index] = c["node_id"]

        sm = SelectorMap(nodes=nodes, token_estimate=estimate_tokens(nodes))
        perceive_ms = (time.perf_counter() - t0) * 1000.0
        # Emit the migration latency number so it lands in /tmp/clarion-worker.log.
        # Lazy stamping: 3 fetch round-trips, 0 stamp round-trips in perceive.
        print(
            f"  [lat] perceive_ms={perceive_ms:.1f} nodes={len(nodes)} "
            f"stamp_round_trips=0 (lazy)",
            flush=True,
        )
        return sm

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

    async def read_facts(self) -> list[Fact]:
        """GROUND source over the relay transport — the SAME shared pure
        ``extract_text_facts`` as the Playwright path. Harvests the live tab's
        readable text as grounded ``Fact``s (each sourced to a real AX ``nodeId``)
        so the kernel's GROUND reads the user's REAL page, never a fixture
        (foundation §1). The ``PageRetriever`` calls this."""
        await self._enable_domains()
        ax_tree = await self._relay.send("Accessibility.getFullAXTree")
        return extract_text_facts(ax_tree)

    async def read_paired_facts(self) -> list[PairedFact]:
        """PARSE source over the relay transport — the SAME shared pure
        ``extract_paired_facts`` as the Playwright path. Fetches the AXTree + the
        DOMSnapshot via the relay (for the ``shared-row`` geometry, keyed back to AX
        nodeIds) and harvests geometric label↔value ``PairedFact``s; BOTH halves of
        every pairing are sourced to a real AX ``nodeId`` (killer-closer #1).
        Symmetric with ``PlaywrightActuator.read_paired_facts``."""
        await self._enable_domains()
        ax_tree, snapshot = await asyncio.gather(
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
        layout_by_backend, _ = parse_snapshot(snapshot)
        geometry = ax_node_geometry(ax_tree, layout_by_backend)
        return extract_paired_facts(ax_tree, geometry=geometry)

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
        clarion_id = await self._ensure_stamped(action.index)
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
        if ok:
            # Record the filled node by stable node_id BEFORE re-perceiving, so the
            # fresh map stamps ``state["filled"]=True`` (parity with Playwright).
            filled_node_id = self._index_to_node_id.get(action.index)
            if filled_node_id:
                self._filled_node_ids.add(filled_node_id)
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
        backend_id = self._index_to_backend_id.get(action.index)
        if backend_id is None:
            return Observation(
                selector_map=await self.perceive(),
                success=False,
                detail=f"no element for index {action.index}",
            )
        # Identity-targeted, coordinate-free: the browser scrolls the node into
        # view and reports its live viewport quads — a real (trusted) click on the
        # actually-visible element. Shared verbatim with PlaywrightActuator.
        ok, detail = await cdp_click_by_backend(self._relay.send, backend_id)
        print(
            f"  [click] idx={action.index} backend={backend_id} ok={ok} {detail}",
            flush=True,
        )
        return Observation(
            selector_map=await self.perceive(), success=ok, detail=detail
        )

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
        clarion_id = await self._ensure_stamped(action.index)
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
        clarion_id = await self._ensure_stamped(index)
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

    async def reperceive_node(self, index: int) -> Optional[AxNode]:
        """Target-node-only incremental re-perceive (migration step 1).

        Re-read ONE node's live geometry + name in a single round-trip (after
        lazy-stamping it once), without rebuilding the whole map — the cheap
        freshness re-check the DeliveryGate will use between a "yes" and the act so
        a stale index is caught instead of clicked. Returns a fresh ``AxNode`` for
        the index, or ``None`` if the node is gone or the index isn't mapped. The
        role is carried over from the last full perceive (it doesn't change for a
        stable element); bbox/name/disabled come live from the page."""
        clarion_id = await self._ensure_stamped(index)
        if clarion_id is None:
            return None
        arg = json.dumps(clarion_id)
        expr = f"({_NODE_STATE_JS})({arg})"
        res = await self._relay.send(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True},
        )
        state = (res.get("result") or {}).get("value")
        if not state:
            return None
        prev = self._index_to_bbox.get(index)
        bbox = state.get("bbox") or prev or [0.0, 0.0, 0.0, 0.0]
        self._index_to_bbox[index] = bbox
        return AxNode(
            index=index,
            role="",  # not re-read; the caller already knows it from perceive
            name=state.get("name") or "",
            state={"disabled": bool(state.get("disabled"))},
            bbox=bbox,
            node_id="",
        )

    # --- perception internals (transport-specific) -------------------------
    # The pure §4 pipeline lives in clarion.actuator.pipeline and is shared
    # verbatim with PlaywrightActuator. Only the CDP transport + the id stamp
    # differ per actuator.

    async def _ensure_stamped(self, index: int) -> Optional[str]:
        """Lazy-resolve an index to its ``data-clarion-id``, stamping the single
        node on first use (migration step 1). Returns the clarion id (stamping it
        via ``_stamp`` if not yet stamped) or ``None`` if the index isn't in the
        current map. This is the ~2-round-trip cost the eager perceive loop used to
        pay for EVERY node — now paid once, only for the node actually acted on."""
        cached = self._index_to_clarion_id.get(index)
        if cached is not None:
            return cached
        backend_id = self._index_to_backend_id.get(index)
        if backend_id is None:
            return None
        clarion_id = await self._stamp(backend_id)
        self._index_to_clarion_id[index] = clarion_id
        return clarion_id

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
