"""The "remember?" gate — end-of-flow preference capture (no memory without a yes).

The third consent clause of the kernel, applied to the privacy surface: nothing
about the user is persisted unless they explicitly said "yes, remember that". After
a flow completes, Clarion NOMINATES the reusable values the user supplied as
preference candidates, SUPPRESSES anything sensitive (a secret is NEVER even
offered), batches them into ONE spoken offer, and on an explicit yes writes each
kept candidate via ``Memory.write_preference``.

This module owns the PURE nomination + secret-suppression + write — testable with
no voice/LLM dependency. The batched spoken consent itself is the caller's job
(``voice_entry`` for the live product path; the autonomous driver has no human, so
it never offers) and is wired there, NOT here.

Secret suppression is a CONSERVATIVE PRIVACY GUARD, not site-specific behavior: it
classifies a FIELD's sensitivity generically (by accessible name + a structural
password signal), never a per-site rule, and suppresses when unsure. The richer
option — a Reasoner sensitivity classification — can replace ``is_sensitive_field``
behind the same seam later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from clarion.contracts.ports import Memory
from clarion.contracts.state import SelectorMap

# Generic SECRET markers — a value whose field name matches any of these is never
# offered for memory. Word-boundary matched so short markers ("pin", "cvv") don't
# false-trip on ordinary fields ("shipping", "service"). Conservative by design.
_SECRET_MARKERS = (
    "password", "passcode", "pin", "otp", "one-time", "one time", "2fa",
    "verification code", "security code", "cvv", "cvc", "card number",
    "credit card", "debit card", "account number", "routing number", "routing",
    "ssn", "social security", "secret", "private key", "seed phrase", "passphrase",
)
_SECRET_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in _SECRET_MARKERS) + r")\b", re.IGNORECASE
)


@dataclass(frozen=True)
class RememberCandidate:
    """One reusable value worth offering to remember (key = the field's label)."""

    key: str
    value: str


def is_sensitive_field(name: str, role: str = "", state: dict | None = None) -> bool:
    """Is this field one we must NEVER offer to remember? True for a name that
    matches a secret marker OR a structural password/protected signal (when the
    actuator surfaces it). Conservative: when unsure, prefer suppression."""
    if _SECRET_RE.search(name or ""):
        return True
    st = state or {}
    if st.get("password") or st.get("protected"):
        return True
    return (role or "").strip().lower() == "password"


def nominate_remember_candidates(
    filled: dict[str, str], page_index: SelectorMap
) -> list[RememberCandidate]:
    """From a finished run's filled fields (``node_id -> typed value``) + the live
    tree, nominate reusable preference candidates, SUPPRESSING secrets. Keys are the
    fields' accessible names — generic, never a per-site list. Blank values and
    duplicate (key, value) pairs are skipped."""
    by_id = {n.node_id: n for n in page_index.nodes.values()}
    out: list[RememberCandidate] = []
    seen: set[tuple[str, str]] = set()
    for node_id, value in (filled or {}).items():
        v = (value or "").strip()
        if not v:
            continue
        node = by_id.get(node_id)
        name = (node.name if node else "") or ""
        role = (node.role if node else "") or ""
        st = (node.state if node else {}) or {}
        if is_sensitive_field(name, role, st):
            continue  # a secret is never even offered.
        key = name.strip() or "value"
        if (key, v) in seen:
            continue
        seen.add((key, v))
        out.append(RememberCandidate(key=key, value=v))
    return out


async def remember_candidates(
    memory: Memory, user_id: str, approved: list[RememberCandidate]
) -> int:
    """Write the APPROVED candidates as preferences — called ONLY after the user
    said "yes" at the batched consent (no memory without a yes). Returns how many
    were written. Best-effort: a single miss is swallowed, never raised."""
    written = 0
    for c in approved:
        try:
            await memory.write_preference(user_id, c.key, c.value, origin="stated")
            written += 1
        except Exception:  # noqa: BLE001 — a memory miss must never break the flow.
            pass
    return written


__all__ = [
    "RememberCandidate",
    "is_sensitive_field",
    "nominate_remember_candidates",
    "remember_candidates",
]
