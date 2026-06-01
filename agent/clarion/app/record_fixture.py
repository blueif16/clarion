"""POL1 — the RECORD pass for the demo-mode fallback (execution §9).

Runs the REAL ``PlaywrightActuator`` through the hero flow ONCE against the LIVE
demo site and serializes each distinct page-state's merged ``SelectorMap`` (plus
the side-channel DOM reads the harness needs) to
``app/fixtures/hero_selectormaps.json``.

This is the CAPTURE step. It is the only thing in demo mode that touches a live
browser/network — everything afterwards (``demo_mode.CachedActuator``) replays
this file with no browser at all, so the FULL hero run is judge-proof even if the
network / LiveKit / Gemini / the demo site is down or the AXTree drifts.

What is honest about this: the fixture records ONLY *perception* — the merged
numbered AXTree the screen reader sees at each beat — exactly as the live
``PlaywrightActuator`` produced it. The K1 kernel, ST1 stage graph, the consent
gate, and the policy are NOT recorded; in demo mode they execute for real over
the cached perception. We cache what the agent *sees*, never what it *decides*.

The recorded states (the "step keys") mirror the harness beats:

  - ``login``         — initial AUTH page (the unlabeled-password RESCUE trigger).
  - ``account``       — after navigate /account (the logged-in marker present).
  - ``pay_upsell``    — after navigate /account/pay (autopay modal open; the form
                        is occluded — PaintOrderRemover sees only the modal).
  - ``pay_form``      — after dismissing the upsell ("No thanks"); the payment
                        form (amount/card/expiry/submit) is reachable.
  - ``pay_submitted`` — after the consented submit click (button → 'Submitted').

Side-channel DOM reads captured alongside (the harness reads these off ``_page``
directly, not via the Actuator port): ``nw_auth`` session flag, the live
confirmation number, and the layout-shift ``.confirm-banner`` text.

Run (demo site MUST be up for this pass only):
    cd web/demo-site && npm run dev -- --port 8770
    cd agent && DEMO_SITE_URL=http://localhost:8770/ \\
        .venv/bin/python -m clarion.app.record_fixture
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Optional

from clarion.contracts.state import Action, SelectorMap

DEMO_SITE_URL = os.environ.get("DEMO_SITE_URL", "http://localhost:8770/")
HEADLESS = os.environ.get("HERO_HEADLESS", "1") != "0"

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                            "hero_selectormaps.json")

# The demo login (sandbox creds — no real account; foundation §9).
_DEMO_USER = "demo@user.com"
_DEMO_PASS = "demo"


def _log(msg: str) -> None:
    print(f"  [record] {msg}", flush=True)


def _find(sm: SelectorMap, *, role: Optional[str] = None, name_has: Optional[str] = None):
    """First node matching role and/or a case-insensitive name substring."""
    for i, n in sm.nodes.items():
        if role is not None and n.role != role:
            continue
        if name_has is not None and name_has.lower() not in n.name.lower():
            continue
        return i, n
    return None, None


async def record(url: str) -> dict:
    """Drive the live actuator through the hero flow, capturing each state."""
    from clarion.actuator.actuator import PlaywrightActuator

    act = await PlaywrightActuator.create(url, headless=HEADLESS)
    page = act._page  # type: ignore[attr-defined]
    states: dict[str, dict] = {}
    reads: dict[str, dict] = {}

    try:
        # ---------------------------------------------------------- login
        await asyncio.sleep(1.0)  # let the React login page hydrate
        sm = await act.perceive()
        states["login"] = sm.model_dump()
        _log(f"login: {len(sm.nodes)} nodes "
             f"{[(i, n.role, n.name) for i, n in sm.nodes.items()]}")

        # Choreograph the login exactly as the harness does (so the recorded
        # post-auth state matches what the harness will replay).
        u_idx, _ = _find(sm, role="textbox", name_has="Username")
        p_idx = next(
            (i for i, n in sm.nodes.items() if n.role == "textbox" and not n.name.strip()),
            None,
        )
        sign_idx, _ = _find(sm, role="button", name_has="Sign in")
        await act.act(Action(kind="fill", index=u_idx, value=_DEMO_USER))
        await act.act(Action(kind="fill", index=p_idx, value=_DEMO_PASS))
        await act.act(Action(kind="click", index=sign_idx))
        await asyncio.sleep(1.2)
        authed = await page.evaluate("sessionStorage.getItem('nw_auth')")
        reads["nw_auth"] = {"value": authed}
        _log(f"post sign-in: url={page.url} nw_auth={authed!r}")

        # -------------------------------------------------------- account
        await act.act(Action(kind="navigate", value=url.rstrip("/") + "/account"))
        await asyncio.sleep(1.0)
        sm = await act.perceive()
        states["account"] = sm.model_dump()
        _log(f"account: {len(sm.nodes)} nodes "
             f"{[(i, n.role, n.name) for i, n in sm.nodes.items()]}")

        # ----------------------------------------------------- pay_upsell
        await act.act(Action(kind="navigate", value=url.rstrip("/") + "/account/pay"))
        await asyncio.sleep(1.3)  # hydration + the upsell modal mounts
        sm = await act.perceive()
        states["pay_upsell"] = sm.model_dump()
        _log(f"pay_upsell (modal open, form occluded): {len(sm.nodes)} nodes "
             f"{[(i, n.name) for i, n in sm.nodes.items()]}")

        # Dismiss the autopay upsell ("No thanks") to reach the form.
        dismiss_idx, _ = _find(sm, name_has="No thanks")
        await act.act(Action(kind="click", index=dismiss_idx))
        await asyncio.sleep(0.6)

        # ------------------------------------------------------- pay_form
        sm = await act.perceive()
        states["pay_form"] = sm.model_dump()
        _log(f"pay_form (form reachable): {len(sm.nodes)} nodes "
             f"{[(i, n.name) for i, n in sm.nodes.items()]}")

        # Fill card + expiry (native-setter) and record the read-backs so the
        # cached actuator can replay the exact post-fill values.
        card_idx, _ = _find(sm, name_has="Card")
        exp_idx, _ = _find(sm, name_has="Expiry")
        amt_idx, _ = _find(sm, name_has="Payment amount")
        await act.act(Action(kind="fill", index=card_idx, value="4242 4242 4242 4242"))
        await act.act(Action(kind="fill", index=exp_idx, value="12 / 28"))
        reads["pay_form_values"] = {
            "amount": await act.read_value(amt_idx),
            "card": await act.read_value(card_idx),
            "expiry": await act.read_value(exp_idx),
        }
        _log(f"post-fill read-backs: {reads['pay_form_values']}")

        # Re-perceive the filled form (its node states may carry the value).
        sm = await act.perceive()
        states["pay_filled"] = sm.model_dump()

        # ------------------------------------------------- pay_submitted
        # Click the irreversible Submit (the real act the kernel gates on consent).
        submit_idx, _ = _find(sm, role="button", name_has="Submit payment")
        await act.act(Action(kind="click", index=submit_idx))
        await asyncio.sleep(1.8)  # the confirmation banner is injected ~1.2s later
        sm = await act.perceive()
        states["pay_submitted"] = sm.model_dump()
        _log(f"pay_submitted: {len(sm.nodes)} nodes "
             f"{[(i, n.name) for i, n in sm.nodes.items()]}")

        conf_num = await page.evaluate("document.getElementById('conf-num')?.textContent")
        banner = await page.evaluate(
            "document.querySelector('.confirm-banner')?.textContent || ''"
        )
        reads["confirmation"] = {"conf_num": conf_num, "banner": banner}
        _log(f"confirmation: conf_num={conf_num!r} banner_present={bool(banner)}")

    finally:
        await act.close()

    return {
        "recorded_at": time.time(),
        "source_url": url,
        "states": states,
        "reads": reads,
    }


async def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else DEMO_SITE_URL
    print(f"RECORD PASS — live actuator over {url} (headless={HEADLESS})", flush=True)
    fixture = await record(url)

    os.makedirs(os.path.dirname(FIXTURE_PATH), exist_ok=True)
    with open(FIXTURE_PATH, "w") as f:
        json.dump(fixture, f, indent=2)

    print(f"\nFIXTURE WRITTEN → {FIXTURE_PATH}", flush=True)
    print(f"  stage keys: {sorted(fixture['states'])}", flush=True)
    print(f"  side-channel reads: {sorted(fixture['reads'])}", flush=True)
    for key, sm in fixture["states"].items():
        print(f"  - {key:14s} {len(sm['nodes'])} nodes  ~{sm['token_estimate']} tokens", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
