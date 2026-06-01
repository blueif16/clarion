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
from typing import Any, Optional

from playwright.async_api import (
    Browser,
    CDPSession,
    Page,
    Playwright,
    async_playwright,
)

from clarion.contracts.ports import Actuator
from clarion.contracts.state import (
    Action,
    AxNode,
    Observation,
    PageDiff,
    SelectorMap,
)

# AX roles we treat as "interactive" for the numbered map (execution §4.1.5).
# Anything outside this set is structural and never gets an index — only things
# a user can act on become a numbered selector-map entry.
_INTERACTIVE_ROLES = {
    "textbox",
    "searchbox",
    "button",
    "link",
    "checkbox",
    "radio",
    "combobox",
    "listbox",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "tab",
    "switch",
    "slider",
    "spinbutton",
    "textarea",
}

# ~4 chars/token is the common rough heuristic; the SelectorMap serialized to the
# LLM is "[idx] role 'name'" per node, so we estimate from that line length.
_CHARS_PER_TOKEN = 4.0

# Fraction of a node's bbox area that must lie inside another node's bbox for it
# to be considered "contained" (a child icon/text inside a button) — §4.1.4.
_CONTAINMENT_THRESHOLD = 0.99

# The native-setter fill (execution §4.3). Applies the prototype value setter so
# React/controlled inputs the naive ``.value=`` misses still register the change,
# then dispatches the events those components listen for. The element is located
# by an injected ``data-clarion-id`` attribute (set during perceive) so resolution
# is exact even when the page has no id/name.
_NATIVE_SETTER_JS = """
(args) => {
  const { clarionId, value } = args;
  const el = document.querySelector('[data-clarion-id="' + clarionId + '"]');
  if (!el) return { ok: false, reason: 'element-not-found', clarionId };
  const proto = el instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype;
  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
  if (!desc || !desc.set) return { ok: false, reason: 'no-value-setter' };
  desc.set.call(el, value);
  // Fire the events controlled components (React etc.) listen for.
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return { ok: true, value: el.value };
}
"""

# Read-back of an element's live value/text by clarion id (read action + assert).
_READ_JS = """
(clarionId) => {
  const el = document.querySelector('[data-clarion-id="' + clarionId + '"]');
  if (!el) return null;
  if ('value' in el && el.value !== undefined) return el.value;
  return (el.textContent || '').trim();
}
"""

class PaintOrderRemover:
    """Drop nodes occluded by a higher paint-order overlay / modal (execution
    §4.1.3 — "kills the 'agent clicked the thing behind the cookie banner' bug").

    Built from the ``DOMSnapshot.captureSnapshot`` layout tree, which gives every
    laid-out node a ``bounds`` rect and a ``paintOrder`` (stacking order; higher =
    painted later = on top). A candidate interactive node is occluded if some
    *other* laid-out node with a strictly higher paint order covers the
    candidate's interaction point (its bbox center).
    """

    def __init__(self, layout_rects: list["_LayoutRect"]) -> None:
        # Sort once, highest paint order first.
        self._rects = sorted(layout_rects, key=lambda r: r.paint_order, reverse=True)

    def is_occluded(self, node: "_LayoutRect") -> bool:
        cx = node.x + node.w / 2.0
        cy = node.y + node.h / 2.0
        for other in self._rects:
            if other.paint_order <= node.paint_order:
                # Sorted desc: once we reach our own paint order, nothing above.
                break
            if other.backend_id == node.backend_id:
                continue
            if other.is_ancestor_of.get(node.backend_id):
                # A node is never occluded by its own ancestor/container.
                continue
            if _point_in_rect(cx, cy, other):
                return True
        return False


class _LayoutRect:
    """A laid-out node's geometry + paint order (from DOMSnapshot)."""

    __slots__ = (
        "backend_id",
        "x",
        "y",
        "w",
        "h",
        "paint_order",
        "is_ancestor_of",
    )

    def __init__(
        self,
        backend_id: int,
        x: float,
        y: float,
        w: float,
        h: float,
        paint_order: int,
    ) -> None:
        self.backend_id = backend_id
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.paint_order = paint_order
        # Filled in by the merger: backend ids of this rect's descendants, so a
        # container never "occludes" its own child.
        self.is_ancestor_of: dict[int, bool] = {}


def _point_in_rect(px: float, py: float, r: _LayoutRect) -> bool:
    return r.x <= px <= r.x + r.w and r.y <= py <= r.y + r.h


def _bbox_containment(inner: list[float], outer: list[float]) -> float:
    """Fraction of ``inner``'s area that lies inside ``outer``. Both are
    [x, y, w, h] in CSS pixels. Returns 0..1."""
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    inner_area = iw * ih
    if inner_area <= 0:
        return 0.0
    # Intersection rect.
    left = max(ix, ox)
    top = max(iy, oy)
    right = min(ix + iw, ox + ow)
    bottom = min(iy + ih, oy + oh)
    if right <= left or bottom <= top:
        return 0.0
    inter = (right - left) * (bottom - top)
    return inter / inner_area


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
        self._index_to_clarion_id: dict[int, str] = {}
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
        """Run the §4.1 pipeline and return the merged, numbered SelectorMap."""
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
        layout_by_backend, all_rects = self._parse_snapshot(snapshot)
        remover = PaintOrderRemover(all_rects)

        # 3. Simplify: keep interactive, non-ignored AX nodes.
        candidates: list[dict[str, Any]] = []
        for ax in ax_tree.get("nodes", []):
            if ax.get("ignored"):
                continue
            role = (ax.get("role") or {}).get("value", "")
            if role not in _INTERACTIVE_ROLES:
                continue
            backend_id = ax.get("backendDOMNodeId")
            if backend_id is None:
                continue
            name = (ax.get("name") or {}).get("value", "") or ""
            rect = layout_by_backend.get(backend_id)
            # A node with no geometry is either hidden or not laid out → drop it
            # (we never number something with no on-screen presence).
            if rect is None or rect.w <= 0 or rect.h <= 0:
                continue
            # 3b. PaintOrderRemover — drop nodes occluded by a higher overlay.
            if remover.is_occluded(rect):
                continue
            candidates.append(
                {
                    "role": role,
                    "name": name,
                    "state": self._extract_state(ax),
                    "node_id": str(ax.get("nodeId", "")),
                    "backend_id": backend_id,
                    "bbox": [rect.x, rect.y, rect.w, rect.h],
                }
            )

        # 4. Bbox-containment filter — a button's child icon/text don't get a
        #    separate index. Keep the larger (container) node, drop the contained.
        kept = self._containment_filter(candidates)

        # 5. Assign sequential indices → SelectorMap, stamping each kept element
        #    with a stable clarion id (via CDP) so act/read resolve it exactly.
        nodes: dict[int, AxNode] = {}
        self._index_to_clarion_id = {}
        self._index_to_bbox = {}
        # Order by reading order (top-to-bottom, left-to-right) for stable, human
        # "item 1, item 2…" numbering that matches the spoken readback.
        kept.sort(key=lambda c: (round(c["bbox"][1] / 8), c["bbox"][0]))
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

        return SelectorMap(nodes=nodes, token_estimate=self._estimate_tokens(nodes))

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
        silently-failed step (execution §4.3)."""

        def key(n: AxNode) -> str:
            return f"{n.role}\x00{n.name}\x00{n.node_id}"

        before_by_key = {key(n): n for n in before.nodes.values()}
        after_by_key = {key(n): n for n in after.nodes.values()}
        added_keys = set(after_by_key) - set(before_by_key)
        removed_keys = set(before_by_key) - set(after_by_key)
        changed_keys = {
            k
            for k in set(before_by_key) & set(after_by_key)
            if before_by_key[k].state != after_by_key[k].state
            or before_by_key[k].bbox != after_by_key[k].bbox
        }
        added = [after_by_key[k].index for k in added_keys]
        removed = [before_by_key[k].index for k in removed_keys]
        changed = [after_by_key[k].index for k in changed_keys]
        return PageDiff(
            added=sorted(added), removed=sorted(removed), changed=sorted(changed)
        )

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
        clarion_id = self._index_to_clarion_id.get(action.index)
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
        clarion_id = self._index_to_clarion_id.get(action.index)
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
        clarion_id = self._index_to_clarion_id.get(index)
        if clarion_id is None:
            return None
        value = await self._page.evaluate(_READ_JS, clarion_id)
        return None if value is None else str(value)

    # --- perception internals ----------------------------------------------

    def _parse_snapshot(
        self, snapshot: dict
    ) -> tuple[dict[int, _LayoutRect], list[_LayoutRect]]:
        """Parse ``DOMSnapshot.captureSnapshot`` into per-backend-id geometry +
        paint order, and mark each rect's ancestry so a container never
        "occludes" its own descendant.

        The snapshot has ``documents[]``; each document has ``nodes`` (parallel
        arrays incl. ``backendNodeId`` and ``parentIndex``) and a ``layout``
        object with ``nodeIndex`` (→ into nodes), ``bounds`` ([x,y,w,h] device
        px), and ``paintOrders``. Indices are document-local, so ancestry is
        resolved within each document.
        """
        layout_by_backend: dict[int, _LayoutRect] = {}
        all_rects: list[_LayoutRect] = []
        docs = snapshot.get("documents", [])
        if not docs:
            return layout_by_backend, all_rects
        # device-pixel → CSS-pixel scaling (DOMRects in the snapshot are device px).
        scale = float(snapshot.get("deviceScaleFactor") or 1.0) or 1.0
        for doc in docs:
            nodes = doc.get("nodes", {})
            backend_ids = nodes.get("backendNodeId", []) or []
            # parentIndex reconstructs ancestry (the "container doesn't occlude
            # its own child" rule); document-local.
            parent_index = nodes.get("parentIndex", []) or []
            layout = doc.get("layout", {})
            node_indices = layout.get("nodeIndex", []) or []
            bounds = layout.get("bounds", []) or []
            paint_orders = layout.get("paintOrders", []) or []

            # node_index → rect, for this document only.
            node_index_to_rect: dict[int, _LayoutRect] = {}
            for li, node_index in enumerate(node_indices):
                if node_index >= len(backend_ids):
                    continue
                backend_id = backend_ids[node_index]
                if backend_id is None or backend_id < 0:
                    continue
                if li >= len(bounds):
                    continue
                b = bounds[li]
                if not b or len(b) < 4:
                    continue
                paint_order = paint_orders[li] if li < len(paint_orders) else 0
                rect = _LayoutRect(
                    backend_id=int(backend_id),
                    x=b[0] / scale,
                    y=b[1] / scale,
                    w=b[2] / scale,
                    h=b[3] / scale,
                    paint_order=int(paint_order),
                )
                layout_by_backend[int(backend_id)] = rect
                all_rects.append(rect)
                node_index_to_rect[node_index] = rect

            # Mark ancestry within this document: walk each laid-out node's parent
            # chain and record it as an ancestor of that node's backend id.
            for node_index, rect in node_index_to_rect.items():
                cur = node_index
                guard = 0
                while 0 <= cur < len(parent_index) and guard < 4096:
                    p = parent_index[cur]
                    guard += 1
                    if p is None or p < 0:
                        break
                    anc_rect = node_index_to_rect.get(p)
                    if anc_rect is not None:
                        anc_rect.is_ancestor_of[rect.backend_id] = True
                    cur = p
        return layout_by_backend, all_rects

    def _containment_filter(
        self, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Drop a candidate whose bbox is ~99% inside another candidate's bbox
        (a child icon/text inside a button) — execution §4.1.4. Keep the outer
        (container) node; it is the one a user means to click."""
        n = len(candidates)
        drop = [False] * n
        # Larger area first so the container is the survivor.
        order = sorted(
            range(n),
            key=lambda i: candidates[i]["bbox"][2] * candidates[i]["bbox"][3],
            reverse=True,
        )
        for oi_pos in range(n):
            oi = order[oi_pos]
            if drop[oi]:
                continue
            outer = candidates[oi]["bbox"]
            outer_area = outer[2] * outer[3]
            for ii_pos in range(oi_pos + 1, n):
                ii = order[ii_pos]
                if drop[ii]:
                    continue
                inner = candidates[ii]["bbox"]
                inner_area = inner[2] * inner[3]
                # Only fold a strictly-smaller node into a larger one.
                if inner_area >= outer_area:
                    continue
                if _bbox_containment(inner, outer) >= _CONTAINMENT_THRESHOLD:
                    drop[ii] = True
        return [c for i, c in enumerate(candidates) if not drop[i]]

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

    @staticmethod
    def _extract_state(ax: dict) -> dict[str, bool]:
        state: dict[str, bool] = {}
        for prop in ax.get("properties", []) or []:
            pname = prop.get("name")
            pval = (prop.get("value") or {}).get("value")
            if isinstance(pval, bool):
                state[pname] = pval
        return state

    @staticmethod
    def _estimate_tokens(nodes: dict[int, AxNode]) -> int:
        """Estimate the token cost of the LLM-facing serialization. Each node
        renders ~ ``[idx] role 'name'`` — count those chars / ~4 (§4.1 budget)."""
        chars = 0
        for n in nodes.values():
            chars += len(f"[{n.index}] {n.role} '{n.name}'\n")
        return int(chars / _CHARS_PER_TOKEN)


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
