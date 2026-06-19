"""Multi-doc calibration: index several KNOWN, distinct campgrounds, then test
whether (a) a goal ranks its OWN campground #1, and (b) any score/margin signal
rejects a goal whose target is NOT indexed.

Run:  cd agent && .venv/bin/python -m probes.recreation_multidoc_calib
Writes a handful of campground pages into clarion-site-structure (idempotent).
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import quote

os.environ.setdefault("CLARION_CRAWL_SETTLE_MS", "3000")

from clarion.actuator.actuator import PlaywrightActuator
from clarion.app.site_indexer import STRUCTURE_INDEX, crawl_and_index, host_of
from clarion.retrieval import MossRetriever

HOST = "www.recreation.gov"
SEARCHES = ["Yosemite", "Yellowstone", "Grand Canyon", "Acadia"]

GOALS = [
    ("Point Reyes", "I want to make a reservation to Point Reyes"),
    ("Yosemite", "reserve a campsite in Yosemite"),
    ("Yellowstone", "book camping in Yellowstone"),
    ("Grand Canyon", "camping at the Grand Canyon"),
    ("ABSENT: Zion", "reserve a campsite at Zion National Park"),
    ("ABSENT: passport", "renew my passport"),
    ("ABSENT: bill", "pay my electricity bill"),
]


async def _first_campground_url(query: str) -> str | None:
    url = "https://www.recreation.gov/search?q=" + quote(query)
    act = await PlaywrightActuator.create(url, headless=True)
    try:
        await act._page.wait_for_timeout(4000)
        links = await act.collect_links()
    finally:
        await act.close()
    for l in links:
        if "/camping/campgrounds/" in l:
            return l.split("?")[0]
    return None


async def main() -> None:
    # 1. Ingest one distinct campground per search term (+ Point Reyes already in).
    print("== ingesting distinct campgrounds ==")
    for q in SEARCHES:
        u = await _first_campground_url(q)
        if not u:
            print(f"  [{q}] no campground link found")
            continue
        res = await crawl_and_index(u, max_pages=1, max_depth=0, headless=True)
        title = res.pages[0] if res.pages else "?"
        print(f"  [{q}] ingested {title}  (chunks={res.chunks})")

    # 2. Probe ranking + score for targeted and absent goals.
    r = MossRetriever(index=STRUCTURE_INDEX)
    await r._ensure_loaded()
    flt = {"field": "site", "condition": {"$eq": HOST}}
    for alpha in (1.0, 0.8):
        print(f"\n##### alpha={alpha} #####")
        for label, goal in GOALS:
            vec = None if r._embedder is None else (await r._embedder.embed([goal]))[0]
            res = await r._moss.search(
                r._index, goal, top_k=3, embedding=vec, alpha=alpha, filter=flt
            )
            hits = res.hits
            top = hits[0].score if hits else float("nan")
            second = hits[1].score if len(hits) > 1 else float("nan")
            margin = top - second
            print(f"\n  [{label}] goal={goal!r}")
            print(f"     top={top:.4f}  2nd={second:.4f}  margin={margin:.4f}")
            for h in hits:
                u = next((p[5:] for p in h.text.splitlines() if p.startswith("URL:")), "?")
                cgid = u.rstrip("/").split("/")[-1]
                print(f"        {h.score:.4f}  cg/{cgid}")


if __name__ == "__main__":
    asyncio.run(main())
