"""SITE-INDEX — a read-only same-origin STRUCTURE crawl into Moss (knowledge-layer
item #4, the "how this site works" map).

This is the invariant-safe shape of "pre-index the page so later retrieval is
fast." It indexes the **stable structure** of a site — headings + the controls a
page affords ("Pay bill", "Account settings", the form fields) — NOT the volatile
VALUES on it (a balance, an amount due, a confirmation #). The split is enforced
by construction, not by a filter:

  - It harvests via the actuator's ``describe_page`` → ``summarize_ax_tree``, which
    keeps ONLY headings + interactive affordances and drops every StaticText/value
    node. So a "$84.32" can't enter the index. (The live VALUE path stays
    ``PageRetriever``/``read_facts``, read fresh from the AXTree at task time —
    never cached, so the kernel never speaks a stale number. foundation §1.)
  - It walks links read-only: ``collect_links`` enumerates same-origin anchors and
    the crawl only ever ``navigate``s (GET) to them — it never clicks, submits, or
    fills, so the crawl cannot take a consequential action (agentic invariant). A
    small denylist also skips logout/delete/cancel-style URLs.

The crawl runs in its OWN ``PlaywrightActuator`` browser (NOT the user's live tab),
bounded by ``max_pages`` / ``max_depth``. Each page becomes one citable ``#`` chunk
(its URL carried in-band for provenance); the whole set is embedded in one batch
and built into a per-site Moss index (``clarion-site-<host>``) via the existing
``GeminiMossIngest`` — zero new provider wiring.

Run:
  .venv/bin/python -m clarion.app.site_indexer https://www.usa.gov/
  .venv/bin/python -m clarion.app.site_indexer https://www.usa.gov/ "how do I file a complaint"
  CLARION_CRAWL_MAX_PAGES=8 CLARION_CRAWL_MAX_DEPTH=1 CRAWL_HEADLESS=0 .venv/bin/python -m clarion.app.site_indexer <url>

Tip: for a bigger crawl, set ``MOSS_EMBED_MODEL=moss-minilm`` so Moss embeds each
chunk locally (no per-chunk Gemini RPC) — the ingest path already honours it.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_AGENT_ROOT, ".env"))

from clarion.contracts.state import Action, Fact, PageReadout  # noqa: E402
from clarion.app.structure_freshness import page_fingerprint  # noqa: E402

# URL substrings that signal a state-changing / destructive target. The crawl is
# already GET-only, but we refuse to even fetch these (a GET /logout still mutates
# session state on plenty of sites). Lower-cased substring match on the full URL.
_DENY = (
    "logout", "log-out", "signout", "sign-out", "/signin", "/login",
    "delete", "/remove", "/destroy", "cancel", "unsubscribe", "/api/",
)


def _same_origin(url: str, origin: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and f"{p.scheme}://{p.netloc}" == origin


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


# ONE category index for ALL sites' structure (replaces one-index-per-site). Sites
# are separated by the ``site`` metadata + a query-time filter, NOT by index — the
# recommended Moss data model (docs/research/moss-index-design.md): far fewer indexes
# (stays under the plan cap), trivial onboarding (just tag metadata), one shared
# embedding model. Kept distinct from the policy KB (``clarion-kb``).
STRUCTURE_INDEX = "clarion-site-structure"


def host_of(url: str) -> str:
    """The host — the ``site`` partition key for the shared structure index
    (e.g. ``www.usa.gov``). Lower-cased; ``""`` for a blank/garbage URL."""
    return (urlparse(url).netloc or "").lower()


def index_name_for(url: str) -> str:
    """DEPRECATED (pre-migration): the per-site index name, e.g.
    ``clarion-site-www-usa-gov``. Retained for back-compat; the live path now uses
    the single ``STRUCTURE_INDEX`` + a ``site`` metadata filter instead."""
    host = urlparse(url).netloc.lower() or "site"
    slug = re.sub(r"[^a-z0-9]+", "-", host).strip("-")
    return f"clarion-site-{slug}"


def _allowed(url: str, origin: str) -> bool:
    return _same_origin(url, origin) and not any(d in url.lower() for d in _DENY)


def page_block(readout: PageReadout) -> str:
    """One page → one citable ``#`` chunk: STRUCTURE only (headings + the controls
    it affords), with the URL in-band for provenance. No StaticText values can
    appear — ``describe_page`` already dropped them."""
    title = readout.title.strip() or urlparse(readout.url).path or readout.url
    headings = "; ".join(h.value.strip() for h in readout.headings if h.value.strip())
    actions = "; ".join(a.value.strip() for a in readout.affordances if a.value.strip())
    lines = [f"# {title}", f"URL: {readout.url}"]
    if headings:
        lines.append(f"Headings: {headings}")
    if actions:
        lines.append(f"Actions available: {actions}")
    # Strip any leading '#' so a heading-like summary line can't open a second
    # markdown chunk (the ingest splits a combined doc on '#'-prefixed lines, so
    # each page block must stay exactly ONE chunk for the per-page id/fingerprint).
    summary = re.sub(r"(?m)^\s*#+\s*", "", readout.summary.strip())
    if summary:
        lines.append(summary)
    return "\n".join(lines)


@dataclass
class CrawlResult:
    index: str
    origin: str
    pages: list[str] = field(default_factory=list)   # the URLs indexed, in order
    chunks: int = 0
    skipped: list[str] = field(default_factory=list)  # denied/off-origin URLs seen


async def crawl_and_index(
    start_url: str,
    *,
    max_pages: int = 6,
    max_depth: int = 1,
    index: Optional[str] = None,
    headless: bool = True,
    log: Callable[[str], None] = print,
) -> CrawlResult:
    """Read-only same-origin BFS from ``start_url``; index each page's STRUCTURE
    into the shared category index (``STRUCTURE_INDEX``), tagged with the ``site``
    host so queries scope by metadata filter. Returns what was indexed (+ skipped)."""
    from clarion.actuator.actuator import PlaywrightActuator
    from clarion.retrieval import GeminiMossIngest

    origin = _origin(start_url)
    index = index or STRUCTURE_INDEX
    host = host_of(start_url)
    result = CrawlResult(index=index, origin=origin)

    actuator = await PlaywrightActuator.create(start_url, headless=headless)
    blocks: list[str] = []
    fingerprints: list[str] = []  # one structural fingerprint per indexed page
    try:
        # BFS frontier of (url, depth); the start page is already loaded by create.
        queue: list[tuple[str, int]] = [(start_url, 0)]
        seen: set[str] = {start_url}
        first = True
        while queue and len(result.pages) < max_pages:
            url, depth = queue.pop(0)
            try:
                if not first:
                    await actuator.act(Action(kind="navigate", value=url))
                first = False
                readout = await actuator.describe_page()
            except Exception as exc:  # noqa: BLE001 - a bad page never kills the crawl
                log(f"  [skip] {url} — {exc}")
                result.skipped.append(url)
                continue

            blocks.append(page_block(readout))
            result.pages.append(url)
            fingerprints.append(page_fingerprint(readout))
            log(f"  [page {len(result.pages)}/{max_pages}] {url} "
                f"({len(readout.headings)} headings, {len(readout.affordances)} actions)")

            if depth < max_depth:
                for link in await actuator.collect_links():
                    if link in seen:
                        continue
                    seen.add(link)
                    if _allowed(link, origin):
                        queue.append((link, depth + 1))
                    else:
                        result.skipped.append(link)
    finally:
        await actuator.close()

    if not blocks:
        log("  [done] nothing to index (no readable pages).")
        return result

    # One combined doc → one chunk per page (split on the `#` headings) → ONE
    # batched embed + ONE upsert. Every chunk is tagged with the `site` host so the
    # single category index holds all sites, scoped at query time by a metadata
    # filter (docs/research/moss-index-design.md). Reuses the live Ingest adapter.
    doc = "\n\n".join(blocks)
    # Per-page stamps for VERIFY-ON-USE: a structural FINGERPRINT (changes only on a
    # real structure change — a control added/removed/renamed — never on a value) +
    # the indexing time, keyed to a STABLE per-URL doc id so a re-crawl SUPERSEDES a
    # changed page in place instead of orphaning a stale chunk. This is the
    # knowledge-layer freshness best practice (CLAUDE.md / docs/clarion-status.md):
    # the cache stays advisory, refreshed by re-perception, never expired on a TTL.
    indexed_at = str(int(time.time()))
    passage_metadata = [
        {"url": u, "fingerprint": fp, "indexed_at": indexed_at}
        for u, fp in zip(result.pages, fingerprints)
    ]
    ingest = GeminiMossIngest(index=index)
    passages = await ingest.ingest(
        doc,
        extra_metadata={"site": host, "category": "structure"},
        passage_metadata=passage_metadata,
        id_basis=list(result.pages),
    )
    result.chunks = len(passages)
    log(f"  [done] indexed {len(result.pages)} pages → {result.chunks} chunks "
        f"into Moss index {index!r} (site={host!r}); stable per-URL ids + "
        f"fingerprints → a re-crawl supersedes a changed page in place.")
    return result


class SiteKnowledge:
    """Query-time consult of the shared STRUCTURE index (the crawler's output) —
    the read side of knowledge-layer item #4(a), injected into the planner.

    Given the live page URL, it queries the ONE ``STRUCTURE_INDEX`` and scopes to the
    current site with a metadata filter (``site $eq <host>``), returning grounded
    STRUCTURE facts (other pages + their affordances) to inform PLANNING ("which page
    hosts this flow"). It is **best-effort and fail-open**: any miss — index not built
    yet, no creds, network error — yields ``[]`` so the planner silently degrades to
    page-only, never erroring. It NEVER feeds the epistemic GROUND (these are
    cross-page structure facts, not live current-page values), so the
    no-fact-without-a-live-source invariant is untouched.

    One ``MossRetriever`` over the shared index is memoised (lazy ``load_index`` on
    first query); per-site separation is the metadata filter, not the index
    (docs/research/moss-index-design.md).
    """

    def __init__(self, *, k: int = 4) -> None:
        self._k = k
        self._retriever = None  # one MossRetriever over the shared STRUCTURE_INDEX

    async def context_facts(self, url: str, goal: str) -> list[Fact]:
        host = host_of(url)
        if not host:
            return []
        try:
            if self._retriever is None:
                from clarion.retrieval import MossRetriever

                self._retriever = MossRetriever(index=STRUCTURE_INDEX)
            return await self._retriever.query(
                goal,
                k=self._k,
                filter={"field": "site", "condition": {"$eq": host}},
            )
        except Exception:  # noqa: BLE001 - consult is optional; degrade to page-only
            return []


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        print("usage: python -m clarion.app.site_indexer <start_url> [smoke_query]")
        return 2
    start_url = sys.argv[1]
    query = sys.argv[2] if len(sys.argv) > 2 else "what can I do on this site"
    max_pages = int(os.environ.get("CLARION_CRAWL_MAX_PAGES", "6"))
    max_depth = int(os.environ.get("CLARION_CRAWL_MAX_DEPTH", "1"))
    index = os.environ.get("CLARION_SITE_INDEX") or None
    headless = os.environ.get("CRAWL_HEADLESS", "1") != "0"

    print(f"== crawl {start_url}  (max_pages={max_pages}, max_depth={max_depth}) ==")
    res = await crawl_and_index(
        start_url, max_pages=max_pages, max_depth=max_depth,
        index=index, headless=headless,
    )
    if not res.chunks:
        return 1

    # The real proof (status doc): load the index back and query it — round-trip
    # retrieval over what we just crawled, scoped to THIS site via a metadata filter
    # (the same path SiteKnowledge uses).
    from clarion.retrieval import MossRetriever

    host = host_of(start_url)
    print(f"\n== smoke query: {query!r} (index {res.index!r}, site={host!r}) ==")
    retriever = MossRetriever(index=res.index)
    hits = await retriever.query(
        query, k=3, filter={"field": "site", "condition": {"$eq": host}}
    )
    if not hits:
        print("  (no hits — index may still be building, or the query missed)")
    for i, h in enumerate(hits, 1):
        snippet = h.value.replace("\n", " ⏎ ")[:160]
        print(f"  {i}. [{h.source_node_id}] {snippet}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
