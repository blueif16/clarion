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

from typing import Any

from clarion.contracts.state import AxNode, Fact, PageDiff, PageReadout, SelectorMap

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

# Group the interactive roles into human-spoken affordance buckets for the ORIENT
# readback ("3 fields you can fill: …"). Order = the order they're read aloud.
_AFFORDANCE_GROUPS: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "fields you can fill",
        frozenset({"textbox", "searchbox", "textarea", "combobox", "spinbutton", "slider"}),
    ),
    ("buttons", frozenset({"button", "switch"})),
    ("links", frozenset({"link", "menuitem", "tab"})),
    ("choices", frozenset({"checkbox", "radio", "option", "listbox"})),
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

    affordances: list[Fact] = []
    group_phrases: list[str] = []
    for label, roles in _AFFORDANCE_GROUPS:
        items: list[Fact] = []
        for role in roles:
            items.extend(by_role.get(role, []))
        if not items:
            continue
        affordances.extend(items)
        names = [f.value for f in items[:max_per_group]]
        more = "" if len(items) <= max_per_group else f" (+{len(items) - max_per_group} more)"
        group_phrases.append(f"{len(items)} {label}: {', '.join(names)}{more}")

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

    affordances: list[Fact] = []
    group_phrases: list[str] = []
    for label, roles in _AFFORDANCE_GROUPS:
        items: list[Fact] = []
        for role in roles:
            items.extend(by_role.get(role, []))
        if not items:
            continue
        affordances.extend(items)
        names = [f.value for f in items[:max_per_group]]
        more = "" if len(items) <= max_per_group else f" (+{len(items) - max_per_group} more)"
        group_phrases.append(f"{len(items)} {label}: {', '.join(names)}{more}")

    summary = _readout_summary(title, [], group_phrases)
    return PageReadout(title=title, url=url, affordances=affordances, summary=summary)
