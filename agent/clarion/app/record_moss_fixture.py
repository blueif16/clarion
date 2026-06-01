"""I2 — record ONE real Moss KB query into the app fixture for OFFLINE demo mode.

The hero run's KB-retrieval beat (late-fee / autopay policy) goes through the
LIVE Moss stack by default. But ``CLARION_DEMO_MODE=1`` must work OFFLINE — no
network, no Gemini embed RPC, no Moss runtime — so this capture pass runs the
REAL ``MossRetriever`` against the LIVE ``clarion-kb`` index ONCE and serializes
the result (the grounded Facts + their Moss ``source_node_id`` + the in-memory
``last_runtime_ms``) into ``app/fixtures/hero_moss_kb.json``.

``app.runtime`` then replays that recorded result via ``CachedRetriever`` in demo
mode, so the offline hero run still shows the (recorded, real) Moss number and the
grounded KB fact — never a fabricated one.

This is HONEST insurance, exactly like ``record_fixture.py`` (perception): we
cache what Moss *returned* (the real passages + the real sub-ms in-memory number),
never invent it. The cached number is labelled "[cached]" in the harness so the
demo never claims a live number it didn't measure this run.

Run (live Moss reachable for this pass only — reuses the prebuilt clarion-kb):
    cd agent && .venv/bin/python -m clarion.app.record_moss_fixture
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

from dotenv import load_dotenv

_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_AGENT_ROOT, ".env"))

from clarion.app.kb_beat import KB_QUERY  # noqa: E402

MOSS_FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "hero_moss_kb.json"
)


async def record(query: str = KB_QUERY) -> dict:
    """Run the live ``MossRetriever`` over ``clarion-kb`` once and capture the
    result + the Moss in-memory ``last_runtime_ms`` (the panel number)."""
    from clarion.instrument import TimedRetriever
    from clarion.retrieval import GeminiEmbedder, MossClient, MossRetriever

    index = os.environ.get("MOSS_INDEX", "clarion-kb")
    moss = MossClient()
    emb = GeminiEmbedder()
    retriever = MossRetriever(moss=moss, embedder=emb, index=index)
    timed = TimedRetriever(retriever)

    facts = await timed.query(query, k=3)
    if not facts:
        raise RuntimeError(
            f"live Moss returned no facts for {query!r} on index {index!r}; "
            f"is clarion-kb built? Run an ingest first."
        )

    return {
        "recorded_at": time.time(),
        "index": index,
        "query": query,
        # The Moss in-memory vector-search time — the panel number per R-Moss's
        # guidance (NOT the wall-clock that includes the Gemini embed RPC).
        "last_runtime_ms": retriever.last_runtime_ms,
        # The wall-clock (embed + search) kept for honesty / context.
        "last_query_ms": round(timed.last_query_ms, 2) if timed.last_query_ms else None,
        "facts": [
            {
                "value": f.value,
                "source_node_id": f.source_node_id,
                "polarity": f.polarity,
                "verified": f.verified,
            }
            for f in facts
        ],
    }


async def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else KB_QUERY
    print(f"RECORD MOSS PASS — live MossRetriever over clarion-kb, query={query!r}",
          flush=True)
    rec = await record(query)

    os.makedirs(os.path.dirname(MOSS_FIXTURE_PATH), exist_ok=True)
    with open(MOSS_FIXTURE_PATH, "w") as f:
        json.dump(rec, f, indent=2)

    print(f"\nMOSS FIXTURE WRITTEN → {MOSS_FIXTURE_PATH}", flush=True)
    print(f"  index={rec['index']}  Moss in-memory last_runtime_ms={rec['last_runtime_ms']} "
          f"(wall-clock embed+search {rec['last_query_ms']} ms)", flush=True)
    for fct in rec["facts"]:
        head = fct["value"].splitlines()[0][:64]
        print(f"  - source={fct['source_node_id']}  {head!r}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
