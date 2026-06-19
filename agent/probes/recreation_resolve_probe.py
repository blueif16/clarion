"""Wire-and-test prep: (A) what the ORIENT readout exposes for the grid (can the
communicator summarize the axes?), and (B) does the semantic ContextRanker surface
the EXACT date×site cell among 515 near-identical buttons (the voice→node resolve)?

No LLM — this isolates the readout + the ranker recall. Run:
  cd agent && .venv/bin/python -m probes.recreation_resolve_probe
"""
from __future__ import annotations

import asyncio
import collections

from clarion.actuator.actuator import PlaywrightActuator
from clarion.retrieval.context_ranker import EmbeddingContextRanker, LocalMiniLMEmbedder

CAMP = "https://www.recreation.gov/camping/campgrounds/233359"

# (intent, the substring that identifies the ONE correct cell)
RESOLVE_CASES = [
    ("Book Site Boat A on June 12", "jun 12, 2026 - site boat a"),
    ("reserve Boat B for June 9", "jun 9, 2026 - site boat b"),
    ("the 16th, Boat A", "jun 16, 2026 - site boat a"),
]


def _name_role(node):
    n = node.get("name") or {}
    r = node.get("role") or {}
    return (r.get("value") if isinstance(r, dict) else r,
            n.get("value") if isinstance(n, dict) else n)


async def main() -> None:
    act = await PlaywrightActuator.create(CAMP, headless=True)
    try:
        await act._page.wait_for_timeout(4000)

        # ---- (A) the ORIENT readout the communicator would speak --------------
        readout = await act.describe_page()
        print("===== (A) describe_page readout =====")
        print(f"title: {readout.title!r}")
        print(f"headings ({len(readout.headings)}): "
              f"{[h.value for h in readout.headings][:10]}")
        print(f"affordances: {len(readout.affordances)}")
        print(f"summary[:400]: {readout.summary[:400]!r}")

        # The GRID AXES, straight from the AX roles (structural, not lexical):
        raw = await act._cdp.send("Accessibility.getFullAXTree")
        cols, rows = [], []
        for nd in raw.get("nodes", []):
            role, name = _name_role(nd)
            if not name:
                continue
            if role == "columnheader":
                cols.append(name)
            elif role == "rowheader":
                rows.append(name)
        print("\n----- grid axes from columnheader/rowheader roles -----")
        print(f"date columns ({len(cols)}): {cols}")
        print(f"site rows ({len(rows)}): {rows[:12]}")

        # ---- (B) semantic resolve: ranker recall on the full grid -------------
        page = await act.perceive()
        print(f"\n===== (B) ContextRanker resolve over {len(page.nodes)} nodes =====")
        ranker = EmbeddingContextRanker(LocalMiniLMEmbedder())
        for intent, needle in RESOLVE_CASES:
            for k in (10, 25):
                sliced = await ranker.rank(intent, page, [], k)
                hit_rank = None
                # rank within the sliced set, by re-scoring order is lost; just check membership + position in full ranking
                names = [(i, sliced.nodes[i].name) for i in sorted(sliced.nodes)]
                present = [i for i, nm in names if needle in nm.lower()]
                print(f"  intent={intent!r:42} k={k:2}  "
                      f"correct cell in top-{k}: {bool(present)}  "
                      f"(indices={present})")
            # show what the top-10 actually contains (first 6) for intuition
            sliced = await ranker.rank(intent, page, [], 10)
            sample = [sliced.nodes[i].name[:48] for i in sorted(sliced.nodes)][:6]
            print(f"      top-10 sample: {sample}")
    finally:
        await act.close()


if __name__ == "__main__":
    asyncio.run(main())
