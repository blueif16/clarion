"""A1 — the full a11y-tree Actuator (Playwright + CDP).

The Actuator port's real implementation (execution §4). Supersedes the spike
slice in ``agent/spike/actuator_min.py``: this one runs the **parallel CDP
triple-fetch**, merges the three domains by ``backendDOMNodeId``, runs the
``PaintOrderRemover`` (drop nodes occluded by higher paint-order overlays) and a
~99% bbox-containment filter (a button's child icon/text don't get separate
indices), then assigns sequential interactive indices into a ``SelectorMap``.

Pipeline (execution §4.1):
  1. **Parallel CDP triple-fetch** (``asyncio.gather``):
       - ``DOM.getDocument`` (structure / backend node ids),
       - ``Accessibility.getFullAXTree`` (role / name / state — what the screen
         reader sees),
       - ``DOMSnapshot.captureSnapshot`` (geometry + paint order).
  2. **Simplify** → keep only non-ignored, interactive AX nodes.
  3. **``PaintOrderRemover``** → drop nodes whose interaction point is covered by
     a higher paint-order node (overlay / modal).
  4. **Bbox-containment filter (~99%)** → drop an interactive node that is almost
     entirely inside another interactive node (child icon / text of a button).
  5. **Assign sequential indices → ``SelectorMap``** (with ``token_estimate``).

Acting (execution §4.3):
  - ``fill`` → resolve ``selector_map[index]`` → real input → **native-setter**
    (``Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set`` +
    dispatch ``input``/``change``), so React-controlled inputs register the change.
  - ``click`` → resolve the node's bbox center and ``page.mouse.click`` it (a
    real, paint-order-honest click — not a CSS-selector click that can hit an
    occluded element).
  - ``navigate`` → ``page.goto(value)``.
  - ``read`` → read the node's live ``.value`` / text back from the DOM.
  - After every act we re-perceive (CONFIRM reads the *new* tree).

``diff`` → a ``PageDiff`` (added / removed / changed by stable node identity).

``perceive_vision`` is the **named, honest fallback** for AX-blind widgets — it
is deferred (execution §4.2 / §17) and raises ``NotImplementedError``.

Provider import (Playwright) is allowed here — this is a Wave-1 adapter. The
kernel never imports it; it sees only the ``Actuator`` ABC.

Context7 facts (resolved 2026-05-31, ``/websites/playwright_dev_python``,
Playwright Python 1.60.0):
  - ``client = await page.context.new_cdp_session(page)`` returns a CDPSession;
    ``await client.send("Domain.command", {params})`` is the CDP call pattern.
  - Async launch: ``await async_playwright().start()`` →
    ``await playwright.chromium.launch(headless=...)``.
  - ``await page.evaluate(js, arg)`` runs JS in page context (the native setter).
  - ``box = await element_handle.bounding_box()`` /
    ``await page.mouse.click(x, y)`` for coordinate clicks.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from playwright.async_api import (
    Browser,
    CDPSession,
    Page,
    Playwright,
    async_playwright,
)

# The §4 perception pipeline is pure over the CDP dicts — shared with
# ExtensionActuator (CDP via chrome.debugger). The names below are re-exported
# from this module for back-compat with the A1 acceptance tests.
from clarion.actuator.pipeline import (  # noqa: F401  (re-exported)
    PaintOrderRemover,
    _bbox_containment,
    _LayoutRect,
    _NATIVE_SETTER_JS,
    _NODE_STATE_JS,
    _READ_JS,
    build_candidates,
    containment_filter,
    diff_maps,
    estimate_tokens,
    extract_text_facts,
    order_reading,
    parse_snapshot,
    summarize_ax_tree,
)
from clarion.contracts.ports import Actuator
from clarion.contracts.state import (
    Action,
    AxNode,
    Fact,
    Observation,
    PageDiff,
    PageReadout,
    SelectorMap,
)

class PlaywrightActuator(Actuator):
    """The real a11y-tree Actuator over a single page (execution §4).

    Lifecycle: ``await PlaywrightActuator.create(url)`` launches headless
    chromium, navigates the configurable target URL, and attaches a CDP session
    with the DOM / Accessibility / DOMSnapshot domains enabled. Call
    ``await actuator.close()`` when done.
    """

    def __init__(
        self,
        playwright: Playwright,
        browser: Browser,
        page: Page,
        cdp: CDPSession,
    ) -> None:
        self._pw = playwright
        self._browser = browser
        self._page = page
        self._cdp = cdp
        # index -> the clarion id we stamp on the real element for exact resolve.
        # Filled LAZILY (see perceive): empty after perceive; an entry appears only
        # once an index is actually acted/read (``_ensure_stamped``).
        self._index_to_clarion_id: dict[int, str] = {}
        # index -> backend node id (in hand from perceive; the input to lazy stamp).
        self._index_to_backend_id: dict[int, int] = {}
        # index -> bbox [x,y,w,h] for coordinate clicks.
        self._index_to_bbox: dict[int, list[float]] = {}
        self._clarion_counter = 0

    # --- lifecycle ----------------------------------------------------------

    @classmethod
    async def create(
        cls, url: str, *, headless: bool = True
    ) -> "PlaywrightActuator":
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=headless)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await page.goto(url, wait_until="load")
        cdp = await page.context.new_cdp_session(page)
        # Enable the three domains the triple-fetch reads.
        await asyncio.gather(
            cdp.send("DOM.enable"),
            cdp.send("Accessibility.enable"),
            cdp.send("DOMSnapshot.enable"),
        )
        return cls(pw, browser, page, cdp)

    async def close(self) -> None:
        try:
            await self._browser.close()
        finally:
            await self._pw.stop()

    # --- Actuator port ------------------------------------------------------

    async def perceive(self) -> SelectorMap:
        """Run the §4.1 pipeline and return the merged, numbered SelectorMap.

        **Lazy stamping (migration step 1):** perceive stamps ZERO nodes — it
        records ``index -> backend_id`` only and writes the ``data-clarion-id`` on
        the first act/read/read_value on an index (``_ensure_stamped``). Kept in
        lockstep with ``ExtensionActuator`` so the transport-parity guarantee holds:
        only WHEN the id is stamped moved (perceive → first resolve); the produced
        ``(index, role, name, bbox)`` are unchanged."""
        # 1. Parallel CDP triple-fetch (execution §4.1.1). DOM.getDocument forces
        #    the backend to materialize the full (pierced) DOM so the AXTree's and
        #    snapshot's backendDOMNodeIds line up; the merge itself keys off the
        #    snapshot geometry + AXTree role/name (both carry backendNodeId).
        _dom_doc, ax_tree, snapshot = await asyncio.gather(
            self._cdp.send("DOM.getDocument", {"depth": -1, "pierce": True}),
            self._cdp.send("Accessibility.getFullAXTree"),
            self._cdp.send(
                "DOMSnapshot.captureSnapshot",
                {
                    "computedStyles": [],
                    "includePaintOrder": True,
                    "includeDOMRects": True,
                },
            ),
        )

        # 2. Geometry + paint order from the snapshot, keyed by backendNodeId
        #    (ancestry marked inside the parser so a container never occludes its
        #    own descendant).
        layout_by_backend, all_rects = parse_snapshot(snapshot)
        remover = PaintOrderRemover(all_rects)

        # 3. Simplify: keep interactive, non-ignored, on-screen, non-occluded AX
        #    nodes (shared §4 pipeline — identical under the extension transport).
        candidates = build_candidates(ax_tree, layout_by_backend, remover)

        # 4. Bbox-containment filter — a button's child icon/text don't get a
        #    separate index. Keep the larger (container) node, drop the contained.
        kept = containment_filter(candidates)

        # 5. Assign sequential indices → SelectorMap. LAZY: record index ->
        #    backend_id and leave the clarion-id unstamped; the first act/read on an
        #    index stamps only that one node (no per-node CDP round-trip here).
        nodes: dict[int, AxNode] = {}
        self._index_to_clarion_id = {}
        self._index_to_backend_id = {}
        self._index_to_bbox = {}
        # Order by reading order (top-to-bottom, left-to-right) for stable, human
        # "item 1, item 2…" numbering that matches the spoken readback.
        order_reading(kept)
        for index, c in enumerate(kept):
            nodes[index] = AxNode(
                index=index,
                role=c["role"],
                name=c["name"],
                state=c["state"],
                bbox=c["bbox"],
                node_id=c["node_id"],
            )
            self._index_to_backend_id[index] = c["backend_id"]
            self._index_to_bbox[index] = c["bbox"]

        return SelectorMap(nodes=nodes, token_estimate=estimate_tokens(nodes))

    async def describe_page(self) -> PageReadout:
        """ORIENT: a grounded readout of the WHOLE page (headings + affordances),
        not just the interactive map ``perceive`` returns. Fetches the full AXTree
        and runs the shared pure summarizer; every item is sourced to a real AX
        ``nodeId`` (foundation §1). Not a ``perceive`` replacement — it's the
        screen-reader read-back the voice plane speaks before a goal is set."""
        ax_tree = await self._cdp.send("Accessibility.getFullAXTree")
        try:
            title = await self._page.title()
        except Exception:  # noqa: BLE001 - title is best-effort
            title = ""
        return summarize_ax_tree(ax_tree, title=title, url=self._page.url or "")

    async def read_facts(self) -> list[Fact]:
        """GROUND source: harvest the page's readable text as grounded ``Fact``s
        (the page-grounded replacement for the ``HeroRetriever`` fixture). Fetches
        the full AXTree and runs the shared pure ``extract_text_facts``; every fact
        is sourced to a real AX ``nodeId`` (foundation §1). Not part of the
        ``Actuator`` port — an extra read, like ``describe_page`` / ``read_value``;
        the ``PageRetriever`` calls it to feed the kernel's GROUND."""
        ax_tree = await self._cdp.send("Accessibility.getFullAXTree")
        return extract_text_facts(ax_tree)

    async def act(self, action: Action) -> Observation:
        """Execute the action against the live page, then re-perceive (§4.3)."""
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
        """Page-diff by stable node identity (role+name+node_id), detecting a
        silently-failed step (execution §4.3). The pure logic lives in
        ``pipeline.diff_maps`` — shared verbatim with ``ExtensionActuator``."""
        return diff_maps(before, after)

    async def perceive_vision(self) -> SelectorMap:
        """Vision fallback for AX-blind widgets (canvas / unlabeled custom
        controls) — execution §4.2. Named honestly; deferred (§17)."""
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
        res = await self._page.evaluate(
            _NATIVE_SETTER_JS, {"clarionId": clarion_id, "value": action.value}
        )
        ok = bool(res.get("ok"))
        after = await self.perceive()
        return Observation(
            selector_map=after,
            success=ok,
            detail="" if ok else f"native-setter failed: {res.get('reason')}",
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
        # Click the bbox center — a paint-order-honest, real user click. (The node
        # is in the map only because PaintOrderRemover already proved its center
        # is the topmost paint at that point.)
        cx = bbox[0] + bbox[2] / 2.0
        cy = bbox[1] + bbox[3] / 2.0
        await self._page.mouse.click(cx, cy)
        # Let any resulting navigation / DOM mutation settle.
        try:
            await self._page.wait_for_load_state("networkidle", timeout=1000)
        except Exception:
            pass
        return Observation(selector_map=await self.perceive(), success=True)

    async def _do_navigate(self, action: Action) -> Observation:
        if not action.value:
            return Observation(
                selector_map=await self.perceive(),
                success=False,
                detail="navigate requires value (url)",
            )
        await self._page.goto(action.value, wait_until="load")
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
        value = await self._page.evaluate(_READ_JS, clarion_id)
        return Observation(
            selector_map=await self.perceive(),
            success=value is not None,
            detail="" if value is None else str(value),
        )

    async def read_value(self, index: int) -> Optional[str]:
        """Live ``.value`` read-back for an input by index (test/assert helper).

        Not part of the ``Actuator`` port — the honest way to prove a field was
        actually filled (vs merely that we *called* fill). Mirrors the spike."""
        clarion_id = await self._ensure_stamped(index)
        if clarion_id is None:
            return None
        value = await self._page.evaluate(_READ_JS, clarion_id)
        return None if value is None else str(value)

    # --- perception internals (transport-specific) -------------------------
    # The pure §4 pipeline (parse_snapshot / build_candidates / containment_filter
    # / order_reading / estimate_tokens) lives in clarion.actuator.pipeline and is
    # shared verbatim with ExtensionActuator. Only the CDP transport + the id
    # stamp differ per actuator.

    def _containment_filter(self, candidates: list[dict]) -> list[dict]:
        """Back-compat shim for the A1 acceptance test, which calls this method
        on the instance. The logic lives in ``pipeline.containment_filter``."""
        return containment_filter(candidates)

    async def reperceive_node(self, index: int) -> Optional[AxNode]:
        """Target-node-only incremental re-perceive (migration step 1).

        Re-read ONE node's live geometry + name in a single ``page.evaluate``
        (after lazy-stamping it once) without rebuilding the whole map — the cheap
        freshness re-check the DeliveryGate will use between a "yes" and the act.
        Returns a fresh ``AxNode`` for the index, or ``None`` if the node is gone or
        the index isn't mapped. Shared ``_NODE_STATE_JS`` with the extension
        transport. The role is carried over from the last full perceive (it doesn't
        change for a stable element); bbox/name/disabled come live from the page."""
        clarion_id = await self._ensure_stamped(index)
        if clarion_id is None:
            return None
        state = await self._page.evaluate(_NODE_STATE_JS, clarion_id)
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

    async def _ensure_stamped(self, index: int) -> Optional[str]:
        """Lazy-resolve an index to its ``data-clarion-id``, stamping the single
        node on first use (migration step 1). Returns the clarion id (stamping it
        via ``_stamp`` if not yet stamped) or ``None`` if the index isn't in the
        current map. This is the ~2-CDP-round-trip cost the eager perceive loop used
        to pay for EVERY node — now paid once, only for the node acted on."""
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
        resolve it exactly (works even with no id/name). Resolves the
        backendNodeId → a frontend nodeId via ``DOM.pushNodesByBackendIdsToFrontend``
        then sets the attribute with ``DOM.setAttributeValue``."""
        self._clarion_counter += 1
        clarion_id = f"cl-{self._clarion_counter}"
        try:
            pushed = await self._cdp.send(
                "DOM.pushNodesByBackendIdsToFrontend",
                {"backendNodeIds": [backend_id]},
            )
            node_ids = pushed.get("nodeIds", [])
            if node_ids:
                await self._cdp.send(
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


async def _selfcheck(url: str) -> None:
    """Standalone actuator self-check: perceive → list nodes → fill first input."""
    act = await PlaywrightActuator.create(url, headless=True)
    try:
        sm = await act.perceive()
        print(
            f"[actuator] perceived {len(sm.nodes)} interactive nodes, "
            f"~{sm.token_estimate} tokens"
        )
        for idx, node in sm.nodes.items():
            print(
                f"  [{idx}] role={node.role!r} name={node.name!r} bbox={node.bbox}"
            )
    finally:
        await act.close()


if __name__ == "__main__":
    import os
    import sys

    target = os.environ.get(
        "A1_TARGET_URL",
        (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765"),
    )
    asyncio.run(_selfcheck(target))
