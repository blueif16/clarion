"""Probe: why did the live perceive COLLAPSE to 3 nodes after interacting with the
recreation.gov home search combobox (worker.log 10:49: CONFIRM.exit nodes=3), and
does pressing ENTER on the filled field submit the search (the capability the
decider asked for — "need to submit via Enter on field" — but had no verb for)?

Reproduces the live run's exact sequence through the REAL actuator:
  1. perceive the home page                  (live run: 245 nodes)
  2. fill the search combobox "Point Reyes"  (live run: ok, 248 nodes)
  3. click the combobox                      (live run: collapse → 3 nodes)
  4. perceive again after a settle           (does it recover on its own?)
  5. RAW getFullAXTree at the collapsed step (is the collapse in the raw tree,
     or in our merge/filter?)
  6. press Enter on the field via CDP        (does the search actually run?)

Run:  cd agent && CLARION_PROBE_HEADLESS=1 .venv/bin/python -m probes.recreation_search_overlay_probe
Throwaway — not part of the gate.
"""
from __future__ import annotations

import asyncio
import os

from clarion.actuator.actuator import PlaywrightActuator
from clarion.contracts.state import Action

URL = os.environ.get("CLARION_PROBE_URL", "https://www.recreation.gov/")
HEADLESS = os.environ.get("CLARION_PROBE_HEADLESS", "1") == "1"
QUERY = "Point Reyes"


def _dump(sm, label: str, cap: int = 40) -> None:
    print(f"\n## {label}: {len(sm.nodes)} interactive nodes")
    if len(sm.nodes) <= cap:
        for idx in sorted(sm.nodes):
            n = sm.nodes[idx]
            st = {k: v for k, v in n.state.items() if v}
            print(f"   [{idx:3d}] {n.role:12s} {n.name[:90]!r}  state={st}")


async def _raw_count(cdp) -> int:
    raw = await cdp.send("Accessibility.getFullAXTree")
    return len(raw.get("nodes", []))


async def main() -> None:
    print(f"# Probe target: {URL}\n# headless={HEADLESS}\n")
    act = await PlaywrightActuator.create(URL, headless=HEADLESS)
    page = act._page  # probe-only access
    cdp = act._cdp
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception as e:
        print(f"[warn] networkidle wait: {e}")

    # --- 1. baseline perceive -------------------------------------------------
    sm = await act.perceive()
    _dump(sm, "STEP 1 — home page baseline")
    print(f"   raw AXTree nodes: {await _raw_count(cdp)}")

    # The search combobox, found by ROLE (structural, like the live decider did).
    search_idx = None
    for idx in sorted(sm.nodes):
        if sm.nodes[idx].role in ("combobox", "searchbox", "textbox"):
            search_idx = idx
            break
    if search_idx is None:
        print("!! no search field in the map — site changed? aborting")
        await act.close()
        return
    node = sm.nodes[search_idx]
    print(f"\n→ search field: [{search_idx}] {node.role} {node.name!r}")

    # --- 2. fill it (the live run's subgoal-0 act) -----------------------------
    obs = await act.act(Action(kind="fill", index=search_idx, value=QUERY))
    _dump(obs.selector_map, f"STEP 2 — after fill({QUERY!r}) success={obs.success}")
    print(f"   raw AXTree nodes: {await _raw_count(cdp)}")

    # --- 3. click the combobox (the act that collapsed the live map) -----------
    sm2 = obs.selector_map
    click_idx = None
    for idx in sorted(sm2.nodes):
        if sm2.nodes[idx].role in ("combobox", "searchbox") or (
            sm2.nodes[idx].role == "textbox" and "search" in sm2.nodes[idx].name.lower()
        ):
            click_idx = idx
            break
    if click_idx is not None:
        obs2 = await act.act(Action(kind="click", index=click_idx))
        _dump(obs2.selector_map, f"STEP 3 — after click on combobox success={obs2.success}")
        print(f"   raw AXTree nodes: {await _raw_count(cdp)}")
        collapsed = len(obs2.selector_map.nodes) < len(sm2.nodes) // 4
        print(f"   COLLAPSE reproduced: {collapsed}")

        # --- 4. does it recover on its own after a settle? ---------------------
        await page.wait_for_timeout(2_500)
        sm3 = await act.perceive()
        _dump(sm3, "STEP 4 — re-perceive after 2.5s settle")
        print(f"   raw AXTree nodes: {await _raw_count(cdp)}")

    # --- 6. press ENTER on the filled field (the proposed submit capability) ---
    # Re-perceive to get a live index for the field, then Enter via CDP — the same
    # transport-shared shape the actuator fix will use.
    sm4 = await act.perceive()
    enter_idx = None
    for idx in sorted(sm4.nodes):
        if sm4.nodes[idx].role in ("combobox", "searchbox", "textbox"):
            enter_idx = idx
            break
    if enter_idx is None:
        print("!! no field to press Enter on")
        await act.close()
        return
    backend = act._index_to_backend_id.get(enter_idx)
    url_before = page.url
    print(f"\n→ pressing Enter on [{enter_idx}] backend={backend} url={url_before!r}")
    await cdp.send("DOM.focus", {"backendNodeId": backend})
    await cdp.send(
        "Input.dispatchKeyEvent",
        {"type": "keyDown", "key": "Enter", "code": "Enter",
         "windowsVirtualKeyCode": 13, "text": "\r"},
    )
    await cdp.send(
        "Input.dispatchKeyEvent",
        {"type": "keyUp", "key": "Enter", "code": "Enter",
         "windowsVirtualKeyCode": 13},
    )
    try:
        await page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass
    await page.wait_for_timeout(1_500)
    print(f"   url after Enter: {page.url!r}")
    print(f"   NAVIGATED: {page.url != url_before}")
    sm5 = await act.perceive()
    print(f"   nodes on the result page: {len(sm5.nodes)}")
    hits = [n.name for n in sm5.nodes.values() if "point reyes" in n.name.lower()]
    print(f"   'Point Reyes' result nodes: {len(hits)}")
    for nm in hits[:5]:
        print(f"      · {nm[:100]}")

    await act.close()


if __name__ == "__main__":
    asyncio.run(main())
