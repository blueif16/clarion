"""Knowledge-layer freshness — the verify-on-use primitive + the supersede-on-
recrawl mechanism (no network; runs in the deterministic gate).

Covers:
  - ``structure_freshness``: the fingerprint is order/whitespace-independent, moves
    on a real structural change, and is BLIND to values (the epistemic firewall).
  - ``compare``: the unseen/fresh/stale verdict.
  - the ingest ``id_basis`` + ``passage_metadata`` extension: a STABLE per-URL id so
    re-ingesting a CHANGED page upserts in place (supersede, no orphaned chunk), and
    distinct URLs get distinct refs — proven against a fake Moss (no creds).
"""

from __future__ import annotations

from clarion.app.structure_freshness import (
    compare,
    page_fingerprint,
    structure_signature,
)
from clarion.contracts.state import Fact, PageReadout


def _readout(title, headings, affordances, summary="", url="https://x/"):
    def f(v):
        return Fact(value=v, source_node_id="n")

    return PageReadout(
        title=title,
        url=url,
        headings=[f(h) for h in headings],
        affordances=[f(a) for a in affordances],
        summary=summary,
    )


# --- the pure fingerprint -------------------------------------------------


def test_signature_is_order_and_whitespace_independent():
    a = structure_signature("Home", ["Pay bill", "Account"], ["Pay", "Settings"])
    b = structure_signature("home", ["  account ", "PAY BILL"], ["settings", "pay"])
    assert a == b


def test_signature_moves_when_a_control_changes():
    base = structure_signature("Home", ["Pay bill"], ["Pay", "Settings"])
    added = structure_signature("Home", ["Pay bill"], ["Pay", "Settings", "Transfer"])
    removed = structure_signature("Home", ["Pay bill"], ["Pay"])
    renamed = structure_signature("Home", ["Pay bill"], ["Pay now", "Settings"])
    assert len({base, added, removed, renamed}) == 4


def test_fingerprint_is_blind_to_values():
    # The structural fingerprint must NOT move when only a VALUE differs (the
    # epistemic firewall: values never enter the cache, so never drive its refresh).
    p1 = _readout("Bill", ["Amount due"], ["Pay"], summary="Amount due is $84.32")
    p2 = _readout("Bill", ["Amount due"], ["Pay"], summary="Amount due is $9,610.00")
    assert page_fingerprint(p1) == page_fingerprint(p2)


def test_fingerprint_moves_on_a_new_affordance():
    p1 = _readout("Bill", ["Amount due"], ["Pay"])
    p2 = _readout("Bill", ["Amount due"], ["Pay", "Autopay"])
    assert page_fingerprint(p1) != page_fingerprint(p2)


def test_compare_verdicts():
    fp = "abc123def456"
    assert compare(None, fp) == "unseen"
    assert compare("", fp) == "unseen"
    assert compare(fp, fp) == "fresh"
    assert compare("0000deadbeef", fp) == "stale"


# --- the ingest supersede mechanism (fake Moss, no network) ---------------


class _FakeMoss:
    """Records upserts by id so we can assert stable-id supersede semantics."""

    def __init__(self):
        self.docs: dict = {}
        self._created = False

    async def list_indexes(self):
        if not self._created:
            return []
        return [type("I", (), {"name": "clarion-site-structure"})()]

    async def create_index(self, name, docs, model_id=None):
        self._created = True
        for d in docs:
            self.docs[d.id] = d
        return type("R", (), {"job_id": None})()

    async def add_docs(self, name, docs):
        for d in docs:
            self.docs[d.id] = d  # upsert by id
        return type("R", (), {"job_id": None})()


def _ingest(fake, monkeypatch):
    # Force the built-in embed path so ingest needs no Gemini key / embedding RPC.
    monkeypatch.setenv("MOSS_EMBED_MODEL", "moss-minilm")
    from clarion.retrieval import GeminiMossIngest

    return GeminiMossIngest(moss=fake, index="clarion-site-structure")


async def test_stable_id_supersedes_a_changed_page(monkeypatch):
    fake = _FakeMoss()
    ing = _ingest(fake, monkeypatch)
    url = "https://usa.gov/benefits"

    doc_v1 = f"# Benefits\nURL: {url}\nActions available: Apply; Check status"
    p1 = await ing.ingest(
        doc_v1,
        extra_metadata={"site": "usa.gov", "category": "structure"},
        passage_metadata=[{"url": url, "fingerprint": "fp1", "indexed_at": "1"}],
        id_basis=[url],
    )
    # The page CHANGES (a control is added) — same URL → SAME ref → upsert in place.
    doc_v2 = f"# Benefits\nURL: {url}\nActions available: Apply; Check status; Cancel"
    p2 = await ing.ingest(
        doc_v2,
        extra_metadata={"site": "usa.gov", "category": "structure"},
        passage_metadata=[{"url": url, "fingerprint": "fp2", "indexed_at": "2"}],
        id_basis=[url],
    )

    assert p1[0].ref == p2[0].ref  # stable id → no orphaned stale chunk
    assert len(fake.docs) == 1  # superseded in place, not accumulated
    stored = fake.docs[p2[0].ref]
    assert stored.text == doc_v2
    assert stored.metadata["fingerprint"] == "fp2"
    assert stored.metadata["url"] == url


async def test_distinct_urls_get_distinct_refs(monkeypatch):
    fake = _FakeMoss()
    ing = _ingest(fake, monkeypatch)
    a = await ing.ingest(
        "# A\nURL: https://s/a",
        extra_metadata={"site": "s"},
        passage_metadata=[{"url": "https://s/a"}],
        id_basis=["https://s/a"],
    )
    b = await ing.ingest(
        "# B\nURL: https://s/b",
        extra_metadata={"site": "s"},
        passage_metadata=[{"url": "https://s/b"}],
        id_basis=["https://s/b"],
    )
    assert a[0].ref != b[0].ref
    assert len(fake.docs) == 2


async def test_falls_back_to_content_ids_when_unaligned(monkeypatch):
    # A length mismatch (1 chunk, 2 id_basis) must NOT crash and must fall back to
    # the prior content-derived id behaviour (fail-safe).
    fake = _FakeMoss()
    ing = _ingest(fake, monkeypatch)
    out = await ing.ingest(
        "# Only one chunk\nURL: https://s/x",
        extra_metadata={"site": "s"},
        id_basis=["https://s/x", "https://s/extra"],  # misaligned on purpose
    )
    assert len(out) == 1 and out[0].ref  # produced a ref, did not raise
