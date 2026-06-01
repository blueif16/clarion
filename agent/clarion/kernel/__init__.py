"""K1 — the Clarion kernel (execution §2).

The 6-node LangGraph loop (GROUND→VERIFY→PROPOSE→⟨CONSENT⟩→ACT→CONFIRM), the
two-clause policy, the two autonomy modes, idempotent ACT, and trace emission.
Imports only ``clarion.contracts`` (ports + state) — never a real provider SDK
(foundation §6 invariant)."""

from clarion.kernel.graph import Mode, build_kernel, make_checkpointer, seed_state
from clarion.kernel.policy import (
    PolicyViolation,
    assert_consented,
    assert_grounded,
    is_consented,
    is_grounded,
    speakable,
)

__all__ = [
    "build_kernel",
    "seed_state",
    "make_checkpointer",
    "Mode",
    "assert_grounded",
    "assert_consented",
    "is_grounded",
    "is_consented",
    "speakable",
    "PolicyViolation",
]
