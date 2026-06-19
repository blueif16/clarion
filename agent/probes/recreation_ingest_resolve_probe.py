"""Feasibility probe: warm-up auto-ingest of recreation.gov, then resolve a
natural-language goal ("reserve Point Reyes") to the correct campground URL.

Tests the REAL path:
  collect_links (anchor BFS reach)  ·  crawl_and_index → clarion-site-structure
  ·  SiteKnowledge/MossRetriever goal→URL resolution  ·  cold-miss fallback.

Run:  cd agent && .venv/bin/python -m probes.recreation_ingest_resolve_probe
Needs Moss creds (.env). Writes the campground page into the live shared
structure index (idempotent: stable per-URL id supersedes in place).
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import quote

os.environ.setdefault("CLARION_CRAWL_SETTLE_MS", "3000")  # SPA settle for the crawl

HOME = "https://www.recreation.gov/"
CAMP = "https://www.recreation.gov/camping/campgrounds/233359"
SEARCH = "https://www.recreation.gov/search?q=" + quote("Point Reyes")
GOAL = "I want to make a reservation to Point Reyes"
NEG = "I want to reserve a campsite at Yosemite Valley"


def _hr(title: str) -> None:
    print(f"\n{'=' * 4} {title} {'=' * 4}")


async def _links_on(url: str, settle_ms: int = 4000):
    from clarion.actuator.actuator import PlaywrightActuator

    act = await PlaywrightActuator.create(url, headless=True)
    try:
        await act._page.wait_for_load_state("networkidle", timeout=12_000)
    except Exception:
        pass
    await act._page.wait_for_timeout(settle_ms)
    title = await act._page.title()
    links = await act.collect_links()
    await act.close()
    return title, links


def _classify(links: list[str]):
    camp = [l for l in links if "/campground" in l.lower()]
    pr = [l for l in links if any(t in l.lower() for t in ("233359", "point", "reyes"))]
    return camp, pr


async def main() -> None:
    from clarion.app.site_indexer import (
        crawl_and_index,
        host_of,
        STRUCTURE_INDEX,
    )
    from clarion.retrieval import MossRetriever

    host = host_of(CAMP)

    # -- A. Can an anchor-crawl from the HOMEPAGE even reach Point Reyes? -----
    _hr("A. Homepage anchor reachability")
    title, links = await _links_on(HOME)
    camp, pr = _classify(links)
    print(f"title={title!r}")
    print(f"total same-origin anchors: {len(links)}")
    print(f"  → links containing '/campground': {len(camp)}")
    print(f"  → links mentioning point/reyes/233359: {len(pr)}")
    for l in camp[:8]:
        print(f"     · {l}")
    for l in pr[:8]:
        print(f"     ! {l}")

    # -- B. Does the SEARCH RESULTS page expose the campground anchor? --------
    _hr("B. Search-results anchor reachability  (q='Point Reyes')")
    title2, links2 = await _links_on(SEARCH)
    camp2, pr2 = _classify(links2)
    print(f"title={title2!r}")
    print(f"total same-origin anchors: {len(links2)}")
    print(f"  → links containing '/campground': {len(camp2)}")
    print(f"  → links mentioning point/reyes/233359: {len(pr2)}")
    for l in (camp2 + pr2)[:12]:
        print(f"     · {l}")

    # -- C0. COLD query (before ingest): goal → URL on the live index --------
    _hr("C0. COLD resolve (before ingest)")
    retr = MossRetriever(index=STRUCTURE_INDEX)
    try:
        cold = await retr.query(
            GOAL, k=3, filter={"field": "site", "condition": {"$eq": host}}
        )
    except Exception as e:
        cold = []
        print(f"[warn] cold query: {e}")
    print(f"goal={GOAL!r}  site={host!r}")
    print(f"hits: {len(cold)}  →  "
          f"{'MISS → fallback: suggest search' if not cold else 'hit(s) present'}")
    for i, h in enumerate(cold, 1):
        print(f"   {i}. [{h.source_node_id}] {h.value[:120].replace(chr(10), ' / ')}")

    # -- C1. INGEST the campground page (depth 0 = just this page) -----------
    _hr("C1. Ingest the Point Reyes campground page → clarion-site-structure")
    res = await crawl_and_index(CAMP, max_pages=1, max_depth=0, headless=True)
    print(f"indexed pages: {res.pages}")
    print(f"chunks: {res.chunks}  index={res.index!r}")

    # -- C2. WARM query (after ingest): does the goal resolve to the URL? ----
    _hr("C2. WARM resolve (after ingest)")
    retr2 = MossRetriever(index=STRUCTURE_INDEX)
    warm = await retr2.query(
        GOAL, k=3, filter={"field": "site", "condition": {"$eq": host}}
    )
    print(f"goal={GOAL!r}")
    print(f"hits: {len(warm)}")
    for i, h in enumerate(warm, 1):
        v = h.value.replace(chr(10), " / ")
        url_line = next((p for p in h.value.splitlines() if p.startswith("URL:")), "?")
        print(f"   {i}. [{h.source_node_id}] {url_line}")
        print(f"       {v[:150]}")
    top_is_camp = bool(warm) and "233359" in warm[0].value
    print(f"\n  → TOP HIT IS THE POINT REYES PAGE: {top_is_camp}  "
          f"({'SUCCESS → redirect here' if top_is_camp else 'no clean resolve'})")

    # -- C3. NEGATIVE control: a goal NOT in the index → should not resolve --
    _hr("C3. NEGATIVE control (goal not ingested)")
    neg = await retr2.query(
        NEG, k=3, filter={"field": "site", "condition": {"$eq": host}}
    )
    print(f"goal={NEG!r}")
    print(f"hits: {len(neg)}")
    for i, h in enumerate(neg, 1):
        url_line = next((p for p in h.value.splitlines() if p.startswith("URL:")), "?")
        print(f"   {i}. [{h.source_node_id}] {url_line}")
    neg_wrong = bool(neg) and "233359" in neg[0].value
    print(f"  → returns Point Reyes for a Yosemite goal? {neg_wrong}  "
          f"(want False; a weak/empty match = fallback: suggest exact search)")


if __name__ == "__main__":
    asyncio.run(main())
