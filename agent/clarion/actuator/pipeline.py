"""Transport-agnostic §4 perception pipeline (pure over CDP dicts).

The smart part of the Actuator (execution §4.1) is **pure Python over the three
raw Chrome DevTools Protocol responses** — it never touches a browser handle:

  - ``DOM.getDocument``            (structure / backend node ids),
  - ``Accessibility.getFullAXTree`` (role / name / state),
  - ``DOMSnapshot.captureSnapshot`` (geometry + paint order).

That is why the same code serves two transports:

  - ``PlaywrightActuator`` — CDP via Playwright's ``CDPSession`` (spawned browser),
  - ``ExtensionActuator``  — CDP via the ``chrome.debugger`` relay (the user's
    real tab).

Both call ``parse_snapshot`` → ``PaintOrderRemover`` → ``build_candidates`` →
``containment_filter`` and then assign sequential indices into a ``SelectorMap``.
Only the *transport* (the triple-fetch and the per-node id stamp) differs; the
perception logic below is shared verbatim. No provider import lives here.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable, Optional

from clarion.contracts.state import (
    AxNode,
    Fact,
    PageDiff,
    PageReadout,
    PairedFact,
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

# ORIENT (the screen-reader readout) reads STRUCTURE the numbered map omits: the
# interactive map is action-only, but "what's on this page" also needs the page's
# headings. Heading roles carry a level in their AX properties.
_HEADING_ROLES = {"heading"}

# GROUND reads the page's readable TEXT CONTENT — where a task's actual VALUES live
# ("$84.32", "June 15, 2026", an account number). These are exactly the nodes the
# numbered interactive map and the ORIENT readout both DROP (they keep affordances
# + headings, not body text). ``extract_text_facts`` harvests them, each grounded
# to its real AX nodeId, as the page-grounded GROUND source — replacing any
# fixture constant (foundation §1: no fact without a source; absence stays absent).
_TEXT_CONTENT_ROLES = {"StaticText", "heading", "paragraph"}

# Roles whose LIVE VALUE (the AX node's ``value.value``) is itself a page value a
# task needs ("the amount field already holds $84.32", a selected combobox option).
# A static-text harvest drops these — the value lives on the control, not in a
# StaticText leaf — so GROUND must read it too (architecture PARSE bullet: "harvest
# control-values"). Each is still grounded to the control's real AX nodeId.
_CONTROL_VALUE_ROLES = {
    "textbox",
    "searchbox",
    "textarea",
    "combobox",
    "spinbutton",
    "slider",
    "checkbox",
    "radio",
    "switch",
    "option",
}

# A value-bearing string carries a currency/percent symbol or a digit — the kind of
# text a task READS (an amount, a date, an account/confirmation number). These are
# never deduped against each other (two different "$" amounts on a page MUST both
# survive — architecture PARSE bullet: "stop deduping value-bearing facts"); only
# pure-label text is deduped (the AX tree repeats a label across a node + its leaves).
_VALUE_BEARING_RE = re.compile(r"[\$£€%]|\d")

# Group the interactive roles into human-spoken affordance buckets for the ORIENT
# readback ("3 fields you can fill: …"). Each entry is (singular, plural, roles);
# the readback picks singular/plural by count so "1 field" reads right. Order = the
# order they're read aloud.
_AFFORDANCE_GROUPS: tuple[tuple[str, str, frozenset[str]], ...] = (
    (
        "field you can fill",
        "fields you can fill",
        frozenset({"textbox", "searchbox", "textarea", "combobox", "spinbutton", "slider"}),
    ),
    ("button", "buttons", frozenset({"button", "switch"})),
    ("link", "links", frozenset({"link", "menuitem", "tab"})),
    ("choice", "choices", frozenset({"checkbox", "radio", "option", "listbox"})),
)

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
# is exact even when the page has no id/name. Shared by both actuators — Playwright
# runs it via ``page.evaluate``, the extension via CDP ``Runtime.evaluate``.
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

# Target-node-only incremental re-perceive (migration step 1): read ONE already-
# stamped node's live geometry + accessible name in a single in-page pass, so a
# freshness re-check (e.g. the DeliveryGate's "is this index still here/where I
# think?" between a "yes" and the act) costs one round-trip instead of a full
# perceive. Returns null if the node is gone (detached/removed). bbox is
# [x,y,w,h] in CSS px, matching the SelectorMap's bbox convention. Shared by both
# transports (Playwright via page.evaluate, extension via Runtime.evaluate).
_NODE_STATE_JS = """
(clarionId) => {
  const el = document.querySelector('[data-clarion-id="' + clarionId + '"]');
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {
    bbox: [r.x, r.y, r.width, r.height],
    name: (el.getAttribute('aria-label')
           || el.textContent || el.value || '').trim(),
    disabled: !!el.disabled,
  };
}
"""


# A CDP transport: send one command + params, get its result dict. Both real
# Actuator transports expose this identical surface — Playwright's
# ``CDPSession.send`` and the ``CdpRelay.send`` — so the click below is shared
# verbatim (the kernel never sees it).
CdpSend = Callable[[str, dict], Awaitable[dict]]


async def cdp_click_by_backend(send: CdpSend, backend_id: int) -> tuple[bool, str]:
    """Click a node by its AX-tree ``backendDOMNodeId`` over any CDP transport.

    Identity-targeted and coordinate-free at OUR layer — we never compute a point
    from a stored bbox (the old bug: DOMSnapshot ``bounds`` are document-absolute,
    but ``Input.dispatchMouseEvent`` is viewport-relative, so an off-screen card
    link was clicked at empty space). Instead the BROWSER does the work, in the
    exact order Playwright's ``locator.click`` documents:

      1. ``DOM.scrollIntoViewIfNeeded`` — bring the node into the viewport.
      2. ``DOM.getContentQuads``        — its live quads, **relative to viewport**
         (CDP's words) — the same space ``Input.dispatchMouseEvent`` wants, so no
         scroll/DPR math is ever done here.
      3. ``Input.dispatchMouseEvent`` press+release at the quad centre — a real,
         trusted click on the actually-visible element.

    Fallback (a node with no content box — zero-area but activable, SVG, etc.):
    resolve it and call its own ``.click()`` after ``scrollIntoView`` — synthetic
    but correct. Returns ``(ok, detail)``.

    Shared verbatim by ``PlaywrightActuator`` (``self._cdp.send``) and
    ``ExtensionActuator`` (``self._relay.send``), so the autonomous Playwright
    proof exercises the SAME click path the extension product runs.
    """
    # (1) Scroll into view — best-effort; a node that can't scroll (detached) is
    # caught by the empty-quads / unresolvable check below, never silently passed.
    try:
        await send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend_id})
    except Exception:  # noqa: BLE001 - non-fatal; quads/resolve decide success
        pass

    # (2) Trusted click at the browser-reported viewport centre.
    quads: list = []
    try:
        res = await send("DOM.getContentQuads", {"backendNodeId": backend_id})
        quads = res.get("quads") or []
    except Exception:  # noqa: BLE001 - fall through to the .click() fallback
        quads = []
    if quads:
        q = quads[0]  # [x1,y1,x2,y2,x3,y3,x4,y4], clockwise (DOM.Quad)
        cx = (q[0] + q[2] + q[4] + q[6]) / 4.0
        cy = (q[1] + q[3] + q[5] + q[7]) / 4.0
        await send(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": cx, "y": cy,
             "button": "left", "buttons": 1, "clickCount": 1},
        )
        await send(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": cx, "y": cy,
             "button": "left", "buttons": 0, "clickCount": 1},
        )
        return True, ""

    # (3) Fallback: the element has no content quads → activate it directly.
    try:
        node = await send("DOM.resolveNode", {"backendNodeId": backend_id})
        object_id = (node.get("object") or {}).get("objectId")
        if not object_id:
            return False, f"node {backend_id} has no content box and is unresolvable"
        await send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": (
                    "function(){ this.scrollIntoView({block:'center',"
                    "inline:'center'}); this.click(); }"
                ),
            },
        )
        return True, "fallback:el.click()"
    except Exception as exc:  # noqa: BLE001 - report the real reason, never guess
        return False, f"click failed for node {backend_id}: {exc}"


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
        # Filled in by the parser: backend ids of this rect's descendants, so a
        # container never "occludes" its own child.
        self.is_ancestor_of: dict[int, bool] = {}


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


def parse_snapshot(
    snapshot: dict,
) -> tuple[dict[int, _LayoutRect], list[_LayoutRect]]:
    """Parse ``DOMSnapshot.captureSnapshot`` into per-backend-id geometry +
    paint order, and mark each rect's ancestry so a container never "occludes"
    its own descendant.

    The snapshot has ``documents[]``; each document has ``nodes`` (parallel
    arrays incl. ``backendNodeId`` and ``parentIndex``) and a ``layout`` object
    with ``nodeIndex`` (→ into nodes), ``bounds`` ([x,y,w,h] device px), and
    ``paintOrders``. Indices are document-local, so ancestry is resolved within
    each document.
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
        # parentIndex reconstructs ancestry (the "container doesn't occlude its
        # own child" rule); document-local.
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


def build_candidates(
    ax_tree: dict,
    layout_by_backend: dict[int, _LayoutRect],
    remover: PaintOrderRemover,
) -> list[dict[str, Any]]:
    """Simplify: keep interactive, non-ignored, on-screen, non-occluded AX nodes
    (execution §4.1 steps 2–3b). Returns candidate dicts ready for the
    containment filter + index assignment."""
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
        # PaintOrderRemover — drop nodes occluded by a higher overlay.
        if remover.is_occluded(rect):
            continue
        candidates.append(
            {
                "role": role,
                "name": name,
                "state": extract_state(ax),
                "node_id": str(ax.get("nodeId", "")),
                "backend_id": backend_id,
                "bbox": [rect.x, rect.y, rect.w, rect.h],
            }
        )
    return candidates


def containment_filter(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop a candidate whose bbox is ~99% inside another candidate's bbox (a
    child icon/text inside a button) — execution §4.1.4. Keep the outer
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


def order_reading(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort kept candidates into reading order (top-to-bottom, left-to-right) so
    the numbered "item 1, item 2…" matches the spoken readback. Mutates + returns
    the list (the ~8px row-quantize tolerates sub-pixel baseline jitter)."""
    candidates.sort(key=lambda c: (round(c["bbox"][1] / 8), c["bbox"][0]))
    return candidates


def extract_state(ax: dict) -> dict[str, bool]:
    """Pull boolean AX properties (focusable/disabled/checked/…) into a flat
    state dict for the AxNode."""
    state: dict[str, bool] = {}
    for prop in ax.get("properties", []) or []:
        pname = prop.get("name")
        pval = (prop.get("value") or {}).get("value")
        if isinstance(pval, bool):
            state[pname] = pval
    return state


def diff_maps(before: SelectorMap, after: SelectorMap) -> PageDiff:
    """Page-diff two SelectorMaps by stable node identity (role+name+node_id),
    so a silently-failed step shows up as added/removed/changed (execution §4.3).

    Pure over the maps and transport-agnostic — shared verbatim by
    ``PlaywrightActuator.diff`` and ``ExtensionActuator.diff``. Indices refer to
    the *after* map for added/changed nodes and the *before* map for removed."""

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


def estimate_tokens(nodes: dict[int, AxNode]) -> int:
    """Estimate the token cost of the LLM-facing serialization. Each node renders
    ~ ``[idx] role 'name'`` — count those chars / ~4 (§4.1 budget)."""
    chars = 0
    for n in nodes.values():
        chars += len(f"[{n.index}] {n.role} '{n.name}'\n")
    return int(chars / _CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# ORIENT — the grounded screen-reader readout (foundation §1, §3 on-ramp)
# ---------------------------------------------------------------------------


def _ax_name(ax: dict) -> str:
    """The accessible name a screen reader would announce, trimmed."""
    return ((ax.get("name") or {}).get("value", "") or "").strip()


def _group_affordances(
    by_role: dict[str, list[Fact]], *, max_per_group: int
) -> tuple[list[Fact], list[str]]:
    """Bucket grounded controls into human affordance groups and render one spoken
    phrase per non-empty group (count + a few names, singular/plural by count).
    Shared by both readout builders so the wording is identical everywhere."""
    affordances: list[Fact] = []
    phrases: list[str] = []
    for singular, plural, roles in _AFFORDANCE_GROUPS:
        items: list[Fact] = []
        for role in roles:
            items.extend(by_role.get(role, []))
        if not items:
            continue
        affordances.extend(items)
        label = singular if len(items) == 1 else plural
        names = [f.value for f in items[:max_per_group]]
        more = "" if len(items) <= max_per_group else f" (+{len(items) - max_per_group} more)"
        phrases.append(f"{len(items)} {label}: {', '.join(names)}{more}")
    return affordances, phrases


def _readout_summary(
    title: str, headings: list[Fact], group_phrases: list[str]
) -> str:
    """Compose the single spoken readback from grounded parts. Ends on an open
    prompt so the user states a goal (the goal is then confirmed, never assumed —
    foundation §1 agentic clause applied to goal-setting)."""
    parts: list[str] = []
    where = title.strip()
    if where:
        parts.append(f"This is {where}.")
    if headings:
        parts.append("The main sections are: " + "; ".join(h.value for h in headings) + ".")
    if group_phrases:
        parts.append("On this page I can see " + "; ".join(group_phrases) + ".")
    if not headings and not group_phrases:
        parts.append(
            "I can't find any labeled headings or controls on this page to read back."
        )
    parts.append("What would you like to do?")
    return " ".join(parts)


def summarize_ax_tree(
    ax_tree: dict,
    *,
    title: str = "",
    url: str = "",
    max_headings: int = 6,
    max_per_group: int = 8,
) -> PageReadout:
    """Turn a raw ``Accessibility.getFullAXTree`` response into a grounded
    ``PageReadout`` — the ORIENT readback (headings + grouped affordances).

    Every surfaced item carries the real AX ``nodeId`` as its ``source_node_id``
    (foundation §1: no fact without a source). Pure over the AX dict — no provider
    import, no geometry: we use the AX ``ignored`` flag to skip hidden nodes (this
    reads the whole page's STRUCTURE, unlike ``build_candidates`` which keeps only
    the interactive, on-screen subset for acting). Shared verbatim by both actuator
    transports (Playwright + extension)."""
    headings: list[Fact] = []
    by_role: dict[str, list[Fact]] = {}
    for ax in ax_tree.get("nodes", []) or []:
        if ax.get("ignored"):
            continue
        name = _ax_name(ax)
        if not name:
            continue
        node_id = str(ax.get("nodeId", ""))
        if not node_id:
            continue
        role = (ax.get("role") or {}).get("value", "") or ""
        fact = Fact(value=name, source_node_id=node_id, verified=True)
        if role in _HEADING_ROLES:
            headings.append(fact)
        elif role in _INTERACTIVE_ROLES:
            by_role.setdefault(role, []).append(fact)

    affordances, group_phrases = _group_affordances(by_role, max_per_group=max_per_group)
    summary = _readout_summary(title, headings[:max_headings], group_phrases)
    return PageReadout(
        title=title,
        url=url,
        headings=headings,
        affordances=affordances,
        summary=summary,
    )


def readout_from_selector_map(
    sm: SelectorMap, *, title: str = "", url: str = "", max_per_group: int = 8
) -> PageReadout:
    """Fallback ORIENT readout built from the interactive ``SelectorMap`` alone
    (actuators without ``describe_page`` — e.g. the cached/replay transport). Still
    fully grounded (each node carries its ``node_id``); just no page headings,
    since the numbered map is action-only."""
    by_role: dict[str, list[Fact]] = {}
    for node in sm.nodes.values():
        name = (node.name or "").strip()
        if not name or not node.node_id:
            continue
        by_role.setdefault(node.role, []).append(
            Fact(value=name, source_node_id=node.node_id, verified=True)
        )

    affordances, group_phrases = _group_affordances(by_role, max_per_group=max_per_group)
    summary = _readout_summary(title, [], group_phrases)
    return PageReadout(title=title, url=url, affordances=affordances, summary=summary)


# ---------------------------------------------------------------------------
# GROUND — the page-grounded fact source (foundation §1, kills the fixture)
# ---------------------------------------------------------------------------


def extract_text_facts(ax_tree: dict, *, max_facts: int = 60) -> list[Fact]:
    """Harvest the page's readable TEXT as grounded ``Fact``s — the page-grounded
    GROUND source that replaces the ``HeroRetriever`` fixture.

    Unlike ``summarize_ax_tree`` (headings + interactive affordances, for the
    ORIENT readback), this keeps the StaticText/heading CONTENT nodes — where the
    actual values a task needs live ("$84.32", "June 15, 2026", an account number).
    Those are precisely the nodes the numbered map and the ORIENT readout drop.

    Every ``Fact`` carries the real AX ``nodeId`` it was read from, so the
    epistemic clause (``policy.assert_grounded``) lets it be spoken. A page that
    does NOT contain a value simply yields no Fact for it — honest absence, so the
    kernel grounds nothing and declines rather than ever speaking a fixture
    constant the real page never showed (foundation §1).

    Also harvests CONTROL VALUES (an input's live ``value.value`` — the amount a
    field already holds) and the text of ``aria-live`` regions (a status/error a
    sighted user sees appear), each grounded to the control / region's real nodeId
    (architecture PARSE bullet). These are page values a static-text harvest drops.

    Pure over the raw ``Accessibility.getFullAXTree`` dict (no provider import, no
    geometry). Shared verbatim by both actuator transports (Playwright + extension).

    Dedup is by case-folded text — but **only for non-value-bearing (label) text**.
    The AX tree repeats a LABEL across a node and its ``InlineTextBox`` leaves, so a
    pure-label dedup is right; a VALUE-bearing string (anything with a digit / $ / %)
    is NEVER deduped, so two distinct ``$142.10`` rows or two identical ``25.00%``
    cells both survive (architecture PARSE bullet: "stop deduping value-bearing
    facts" — over-dedup is how the amount-due gets confused with the past-due).
    The synthetic ``InlineTextBox`` leaves carry NEGATIVE nodeIds and are filtered so
    every surfaced fact points at a real, resolvable node."""
    facts: list[Fact] = []
    seen: set[str] = set()
    for ax in ax_tree.get("nodes", []) or []:
        if ax.get("ignored"):
            continue
        role = (ax.get("role") or {}).get("value", "") or ""
        node_id = str(ax.get("nodeId", ""))
        # A real, positive AX nodeId only (InlineTextBox leaves carry synthetic
        # negative ids and merely duplicate their StaticText parent's text).
        if not node_id or node_id.startswith("-"):
            continue

        value: str = ""
        if role in _TEXT_CONTENT_ROLES:
            value = _ax_name(ax)
        elif role in _CONTROL_VALUE_ROLES:
            # The control's LIVE value (what it currently holds), not its label.
            value = ((ax.get("value") or {}).get("value") or "")
            if isinstance(value, (int, float)):
                value = str(value)
            value = value.strip() if isinstance(value, str) else ""
        elif _is_live_region(ax):
            # An aria-live region's announced text (a status / validation message).
            value = _ax_name(ax)
        if not value:
            continue

        value_bearing = bool(_VALUE_BEARING_RE.search(value))
        if not value_bearing:
            # Only label text is deduped (the AX tree repeats it across leaves).
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
        facts.append(Fact(value=value, source_node_id=node_id, verified=True))
        if len(facts) >= max_facts:
            break
    return facts


def _is_live_region(ax: dict) -> bool:
    """True if the node is an ``aria-live`` region (or an ``alert``/``status``
    role, which are implicitly live). Read from the AX ``properties`` — ``live``
    is ``"polite"``/``"assertive"`` for an explicit region."""
    role = (ax.get("role") or {}).get("value", "") or ""
    if role in ("alert", "status"):
        return True
    for prop in ax.get("properties", []) or []:
        if prop.get("name") == "live":
            live = (prop.get("value") or {}).get("value")
            if live in ("polite", "assertive"):
                return True
    return False


# ---------------------------------------------------------------------------
# PARSE — geometric label↔value pairing (architecture killer-closer #1)
# ---------------------------------------------------------------------------
#
# The worst epistemic failure is a CLEAN CITATION ON THE WRONG NUMBER (reading the
# past-due row's $142.10 as the amount due). A bare pair of grounded ``Fact``s does
# not protect against that — two true facts can be mis-associated. ``extract_paired_facts``
# makes the ASSOCIATION itself grounded: it only emits a ``PairedFact`` when a REAL
# STRUCTURAL/GEOMETRIC signal joins the label and the value, NEVER 8px reading-order.
#
# The four signals (``PairedFact.method``), strongest-first:
#   - ``aria-labelledby`` : the value node's ``name.sources`` names the label node
#                           via an ``aria-labelledby`` relatedElement (explicit).
#   - ``for``             : a native ``<label for>`` relatedElement (``nativeSource``).
#   - ``dom-ancestry``    : a table row/cell or a shared immediate parent joins a
#                           label cell/header to a value cell (the AX ``row``/``cell``/
#                           ``columnheader`` model + ``parentId``) — structural, not visual.
#   - ``shared-row``      : the label text and a value text vertically overlap (one
#                           visual row), the label is immediately LEFT of the value,
#                           and the value is the UNIQUE nearest right-neighbour within
#                           a small gap. Geometry — but disqualified the moment the
#                           pairing is AMBIGUOUS (≥2 plausible values on the row), which
#                           is exactly the reading-order mis-pairing this fence refuses.

# Max horizontal gap (CSS px) between a label's right edge and a value's left edge
# for a shared-row pairing — beyond this they are not "the same field" visually.
_SHARED_ROW_MAX_GAP = 220.0
# A second value within this px of the nearest one makes a shared-row pairing
# AMBIGUOUS → refused (we never guess which of two adjacent values the label means).
_SHARED_ROW_AMBIGUITY_MARGIN = 24.0


def _grounded_text(ax: dict) -> tuple[str, str]:
    """``(value, node_id)`` of a node's spoken text + its REAL nodeId, or ``("", "")``
    when the node is ignored, leaf-synthetic (negative id), or empty. A control's
    own ``value.value`` is included so a labelled input pairs to what it holds."""
    if ax.get("ignored"):
        return "", ""
    node_id = str(ax.get("nodeId", ""))
    if not node_id or node_id.startswith("-"):
        return "", ""
    role = (ax.get("role") or {}).get("value", "") or ""
    text = _ax_name(ax)
    if not text and role in _CONTROL_VALUE_ROLES:
        v = (ax.get("value") or {}).get("value") or ""
        text = str(v).strip()
    return text, node_id


def _node_text_via_children(ax: dict, by_id: dict[str, dict]) -> str:
    """A cell/container's text — its own name, else the first grounded descendant's
    (a ``cell`` often holds its text in a StaticText child)."""
    own = _ax_name(ax)
    if own:
        return own
    for cid in ax.get("childIds") or []:
        child = by_id.get(str(cid))
        if child is None:
            continue
        t = _ax_name(child)
        if t:
            return t
    return ""


def _resolve_related_label(
    source: dict, by_id: dict[str, dict], by_backend: dict[int, dict]
) -> Optional[dict]:
    """Resolve the AX node a ``name.sources`` relatedElement points at (the LABEL),
    via its ``backendDOMNodeId`` (CDP's relatedNodes key). Returns the label ax node
    or ``None`` when the relationship is empty/unresolvable."""
    for rel in source.get("relatedNodes") or []:
        backend = rel.get("backendDOMNodeId")
        node = by_backend.get(backend) if backend is not None else None
        if node is None:
            # Some CDP builds carry the text inline on the relatedNode itself.
            text = (rel.get("text") or "").strip()
            if text:
                return {"__synthetic_text__": text, "backendDOMNodeId": backend}
            continue
        return node
    return None


def _aria_pairings(
    nodes: list[dict], by_id: dict[str, dict], by_backend: dict[int, dict]
) -> list[PairedFact]:
    """``aria-labelledby`` / ``<label for>`` pairings: a control's name.sources names
    a label node by relationship. The control (value) pairs with the label, BOTH
    grounded to real nodeIds — the strongest, fully-explicit pairing signal.

    The VALUE half is the control's LIVE value (``value.value`` — what the field
    holds), not its accessible name (which, for a labelled control, is the label
    echoed back). The value is grounded to the control's own nodeId; a control with
    no live value yields no pairing (honest absence)."""
    pairs: list[PairedFact] = []
    for ax in nodes:
        node_id = str(ax.get("nodeId", ""))
        if not node_id or node_id.startswith("-") or ax.get("ignored"):
            continue
        # The control's own live value (the value half), not its label-echo name.
        v = (ax.get("value") or {}).get("value")
        value_text = str(v).strip() if v not in (None, "") else ""
        value_id = node_id
        if not value_text:
            continue
        name = ax.get("name") or {}
        for src in name.get("sources") or []:
            if src.get("superseded"):
                continue
            attr = src.get("attribute")
            native = src.get("nativeSource")
            if attr == "aria-labelledby":
                method = "aria-labelledby"
            elif native == "label":
                method = "for"
            else:
                continue
            label_node = _resolve_related_label(src, by_id, by_backend)
            if label_node is None:
                continue
            if "__synthetic_text__" in label_node:
                continue  # no real label nodeId to ground → skip (honest)
            label_text, label_id = _grounded_text(label_node)
            if not label_text or not label_id or label_id == value_id:
                continue
            pairs.append(
                PairedFact(
                    label=Fact(value=label_text, source_node_id=label_id, verified=True),
                    value=Fact(value=value_text, source_node_id=value_id, verified=True),
                    method=method,  # type: ignore[arg-type]
                )
            )
            break  # one pairing per control (the first non-superseded source)
    return pairs


def _table_pairings(
    nodes: list[dict], by_id: dict[str, dict]
) -> list[PairedFact]:
    """``dom-ancestry`` pairings from the AX TABLE model: pair every column header
    with each value cell in its column, and the row header (first cell) with each
    value cell in its row. Structural (the ``table``→``row``→``cell``/``columnheader``
    tree), never visual — a value cell pairs ONLY to the header at its real column
    index in the same row, so a wrong column never pairs.

    Robustness: a header cell is never itself a value half (no header↔header noise),
    and the column-header pairing only fires when the header row's cell count equals
    the data row's — so a multi-level / spanned header (where column indices don't
    line up) is NOT mis-aligned; the row-header pairing still grounds those rows."""
    pairs: list[PairedFact] = []
    header_roles = ("columnheader", "rowheader")
    for table in nodes:
        if (table.get("role") or {}).get("value") not in ("table", "grid", "treegrid"):
            continue
        # Collect rows in document order; the FIRST all-columnheader row is the
        # column-header row (a later one is a multi-level header → ignored for
        # column alignment, which the count-match guard below also enforces).
        rows: list[list[dict]] = []
        col_headers: list[dict] = []
        for rid in _descendant_rows(table, by_id):
            row = by_id.get(str(rid))
            if row is None:
                continue
            cells = [by_id[str(c)] for c in (row.get("childIds") or []) if str(c) in by_id]
            cells = [c for c in cells if (c.get("role") or {}).get("value")
                     in ("cell", "gridcell", "columnheader", "rowheader")]
            if not cells:
                continue
            header_row = all(
                (c.get("role") or {}).get("value") == "columnheader" for c in cells
            )
            if header_row and not col_headers:
                col_headers = cells
                continue
            rows.append(cells)

        for cells in rows:
            row_header = cells[0]
            rh_text = _node_text_via_children(row_header, by_id)
            rh_id = str(row_header.get("nodeId", ""))
            # Column pairing only when the header row aligns 1:1 with this row.
            aligned_headers = col_headers if len(col_headers) == len(cells) else []
            for ci, cell in enumerate(cells):
                # A header cell is structure, not a value → never the value half.
                if (cell.get("role") or {}).get("value") in header_roles and ci != 0:
                    continue
                cell_text = _node_text_via_children(cell, by_id)
                cell_id = str(cell.get("nodeId", ""))
                if not cell_text or not cell_id or cell_id.startswith("-") or ci == 0:
                    continue
                # value cell ↔ its column header (same column index, same row).
                if ci < len(aligned_headers):
                    header = aligned_headers[ci]
                    h_text = _node_text_via_children(header, by_id)
                    h_id = str(header.get("nodeId", ""))
                    if h_text and h_id and h_id != cell_id:
                        pairs.append(
                            PairedFact(
                                label=Fact(value=h_text, source_node_id=h_id, verified=True),
                                value=Fact(value=cell_text, source_node_id=cell_id, verified=True),
                                method="dom-ancestry",
                            )
                        )
                # value cell ↔ the row header (the row's first cell).
                if rh_text and rh_id and not rh_id.startswith("-") and rh_id != cell_id:
                    pairs.append(
                        PairedFact(
                            label=Fact(value=rh_text, source_node_id=rh_id, verified=True),
                            value=Fact(value=cell_text, source_node_id=cell_id, verified=True),
                            method="dom-ancestry",
                        )
                    )
    return pairs


def _descendant_rows(table: dict, by_id: dict[str, dict]) -> list[str]:
    """The ``row`` nodeIds under a table, in document order (rows may sit under a
    ``rowgroup``/``thead``/``tbody`` between the table and its rows)."""
    out: list[str] = []
    stack = list(table.get("childIds") or [])
    # Preserve document order with an explicit DFS (childIds are ordered).
    def walk(node_id: str) -> None:
        node = by_id.get(str(node_id))
        if node is None:
            return
        role = (node.get("role") or {}).get("value")
        if role == "row":
            out.append(str(node_id))
            return  # cells handled by the caller; don't recurse into a row
        for cid in node.get("childIds") or []:
            walk(cid)

    for cid in table.get("childIds") or []:
        walk(cid)
    return out


def _shared_row_pairings(
    nodes: list[dict], by_id: dict[str, dict], geometry: dict[int, list[float]]
) -> list[PairedFact]:
    """``shared-row`` pairings by GEOMETRY: a label text and a value text on one
    visual row, label immediately LEFT of the value, value the UNIQUE nearest
    right-neighbour within a gap. REFUSES the moment two values tie (ambiguous), so
    a reading-order coincidence never becomes a pairing. ``geometry`` maps a real AX
    nodeId → [x,y,w,h] (CSS px); when empty this signal yields nothing (pure path)."""
    if not geometry:
        return []
    # Grounded laid-out text nodes only.
    items: list[dict] = []
    for ax in nodes:
        if (ax.get("role") or {}).get("value") not in _TEXT_CONTENT_ROLES:
            continue
        text, node_id = _grounded_text(ax)
        if not text or not node_id:
            continue
        box = geometry.get(node_id)
        if not box or box[2] <= 0 or box[3] <= 0:
            continue
        items.append({"text": text, "id": node_id, "x": box[0], "y": box[1], "w": box[2], "h": box[3]})

    pairs: list[PairedFact] = []
    for label in items:
        if _VALUE_BEARING_RE.search(label["text"]):
            continue  # a value can't be a label
        label_right = label["x"] + label["w"]
        # Candidate values: value-bearing, same row, strictly to the right.
        cands: list[tuple[float, dict]] = []
        for val in items:
            if val is label or not _VALUE_BEARING_RE.search(val["text"]):
                continue
            overlap = min(label["y"] + label["h"], val["y"] + val["h"]) - max(label["y"], val["y"])
            if overlap < 0.5 * min(label["h"], val["h"]):
                continue
            gap = val["x"] - label_right
            if -4.0 <= gap <= _SHARED_ROW_MAX_GAP:
                cands.append((gap, val))
        if not cands:
            continue
        cands.sort(key=lambda c: c[0])
        nearest_gap, nearest = cands[0]
        # AMBIGUITY fence: a second value almost as close → refuse (don't guess).
        if len(cands) > 1 and (cands[1][0] - nearest_gap) < _SHARED_ROW_AMBIGUITY_MARGIN:
            continue
        pairs.append(
            PairedFact(
                label=Fact(value=label["text"], source_node_id=label["id"], verified=True),
                value=Fact(value=nearest["text"], source_node_id=nearest["id"], verified=True),
                method="shared-row",
            )
        )
    return pairs


def ax_node_geometry(
    ax_tree: dict, layout_by_backend: dict[int, "_LayoutRect"]
) -> dict[str, list[float]]:
    """Map a node's REAL AX ``nodeId`` → its [x,y,w,h] (CSS px), bridging the AX
    tree (nodeId) and the DOMSnapshot geometry (keyed by ``backendDOMNodeId``). Feeds
    ``extract_paired_facts(geometry=…)`` so the ``shared-row`` signal uses the SAME
    laid-out rects the numbered map does. Shared by both transports."""
    geom: dict[str, list[float]] = {}
    for n in ax_tree.get("nodes", []) or []:
        if n.get("ignored"):
            continue
        node_id = str(n.get("nodeId", ""))
        backend = n.get("backendDOMNodeId")
        if not node_id or backend is None:
            continue
        rect = layout_by_backend.get(int(backend))
        if rect is None or rect.w <= 0 or rect.h <= 0:
            continue
        geom[node_id] = [rect.x, rect.y, rect.w, rect.h]
    return geom


def extract_paired_facts(
    ax_tree: dict,
    *,
    geometry: Optional[dict[str, list[float]]] = None,
    max_pairs: int = 80,
) -> list[PairedFact]:
    """Harvest geometric label↔value ``PairedFact``s from a live AX tree — the
    killer-closer-#1 fence at EXTRACT time (architecture). A ``PairedFact`` is emitted
    ONLY when a real structural/geometric signal joins the two halves; both halves are
    grounded to real AX nodeIds, so ``PairedFact.backs(label, value)`` is true iff a
    single pairing grounds an "X is Y" claim byte-identically. A value that merely sits
    near the wrong label in reading order produces NO backing pairing → the claim is
    ungroundable and the kernel (Wave C) refuses it.

    ``geometry`` (real AX nodeId → [x,y,w,h] CSS px, from the DOMSnapshot) enables the
    ``shared-row`` signal; when omitted the function is fully pure over the AX dict and
    relies on the explicit (aria/for) + structural (table) signals only. Shared verbatim
    by both actuator transports (Playwright + extension).

    De-duplicated by ``PairedFact.id`` (label-id + value-id + method) so the same
    pairing surfaced by two signals appears once; distinct value cells never collapse."""
    nodes = ax_tree.get("nodes", []) or []
    by_id: dict[str, dict] = {}
    by_backend: dict[int, dict] = {}
    for n in nodes:
        nid = str(n.get("nodeId", ""))
        if nid:
            by_id[nid] = n
        backend = n.get("backendDOMNodeId")
        if backend is not None:
            by_backend[int(backend)] = n

    pairs: list[PairedFact] = []
    pairs.extend(_aria_pairings(nodes, by_id, by_backend))
    pairs.extend(_table_pairings(nodes, by_id))
    pairs.extend(_shared_row_pairings(nodes, by_id, geometry or {}))

    # De-dup by stable pairing id (label-id + value-id + method); keep insertion
    # order (strongest signal first). Distinct value cells never collapse.
    out: list[PairedFact] = []
    seen: set[str] = set()
    for p in pairs:
        if p.id in seen:
            continue
        seen.add(p.id)
        out.append(p)
        if len(out) >= max_pairs:
            break
    return out
