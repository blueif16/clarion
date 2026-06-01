"""POL1 — the demo-mode fallback (execution §9; winner-pattern "demo-mode
fallback so a live run is judge-proof").

``CachedActuator`` implements the FROZEN ``Actuator`` ABC by REPLAYING the
fixture recorded by ``record_fixture.py`` — no real browser, no network. When
``CLARION_DEMO_MODE=1`` the runtime swaps the live ``PlaywrightActuator`` for
this, so the FULL hero run completes even if the network / LiveKit / Gemini / the
demo site is DOWN or the live AXTree drifts.

This is HONEST insurance, not a mock of the outcome:
  - ONLY perception is cached. ``perceive()`` returns the recorded ``SelectorMap``
    for the current page-state; ``act()`` advances that state deterministically
    and records what it WOULD have done; ``diff()`` works off the cached
    before/after maps.
  - The K1 kernel, the ST1 stage graph, the consent gate (``interrupt()`` /
    ``Command(resume=)``), and the two-clause policy still EXECUTE for real over
    this cached perception. The PAY consent HARD-STOP fires from the cached
    submit button exactly as it does live — because the kernel forms the
    irreversible proposal from the perceived tree, and that tree is real (just
    replayed). We never fake the consent decision or a stage transition.

The state machine mirrors the recorded beats (see ``record_fixture.py``):

  login ──click "Sign in"──▶ (nw_auth set)
        ──navigate /account──▶ account
        ──navigate /account/pay──▶ pay_upsell (modal; form occluded)
        ──click "No thanks"──▶ pay_form ──(fills recorded)──▶ pay_filled
        ──click "Submit payment"──▶ pay_submitted (+ confirmation injected)

Resolution is by the node's NAME in the CURRENT cached map (not a stale index),
so the kernel/harness pick indices off a fresh ``perceive()`` and the cached act
advances correctly — identical to how the live actuator behaves.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from clarion.contracts.ports import Actuator
from clarion.contracts.state import (
    Action,
    AxNode,
    Observation,
    PageDiff,
    SelectorMap,
)

DEMO_MODE_ENV = "CLARION_DEMO_MODE"
FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "hero_selectormaps.json"
)


def demo_mode_enabled() -> bool:
    """True when ``CLARION_DEMO_MODE`` is set to a truthy value (1/true/yes)."""
    return os.environ.get(DEMO_MODE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _load_fixture(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"demo-mode fixture not found at {path!r}. Record it first with the "
            f"demo site up:  .venv/bin/python -m clarion.app.record_fixture"
        )
    with open(path) as f:
        return json.load(f)


def _selector_map_from_fixture(raw: dict) -> SelectorMap:
    """Rehydrate a ``SelectorMap`` from the fixture's ``model_dump`` JSON (keys are
    strings in JSON; coerce back to int indices)."""
    nodes = {int(k): AxNode.model_validate(v) for k, v in raw.get("nodes", {}).items()}
    return SelectorMap(nodes=nodes, token_estimate=raw.get("token_estimate", 0))


class _CachedPage:
    """A stand-in for the Playwright ``page`` object the harness reaches through
    ``actuator._page`` for the side-channel DOM reads the Actuator port does not
    cover (``page.url``, ``page.evaluate(js)``). It serves the recorded values
    from the fixture and tracks the cached navigation URL.

    Only the exact ``evaluate`` snippets the hero harness issues are interpreted
    (by substring match against the recorded JS) — anything else returns None,
    surfacing an honest gap rather than silently faking arbitrary JS.
    """

    def __init__(self, actuator: "CachedActuator", base_url: str) -> None:
        self._act = actuator
        self.url = base_url

    async def evaluate(self, js: str, arg: object = None) -> object:
        snippet = js.strip()
        if "sessionStorage.getItem('nw_auth')" in snippet:
            return self._act._nw_auth
        if "getElementById('conf-num')" in snippet:
            return self._act._conf_num if self._act._submitted else None
        if "confirm-banner" in snippet:
            return self._act._banner if self._act._submitted else ""
        # An unmodelled read — honest: return None (the harness treats a None as
        # "not present"), never a fabricated value.
        return None


class CachedActuator(Actuator):
    """Replay the recorded fixture as a deterministic ``Actuator`` (no browser).

    Construct via ``CachedActuator.create(demo_site_url)`` to mirror the
    ``PlaywrightActuator.create`` signature the runtime calls. The ``demo_site_url``
    is used ONLY to seed ``page.url`` and map navigations to cached page-states —
    no request is made.
    """

    def __init__(self, fixture: dict, base_url: str) -> None:
        self._fixture = fixture
        states = fixture["states"]
        self._maps: dict[str, SelectorMap] = {
            key: _selector_map_from_fixture(raw) for key, raw in states.items()
        }
        reads = fixture.get("reads", {})
        self._recorded_values: dict[str, str] = dict(reads.get("pay_form_values", {}))
        conf = reads.get("confirmation", {})
        self._conf_num: Optional[str] = conf.get("conf_num")
        self._banner: str = conf.get("banner", "")
        self._recorded_nw_auth: Optional[str] = reads.get("nw_auth", {}).get("value")

        # --- replay state machine ---
        self._page_key = "login"
        self._nw_auth: Optional[str] = None
        self._submitted = False
        # Live fills recorded by act(fill) → name -> value, for read-back.
        self._fills: dict[str, str] = {}
        # The record of every act we WOULD have performed (honest audit trail).
        self.act_log: list[dict] = []

        base = base_url.rstrip("/")
        self._base_url = base
        self._page = _CachedPage(self, base + "/")

    # --- lifecycle (mirror PlaywrightActuator) ------------------------------

    @classmethod
    async def create(cls, url: str, *, headless: bool = True) -> "CachedActuator":
        fixture = _load_fixture(FIXTURE_PATH)
        return cls(fixture, url)

    async def close(self) -> None:
        # No browser to tear down.
        return None

    # --- internal helpers ---------------------------------------------------

    def _current_map(self) -> SelectorMap:
        """The cached map for the current page-state.

        For the pay form we surface the FILLED variant once a card value has been
        recorded (so a re-perceive after fill reflects the fill, like live)."""
        if self._page_key == "pay_form" and self._fills and "pay_filled" in self._maps:
            return self._maps["pay_filled"]
        if self._page_key not in self._maps:
            raise KeyError(
                f"no cached SelectorMap for page-state {self._page_key!r}; "
                f"recorded: {sorted(self._maps)}"
            )
        return self._maps[self._page_key].model_copy(deep=True)

    def _node_by_index(self, sm: SelectorMap, index: Optional[int]) -> Optional[AxNode]:
        if index is None:
            return None
        return sm.nodes.get(index)

    # --- Actuator port ------------------------------------------------------

    async def perceive(self) -> SelectorMap:
        """Return the cached, recorded merged-AXTree for the current page-state."""
        return self._current_map()

    async def act(self, action: Action) -> Observation:
        """Advance the cached state machine deterministically and record what we
        WOULD have done. Returns an ``Observation`` over the re-perceived (cached)
        map — the same contract the live actuator honours."""
        if action.kind == "navigate":
            return await self._do_navigate(action)
        if action.kind == "fill":
            return await self._do_fill(action)
        if action.kind == "click":
            return await self._do_click(action)
        if action.kind == "read":
            return await self._do_read(action)
        return Observation(
            selector_map=await self.perceive(),
            success=False,
            detail=f"unknown action kind {action.kind!r}",
        )

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        """Page-diff by stable node identity (role+name+node_id) over the cached
        before/after maps — identical algorithm to the live actuator."""

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
        return PageDiff(
            added=sorted(after_by_key[k].index for k in added_keys),
            removed=sorted(before_by_key[k].index for k in removed_keys),
            changed=sorted(after_by_key[k].index for k in changed_keys),
        )

    # --- act helpers (the deterministic state machine) ----------------------

    async def _do_navigate(self, action: Action) -> Observation:
        url = (action.value or "").rstrip("/")
        path = url[len(self._base_url):] if url.startswith(self._base_url) else url
        if path in ("", "/"):
            self._page_key = "login"
        elif path.endswith("/account/pay"):
            self._page_key = "pay_upsell"
        elif path.endswith("/account"):
            self._page_key = "account"
        self._page.url = url + ("/" if path in ("", "/") else "")
        self.act_log.append({"kind": "navigate", "to": self._page_key, "url": action.value})
        return Observation(selector_map=await self.perceive(), success=True)

    async def _do_fill(self, action: Action) -> Observation:
        sm = self._current_map()
        node = self._node_by_index(sm, action.index)
        if node is None or action.value is None:
            return Observation(
                selector_map=sm, success=False,
                detail=f"no cached element for fill index {action.index}",
            )
        # Record the value keyed by the field's accessible name for read-back.
        self._fills[node.name] = action.value
        self.act_log.append({"kind": "fill", "name": node.name, "value": action.value})
        return Observation(selector_map=await self.perceive(), success=True)

    async def _do_click(self, action: Action) -> Observation:
        sm = self._current_map()
        node = self._node_by_index(sm, action.index)
        if node is None:
            return Observation(
                selector_map=sm, success=False,
                detail=f"no cached element for click index {action.index}",
            )
        name = node.name.lower()
        self.act_log.append({"kind": "click", "name": node.name, "page": self._page_key})

        # The deterministic transitions — keyed by what was clicked, exactly the
        # real DOM consequences the record pass captured.
        if "sign in" in name:
            # Authenticate: set the session flag the harness reads off _page.
            self._nw_auth = self._recorded_nw_auth or "1"
            self._page.url = self._base_url + "/account"
            self._page_key = "account"
        elif "no thanks" in name:
            # Dismiss the autopay upsell → the form becomes reachable.
            self._page_key = "pay_form"
        elif "submit payment" in name:
            # The IRREVERSIBLE act (only reached after the consent approve in the
            # kernel) → submitted state + the async confirmation injection.
            self._submitted = True
            self._page_key = "pay_submitted"

        return Observation(selector_map=await self.perceive(), success=True)

    async def _do_read(self, action: Action) -> Observation:
        value = await self.read_value(action.index) if action.index is not None else None
        return Observation(
            selector_map=await self.perceive(),
            success=value is not None,
            detail="" if value is None else str(value),
        )

    # --- read-back (mirror PlaywrightActuator.read_value) -------------------

    async def read_value(self, index: int) -> Optional[str]:
        """Cached live-value read-back. Returns a value we recorded via fill, else
        the recorded default for that field (e.g. the amount's defaultValue), else
        None. Not part of the Actuator port — the honest way the harness proves a
        field carries a value (mirrors ``PlaywrightActuator.read_value``)."""
        sm = self._current_map()
        node = sm.nodes.get(index)
        if node is None:
            return None
        if node.name in self._fills:
            return self._fills[node.name]
        # Map the field's accessible name to the recorded read-back default.
        defaults = {
            "Payment amount": self._recorded_values.get("amount"),
            "Card number": self._recorded_values.get("card"),
            "Expiry": self._recorded_values.get("expiry"),
        }
        if node.name in defaults and defaults[node.name] is not None:
            return defaults[node.name]
        return None


__all__ = [
    "CachedActuator",
    "demo_mode_enabled",
    "DEMO_MODE_ENV",
    "FIXTURE_PATH",
]
