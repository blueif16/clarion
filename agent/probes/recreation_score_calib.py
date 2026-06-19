"""Calibration: raw Moss similarity scores for in-index vs out-of-index goals,
scoped to recreation.gov structure. Picks a defensible min_score cutoff.

Run:  cd agent && .venv/bin/python -m probes.recreation_score_calib
Assumes the Point Reyes page (233359) is already in clarion-site-structure
(probes.recreation_ingest_resolve_probe ingests it).
"""
from __future__ import annotations

import asyncio

from clarion.app.site_indexer import STRUCTURE_INDEX, host_of
from clarion.retrieval import MossRetriever

HOST = host_of("https://www.recreation.gov/camping/campgrounds/233359")

# (label, goal, expect) — IN = should resolve to an indexed page, OUT = should not.
GOALS = [
    ("IN ", "I want to make a reservation to Point Reyes"),
    ("IN ", "Point Reyes National Seashore campground"),
    ("IN ", "reserve a boat-in campsite at Point Reyes"),
    ("OUT", "I want to reserve a campsite at Yosemite Valley"),
    ("OUT", "book a permit for Half Dome"),
    ("OUT", "pay my electricity bill"),
    ("OUT", "renew my passport"),
    ("?  ", "camping reservation"),
]


async def main() -> None:
    r = MossRetriever(index=STRUCTURE_INDEX)
    await r._ensure_loaded()
    flt = {"field": "site", "condition": {"$eq": HOST}}
    print(f"site={HOST!r}  index={STRUCTURE_INDEX!r}\n")
    for alpha in (1.0, 0.8, 0.5):
        print(f"\n##### alpha={alpha}  (1.0=pure semantic, 0.0=pure keyword) #####")
        for tag, goal in GOALS:
            vec = None if r._embedder is None else (await r._embedder.embed([goal]))[0]
            res = await r._moss.search(
                r._index, goal, top_k=1, embedding=vec, alpha=alpha, filter=flt
            )
            top = res.hits[0].score if res.hits else float("nan")
            print(f"  [{tag}] top={top:7.4f}  {goal!r}")


if __name__ == "__main__":
    asyncio.run(main())
