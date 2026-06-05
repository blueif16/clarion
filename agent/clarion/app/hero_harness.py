"""RETIRED — the hand-driven pay-topology hero harness is gone.

This module USED to drive a baked AUTH→LOCATE→FILL→REVIEW→PAY→CONFIRM script with
demo-site creds, per-stage done-predicates (``auth_done`` / ``locate_done`` / …)
and a hardcoded "Submit payment" button name. Those predicates were DELETED in the
de-hardcoding migration (architecture Step 4 — the done registry is gone; "done" is
now the reasoner-SELECTED generic check evaluated in CODE by ``stages.checks``), so
the old harness no longer imports. There is nothing to salvage from a site-specific
script in a de-hardcoded system.

The proof that the Task plane drives a full goal end-to-end is now the GENERIC,
site-agnostic driver: ``clarion.app.gov_proof``. It runs ``HeroRuntime.create`` +
``build_stage_graph`` (the LLM-derived plan + the kernel loop per subgoal + the
generic done-check) over a real ``PlaywrightActuator`` + the live ``GeminiReasoner``,
on REAL sites, with ZERO baked topology and an autonomous consent policy that never
approves an irreversible step on a live third-party site.

Kept import-clean (``python -c "import clarion.app.hero_harness"`` succeeds) so
nothing that referenced this module breaks; running it just points at the new
driver.

Run the real proof:
  .venv/bin/python -m clarion.app.gov_proof        # both goals on two real sites
"""

from __future__ import annotations

import sys

_RETIRED_MSG = (
    "clarion.app.hero_harness is RETIRED (the hardcoded pay topology was deleted in "
    "the de-hardcoding migration). The end-to-end proof is now the generic driver:\n"
    "    .venv/bin/python -m clarion.app.gov_proof\n"
)


def main() -> int:
    """Print the retirement notice and hand off to the generic driver."""
    import asyncio

    sys.stderr.write(_RETIRED_MSG)
    from clarion.app import gov_proof

    return asyncio.run(gov_proof.main())


__all__ = ["main"]


if __name__ == "__main__":
    sys.exit(main())
