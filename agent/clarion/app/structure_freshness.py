"""Structure-cache freshness — the verify-on-use primitive (knowledge-layer).

Best practice recorded in CLAUDE.md / ``docs/clarion-status.md`` and the briefs
(``research/site-cache-freshness-best-practices-2026-06-06.md``): the site-STRUCTURE
cache is **advisory** (the live re-perceive is the authority), so freshness is
handled by **VERIFY-ON-USE, not a TTL**. Each cached page carries a structural
FINGERPRINT; when a page is re-seen we compare the live fingerprint to the cached
one and **supersede on mismatch** (never hard-delete). This is the shape the
industry ships as "self-healing locators": a multi-attribute fingerprint + an
equality/confidence check, fail-loud when it can't resolve.

The fingerprint is intentionally **structure-only** — it hashes the page's title +
headings + affordance LABELS, which ``describe_page`` / ``summarize_ax_tree`` have
already stripped of every StaticText/value. So a changing balance or amount-due can
NEVER perturb it; only a real structural change (a control added/removed/renamed, a
heading change) does. That keeps freshness aligned with the epistemic invariant:
values never enter the cache, so they never drive its refresh.

Pure module — no provider SDKs, no I/O — so it sits in the no-network test gate and
is shared by the writer (``site_indexer``) and any future verify-on-use consumer.
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Literal

from clarion.contracts.state import PageReadout

# Verify-on-use verdict. ``unseen`` = no cached fingerprint (a new page → index it);
# ``fresh`` = cache still valid (skip the re-write); ``stale`` = the page changed
# since we indexed it (→ supersede the cached structure in place).
Freshness = Literal["fresh", "stale", "unseen"]


def _normalized(items: Iterable[str]) -> list[str]:
    """The order- and whitespace-independent SET of non-blank labels (stripped,
    lower-cased, deduped, sorted) — so cosmetic reordering/spacing is not a change,
    but an added/removed/renamed item is."""
    seen = {s.strip().lower() for s in items if s and s.strip()}
    return sorted(seen)


def structure_signature(
    title: str, headings: Iterable[str], affordances: Iterable[str]
) -> str:
    """Order-independent structural fingerprint (sha1, 12 hex) over the title + the
    SET of heading texts + the SET of affordance labels. Values never appear here —
    the caller passes structure only (headings + control labels)."""
    payload = "\x00".join(
        [
            "T:" + title.strip().lower(),
            "H:" + "\x1f".join(_normalized(headings)),
            "A:" + "\x1f".join(_normalized(affordances)),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def page_fingerprint(readout: PageReadout) -> str:
    """Structural fingerprint of a live ORIENT readout (its headings + affordance
    labels). The handle that lets verify-on-use detect that a page has changed."""
    return structure_signature(
        readout.title,
        (h.value for h in readout.headings),
        (a.value for a in readout.affordances),
    )


def compare(cached_fp: str | None, live_fp: str) -> Freshness:
    """Verify-on-use verdict: no cached fingerprint → ``unseen``; equal → ``fresh``
    (skip the re-write); differ → ``stale`` (the page changed → supersede)."""
    if not cached_fp:
        return "unseen"
    return "fresh" if cached_fp == live_fp else "stale"


__all__ = ["Freshness", "structure_signature", "page_fingerprint", "compare"]
