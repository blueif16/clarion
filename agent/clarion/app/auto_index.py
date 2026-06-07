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
  - **THROTTLED (per landing-page URL).** Each distinct page URL is crawled at most
    once per process — so the agent indexes EVERY public page it navigates to, but
    never re-crawls one within a session. After a crawl, every page it actually
    indexed (the seed + its BFS neighbours) is marked seen, so landing later on a
    page already reached as a neighbour won't re-crawl it. Cross-process freshness is
    the re-crawl supersede + fingerprint (``structure_freshness``), not a TTL.
"""

from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit, urlunsplit

# Page URLs already crawled (or in flight) in THIS process — the per-landing-page
# throttle, so a session indexes every public page it visits, each at most once.
_SEEN_URLS: set[str] = set()


def auto_index_enabled() -> bool:
    """True iff auto-index is opted in via ``CLARION_AUTO_INDEX=1`` (default off)."""
    return os.environ.get("CLARION_AUTO_INDEX") == "1"


def _url_key(url: str) -> str:
    """A normalized dedup key for a page URL: scheme + host (lower-cased) + path
    (trailing slash trimmed) + query, with the fragment dropped. ``""`` for a
    blank/garbage URL (which is then skipped)."""
    try:
        s = urlsplit((url or "").strip())
    except ValueError:
        return ""
    if s.scheme not in ("http", "https") or not s.netloc:
        return ""
    path = s.path.rstrip("/") or "/"
    return urlunsplit((s.scheme.lower(), s.netloc.lower(), path, s.query, ""))


def _reset_seen() -> None:
    """Clear the per-process URL throttle (tests only)."""
    _SEEN_URLS.clear()


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
    from clarion.app.site_indexer import is_denied_url  # light (stdlib only)

    key = _url_key(url)
    if not key or key in _SEEN_URLS:
        return False
    # Don't auto-crawl a denylisted SEED (a /logout, /login, delete… page) — and
    # don't consume its throttle slot, so a later normal page still indexes.
    if is_denied_url(url):
        log(f"[auto-index] seed {url!r} is denylisted (auth/destructive) — skipped")
        return False
    # Claim the URL BEFORE awaiting so two concurrent navigations don't double-crawl.
    _SEEN_URLS.add(key)
    try:
        if crawl is None:
            from clarion.app.site_indexer import crawl_and_index

            crawl = crawl_and_index
        max_pages = int(os.environ.get("CLARION_CRAWL_MAX_PAGES", "6"))
        max_depth = int(os.environ.get("CLARION_CRAWL_MAX_DEPTH", "1"))
        log(
            f"[auto-index] crawling public structure from {url!r} "
            f"(max_pages={max_pages}, depth={max_depth})"
        )
        result = await crawl(url, max_pages=max_pages, max_depth=max_depth, log=log)
        # Mark every page the crawl actually indexed (the seed + its BFS neighbours)
        # so navigating later to one already reached as a neighbour won't re-crawl it.
        for u in getattr(result, "pages", None) or []:
            k = _url_key(u)
            if k:
                _SEEN_URLS.add(k)
        return True
    except Exception as exc:  # noqa: BLE001 - best-effort warm-up; never propagate.
        # Release the URL so a later navigation can retry after a transient failure.
        _SEEN_URLS.discard(key)
        log(f"[auto-index] skipped {url!r} — {exc}")
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
