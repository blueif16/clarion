"""S1 — minimal Actuator implementation (Playwright + CDP).

The seam's bottom edge (execution §4). This is the *spike* slice of the full A1
actuator: just enough perception + actuation to round-trip ONE field on the C2
target page, built strictly against the frozen `clarion.contracts` types.

What it does (execution §4.1 / §4.3, spike scope):
  - `perceive()` → drives a CDP session, calls `Accessibility.getFullAXTree`,
    and builds a minimal numbered `SelectorMap` of the interactive nodes
    (textbox / button) on the C2 page. The full A1 triple-fetch + PaintOrderRemover
    + bbox-containment filter is out of scope for the spike — here we keep only the
    interactive, named nodes, which is all the one-field round-trip needs.
  - `act(fill)` → resolves `selector_map[index]` back to the real DOM node and
    fills it with the **native-setter** technique
    (`Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set`
    + dispatch 'input'), so React-controlled inputs the naive `.value=` misses
    still register the change (execution §4.3).
  - `diff()` → minimal SelectorMap delta (added/removed/changed by node_id+name+value).
  - `read_value(index)` → CDP read-back of an input's live `.value`, used by the
    GATE to *assert* the field was actually filled (not just that we called fill).

Provider import (Playwright) is allowed here — this is a Wave-1/spike adapter.
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

from clarion.contracts.ports import Actuator
from clarion.contracts.state import (
    Action,
    AxNode,
    Observation,
    PageDiff,
    SelectorMap,
)

# AX roles we treat as "interactive" for the spike's numbered map. The full A1
# actuator widens this; the spike only needs to see the text input + submit.
_INTERACTIVE_ROLES = {
    "textbox",
    "searchbox",
    "button",
    "link",
    "checkbox",
    "radio",
    "combobox",
    "menuitem",
    "tab",
    "switch",
}

# The native-setter fill (execution §4.3). Resolves the input via its DOM
# backendNodeId-derived attributes; here the spike resolves by AX `name`+`role`
# using a robust accessible-name query, then applies the prototype value setter
# and dispatches the events React/controlled inputs listen for.
_NATIVE_SETTER_JS = """
(args) => {
  const { selector, value } = args;
  const el = document.querySelector(selector);
  if (!el) return { ok: false, reason: 'element-not-found', selector };
  const proto = el instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, value);
  // Fire the events controlled components (React etc.) listen for.
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return { ok: true, value: el.value };
}
"""


class MinActuator(Actuator):
    """Minimal CDP/Playwright Actuator over a single page (the C2 target).

    Lifecycle: `await MinActuator.create(url)` launches chromium, opens the page,
    attaches a CDP session, and enables the Accessibility + DOM domains. Call
    `await actuator.close()` when done.
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
        # index -> css selector we can hand to page.eval for the native setter.
        self._index_to_selector: dict[int, str] = {}

    # --- lifecycle ----------------------------------------------------------

    @classmethod
    async def create(cls, url: str, *, headless: bool = True) -> "MinActuator":
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=headless)
        page = await browser.new_page()
        await page.goto(url, wait_until="load")
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("Accessibility.enable")
        await cdp.send("DOM.enable")
        return cls(pw, browser, page, cdp)

    async def close(self) -> None:
        try:
            await self._browser.close()
        finally:
            await self._pw.stop()

    # --- Actuator port ------------------------------------------------------

    async def perceive(self) -> SelectorMap:
        """Build the minimal numbered AXTree via `Accessibility.getFullAXTree`.

        We keep only interactive, ignored=false nodes and assign sequential
        indices. We resolve each node to a stable CSS selector (by id / name /
        label) so `act` can target it with the native setter.
        """
        result = await self._cdp.send("Accessibility.getFullAXTree")
        ax_nodes = result.get("nodes", [])

        nodes: dict[int, AxNode] = {}
        self._index_to_selector = {}
        index = 0
        for ax in ax_nodes:
            if ax.get("ignored"):
                continue
            role = (ax.get("role") or {}).get("value", "")
            if role not in _INTERACTIVE_ROLES:
                continue
            name = (ax.get("name") or {}).get("value", "") or ""
            node_id = str(ax.get("nodeId", ""))
            backend_id = ax.get("backendDOMNodeId")

            state = self._extract_state(ax)
            selector = await self._resolve_selector(role, name, backend_id)
            if selector is None:
                # Cannot ground this node back to the DOM → skip it (we never
                # number something we can't act on).
                continue

            nodes[index] = AxNode(
                index=index,
                role=role,
                name=name,
                state=state,
                bbox=None,  # geometry is out of scope for the spike's one field
                node_id=node_id,
            )
            self._index_to_selector[index] = selector
            index += 1

        token_estimate = sum(len(n.role) + len(n.name) for n in nodes.values())
        return SelectorMap(nodes=nodes, token_estimate=token_estimate)

    async def act(self, action: Action) -> Observation:
        """Execute the action against the live page, then re-perceive (§4.3)."""
        if action.kind == "fill":
            if action.index is None or action.value is None:
                return Observation(
                    selector_map=await self.perceive(),
                    success=False,
                    detail="fill requires index and value",
                )
            selector = self._index_to_selector.get(action.index)
            if selector is None:
                return Observation(
                    selector_map=await self.perceive(),
                    success=False,
                    detail=f"no selector for index {action.index}",
                )
            res = await self._page.evaluate(
                _NATIVE_SETTER_JS, {"selector": selector, "value": action.value}
            )
            ok = bool(res.get("ok"))
            after = await self.perceive()
            return Observation(
                selector_map=after,
                success=ok,
                detail="" if ok else f"native-setter failed: {res.get('reason')}",
            )

        if action.kind == "click":
            selector = self._index_to_selector.get(action.index or -1)
            if selector is None:
                return Observation(
                    selector_map=await self.perceive(),
                    success=False,
                    detail=f"no selector for index {action.index}",
                )
            await self._page.click(selector)
            return Observation(selector_map=await self.perceive(), success=True)

        # read / navigate: no-op re-perceive for the spike.
        return Observation(selector_map=await self.perceive(), success=True)

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        before_by_id = {n.node_id: n for n in before.nodes.values()}
        after_by_id = {n.node_id: n for n in after.nodes.values()}
        added_ids = set(after_by_id) - set(before_by_id)
        removed_ids = set(before_by_id) - set(after_by_id)
        changed_ids = {
            nid
            for nid in set(before_by_id) & set(after_by_id)
            if before_by_id[nid].name != after_by_id[nid].name
            or before_by_id[nid].state != after_by_id[nid].state
        }
        added = [n.index for n in after.nodes.values() if n.node_id in added_ids]
        removed = [n.index for n in before.nodes.values() if n.node_id in removed_ids]
        changed = [n.index for n in after.nodes.values() if n.node_id in changed_ids]
        return PageDiff(added=added, removed=removed, changed=changed)

    # --- spike-only read-back (NOT part of the port) ------------------------

    async def read_value(self, index: int) -> Optional[str]:
        """CDP/DOM read-back of an input's *live* `.value` for the GATE assert.

        This is the only honest way to prove the field was actually filled (vs.
        merely that we *called* fill) — execution §7 accept condition (a).
        """
        selector = self._index_to_selector.get(index)
        if selector is None:
            return None
        return await self._page.evaluate(
            "(sel) => { const el = document.querySelector(sel);"
            " return el ? el.value : null; }",
            selector,
        )

    # --- internals ----------------------------------------------------------

    @staticmethod
    def _extract_state(ax: dict) -> dict[str, bool]:
        state: dict[str, bool] = {}
        for prop in ax.get("properties", []) or []:
            pname = prop.get("name")
            pval = (prop.get("value") or {}).get("value")
            if isinstance(pval, bool):
                state[pname] = pval
        return state

    async def _resolve_selector(
        self, role: str, name: str, backend_id: Optional[int]
    ) -> Optional[str]:
        """Resolve an AX node to a CSS selector usable by the native setter.

        Strategy (robust enough for the spike's labeled form): use the DOM
        backendNodeId to pull the element's `id`/`name`/tag via CDP, prefer a
        `#id` selector, fall back to `[name="..."]`, then to a role-derived tag.
        """
        if backend_id is not None:
            try:
                described = await self._cdp.send(
                    "DOM.describeNode", {"backendNodeId": backend_id}
                )
                node = described.get("node", {})
                attrs = node.get("attributes", []) or []
                attr_map = {attrs[i]: attrs[i + 1] for i in range(0, len(attrs), 2)}
                if attr_map.get("id"):
                    return f'#{attr_map["id"]}'
                if attr_map.get("name"):
                    tag = (node.get("localName") or "").lower()
                    return f'{tag}[name="{attr_map["name"]}"]'
            except Exception:
                pass

        # Fallback: role-derived tag (submit button on the spike page).
        if role == "button":
            return 'button[type="submit"]'
        if role in ("textbox", "searchbox"):
            return "input[type='text']"
        return None


async def _selfcheck(url: str) -> None:
    """Standalone actuator self-check: perceive → fill → read-back."""
    act = await MinActuator.create(url, headless=True)
    try:
        sm = await act.perceive()
        print(f"[actuator] perceived {len(sm.nodes)} interactive nodes, "
              f"~{sm.token_estimate} tokens")
        for idx, node in sm.nodes.items():
            print(f"  [{idx}] role={node.role!r} name={node.name!r} "
                  f"node_id={node.node_id}")
        # Find the Full name textbox.
        target = next(
            (i for i, n in sm.nodes.items() if n.role in ("textbox", "searchbox")),
            None,
        )
        assert target is not None, "no textbox found on C2 page"
        before_val = await act.read_value(target)
        print(f"[actuator] before fill, index {target} value={before_val!r}")
        obs = await act.act(Action(kind="fill", index=target, value="Jane Smith"))
        after_val = await act.read_value(target)
        print(f"[actuator] act.success={obs.success}; after fill value={after_val!r}")
        assert after_val == "Jane Smith", f"native-setter fill failed: {after_val!r}"
        print("[actuator] SELF-CHECK PASS — native-setter filled the field")
    finally:
        await act.close()


if __name__ == "__main__":
    import os
    import sys

    target_url = os.environ.get(
        "SPIKE_TARGET_URL",
        (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765"),
    )
    asyncio.run(_selfcheck(target_url))
