"""AUTO-INDEX — the background crawl-on-activation trigger for the knowledge layer.

This is the piece that makes the site-STRUCTURE knowledge layer **self-populating**:
when the agent activates on a page (the planner's first ORIENT), it kicks a read-only
crawl of that host's PUBLIC structure into the shared ``clarion-site-structure`` Moss
index, so later planning has a SITE MAP without a manual ``site_indexer`` run. The
crawl IS ``site_indexer.crawl_and_index`` — its own cookie-less Playwright browser,
GET-only, denylist-guarded, bounded by ``max_pages``/``max_depth``, and (since the
freshness pass) stable-per-URL + fingerprinted so a re-run **supersedes** in place.

Safety / invariants (why this is allowed to run automatically):
  - **PUBLIC by construction.** The crawl runs in a FRESH browser with NO user
    session, so it can only ever reach anonymously-public pages — it cannot see or
    write the user's authenticated/private pages. The private/live surface stays
    DEFERRED behind the consent path; auto-index never writes a private page to the
    shared index. The existing ``_DENY`` denylist refuses destructive GETs. (No new
    lexical/keyword classifier — the cookie-less-crawler property IS the gate, which
    keeps us clear of the no-hardcoded-heuristics rule.)
  - **NON-BLOCKING.** Scheduled as a fire-and-forget background task; the activation
    turn never waits on it.
  - **FAIL-OPEN.** Any error (no creds, no Playwright, network) is swallowed —
    auto-index is a best-effort warm-up, never a hard dependency.
  - **GATED.** Off unless ``CLARION_AUTO_INDEX=1`` (so the no-network test gate, demo
    mode, and the event-day live worker are untouched unless explicitly enabled).
  - **THROTTLED.** Each host is crawled at most once per process (an in-memory
    guard), so a multi-turn session never re-crawls. Cross-process freshness is the
    re-crawl supersede + fingerprint (``structure_freshness``), not a TTL.
"""

from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, Optional

# Hosts already crawled (or in flight) in THIS process — the per-process throttle.
_SEEN_HOSTS: set[str] = set()


def auto_index_enabled() -> bool:
    """True iff auto-index is opted in via ``CLARION_AUTO_INDEX=1`` (default off)."""
    return os.environ.get("CLARION_AUTO_INDEX") == "1"


def _reset_seen() -> None:
    """Clear the per-process host throttle (tests only)."""
    _SEEN_HOSTS.clear()


async def auto_index_host(
    url: str,
    *,
    crawl: Optional[Callable[..., Awaitable]] = None,
    log: Callable[[str], None] = lambda _m: None,
) -> bool:
    """Run a read-only PUBLIC structure crawl of ``url``'s host into the shared
    index — **gated, throttled, fail-open**. Returns ``True`` iff a crawl ran.

    ``crawl`` is injectable (tests); it defaults to ``site_indexer.crawl_and_index``.
    """
    if not auto_index_enabled():
        return False
    from clarion.app.site_indexer import host_of, is_denied_url  # light (stdlib only)

    host = host_of(url)
    if not host or host in _SEEN_HOSTS:
        return False
    # Don't auto-crawl when the user is sitting on a denylisted SEED (a /logout,
    # /login, delete… page) — and don't consume the host's throttle slot, so a later
    # activation on a normal page of the same host can still index it.
    if is_denied_url(url):
        log(f"[auto-index] seed {url!r} is denylisted (auth/destructive) — skipped")
        return False
    # Claim the host BEFORE awaiting so two concurrent activations don't double-crawl.
    _SEEN_HOSTS.add(host)
    try:
        if crawl is None:
            from clarion.app.site_indexer import crawl_and_index

            crawl = crawl_and_index
        max_pages = int(os.environ.get("CLARION_CRAWL_MAX_PAGES", "6"))
        max_depth = int(os.environ.get("CLARION_CRAWL_MAX_DEPTH", "1"))
        log(
            f"[auto-index] crawling public structure of {host!r} "
            f"(max_pages={max_pages}, depth={max_depth})"
        )
        await crawl(url, max_pages=max_pages, max_depth=max_depth, log=log)
        return True
    except Exception as exc:  # noqa: BLE001 - best-effort warm-up; never propagate.
        # Release the host so a later activation can retry after a transient failure.
        _SEEN_HOSTS.discard(host)
        log(f"[auto-index] skipped {host!r} — {exc}")
        return False


def schedule_auto_index(
    url: str, *, log: Callable[[str], None] = lambda _m: None
) -> Optional["asyncio.Task"]:
    """Fire-and-forget ``auto_index_host`` as a background task (NON-blocking) when
    enabled and a running loop exists; otherwise a no-op. Never raises into the
    caller — safe to call straight from the planner's ORIENT hook."""
    if not auto_index_enabled():
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None  # no running loop (sync context) — skip rather than block.
    try:
        return loop.create_task(auto_index_host(url, log=log))
    except Exception:  # noqa: BLE001 - scheduling is best-effort.
        return None


__all__ = ["auto_index_enabled", "auto_index_host", "schedule_auto_index"]
