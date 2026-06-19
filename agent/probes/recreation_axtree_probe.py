"""Feasibility probe: does Clarion's AXTree perception surface recreation.gov's
date-availability grid well enough to summarize the fields and pick one date?

Run:  cd agent && CLARION_PROBE_HEADLESS=1 .venv/bin/python -m probes.recreation_axtree_probe
Throwaway — not part of the gate.
"""
from __future__ import annotations

import asyncio
import collections
import os
import re

from clarion.actuator.actuator import PlaywrightActuator

URL = os.environ.get(
    "CLARION_PROBE_URL",
    "https://www.recreation.gov/camping/campgrounds/233359",
)
HEADLESS = os.environ.get("CLARION_PROBE_HEADLESS", "1") == "1"

DATE_RE = re.compile(
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
    r"mon|tue|wed|thu|fri|sat|sun|\b20\d\d\b|available|reserved|walk-?up|"
    r"not available|closed)",
    re.I,
)


def _flatten_ax(ax_tree: dict) -> list[dict]:
    return ax_tree.get("nodes", [])


def _name_of(node: dict) -> str:
    n = node.get("name") or {}
    return (n.get("value") or "") if isinstance(n, dict) else str(n)


def _role_of(node: dict) -> str:
    r = node.get("role") or {}
    return (r.get("value") or "") if isinstance(r, dict) else str(r)


async def main() -> None:
    print(f"# Probe target: {URL}\n# headless={HEADLESS}\n")
    act = await PlaywrightActuator.create(URL, headless=HEADLESS)
    page = act._page  # probe-only access
    cdp = act._cdp

    # The availability grid loads lazily. Give the SPA time + try to wait for a
    # table/grid to appear; don't hard-fail if the selector never resolves.
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception as e:
        print(f"[warn] networkidle wait: {e}")
    for sel in ("table", "[role=grid]", "[role=table]", "button[aria-label]"):
        try:
            await page.wait_for_selector(sel, timeout=8_000)
            print(f"[ok] found selector: {sel}")
            break
        except Exception:
            continue
    await page.wait_for_timeout(4_000)

    title = await page.title()
    print(f"\n## Page title: {title!r}")

    # --- RAW full AXTree: what is the grid actually MADE of? -----------------
    raw = await cdp.send("Accessibility.getFullAXTree")
    nodes = _flatten_ax(raw)
    role_hist = collections.Counter(_role_of(n) for n in nodes)
    print(f"\n## RAW AXTree: {len(nodes)} total nodes")
    print("## Role histogram (top 30):")
    for role, cnt in role_hist.most_common(30):
        print(f"   {cnt:5d}  {role or '<empty>'}")

    # Date-looking names by role — is the availability data even IN the a11y tree?
    by_role_dateish: dict[str, list[str]] = collections.defaultdict(list)
    for n in nodes:
        nm = _name_of(n)
        if nm and DATE_RE.search(nm):
            by_role_dateish[_role_of(n)].append(nm)
    print("\n## Date/availability-looking accessible NAMES, grouped by role:")
    for role, names in sorted(by_role_dateish.items(), key=lambda kv: -len(kv[1])):
        print(f"\n   [{role or '<empty>'}]  ({len(names)} nodes)")
        for nm in names[:12]:
            print(f"      · {nm[:110]}")
        if len(names) > 12:
            print(f"      … +{len(names) - 12} more")

    # --- The REAL Clarion perception: numbered SelectorMap -------------------
    sm = await act.perceive()
    print(f"\n## Clarion SelectorMap: {len(sm.nodes)} INTERACTIVE nodes "
          f"(token_estimate={sm.token_estimate})")
    sm_role_hist = collections.Counter(n.role for n in sm.nodes.values())
    print("## Interactive role histogram:")
    for role, cnt in sm_role_hist.most_common():
        print(f"   {cnt:5d}  {role}")

    print("\n## Interactive nodes whose NAME looks date/availability-ish:")
    hit = 0
    for idx in sorted(sm.nodes):
        node = sm.nodes[idx]
        if node.name and DATE_RE.search(node.name):
            hit += 1
            if hit <= 40:
                print(f"   [{idx:3d}] {node.role:10s} {node.name[:95]!r}")
    print(f"   → {hit} interactive nodes carry a date/availability name")

    # --- FILLABLE FORM FIELDS (what a payment page is all about) ------------
    FILL_ROLES = {
        "textbox", "searchbox", "combobox", "listbox", "checkbox",
        "radio", "spinbutton", "switch", "slider", "textarea",
    }
    print("\n## FILLABLE interactive fields (textbox/combobox/checkbox/...):")
    nfill = 0
    for idx in sorted(sm.nodes):
        node = sm.nodes[idx]
        if node.role in FILL_ROLES:
            nfill += 1
            st = {k: v for k, v in node.state.items() if v}
            print(f"   [{idx:3d}] {node.role:10s} {node.name[:80]!r}  state={st}")
    print(f"   → {nfill} fillable fields")

    # --- describe_page readout (the ORIENT summary the voice plane speaks) ---
    try:
        readout = await act.describe_page()
        items = getattr(readout, "items", None) or []
        print(f"\n## describe_page(): {len(items)} grounded readout items")
        for it in items[:20]:
            txt = getattr(it, "text", None) or getattr(it, "name", None) or str(it)
            print(f"   · {str(txt)[:100]}")
    except Exception as e:
        print(f"\n[warn] describe_page failed: {e}")

    await act.close()


if __name__ == "__main__":
    asyncio.run(main())
