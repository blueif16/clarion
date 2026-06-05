"""TimedRetriever — wraps any Retriever and measures wall-clock query latency.

This is the §8 "speculative retrieval" timing source. The GROUND node (K1)
calls ``retriever.query(…)``; if the retriever is a TimedRetriever the elapsed
milliseconds are available immediately after the call as ``last_query_ms``.

Usage in GROUND (or any caller that wants the live latency number)::

    retriever = TimedRetriever(some_retriever)
    facts = await retriever.query(goal)
    retrieval_ms = retriever.last_query_ms   # float ms, > 0 after first query

The §8 contract also requires that ``Fact.retrieved_at`` is stamped at the
moment of retrieval (not at construction). TimedRetriever writes the timestamp
on every returned Fact so consumers that read ``retrieved_at`` always see a
fresh value even when the underlying retriever leaves it at the pydantic
default (0.0).

This module is **pure**: stdlib only + the frozen contracts. No provider SDKs.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from clarion.contracts.ports import Retriever
from clarion.contracts.state import Fact


class Timed:
    """A tiny reusable wall-clock stopwatch for the migration latency budget.

    The migration (``docs/clarion-architecture.md`` step 0) needs per-phase
    millisecond numbers — ``perceive_ms`` / ``decide_ms`` / ``stale_check_ms`` /
    ``turn_ms`` — read straight out of a log file on a REAL page. ``Timed`` is the
    one shared facility: a named context manager that records the elapsed ms of
    the most recent block as ``last_ms`` and (optionally) reports it via a sink.

    Pure stdlib + frozen contracts; no provider SDK, no logging framework — the
    sink is just a ``callable(name, ms)`` the caller supplies (``print`` in the
    worker so the number lands in ``/tmp/clarion-worker.log``).

    Usage::

        meter = Timed("perceive_ms", sink=lambda n, ms: print(f"[lat] {n}={ms:.1f}"))
        with meter:
            sm = await actuator.perceive()
        # meter.last_ms holds the elapsed ms (also reported via the sink)

    The same instance can be reused across calls; ``last_ms`` always reflects the
    most recent block (``None`` before the first).
    """

    def __init__(self, name: str, *, sink=None) -> None:
        self.name = name
        self._sink = sink
        self.last_ms: float | None = None
        self._t0: float | None = None

    def __enter__(self) -> "Timed":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        if self._t0 is not None:
            self.last_ms = (time.perf_counter() - self._t0) * 1000.0
            self._t0 = None
            if self._sink is not None:
                self._sink(self.name, self.last_ms)
        # Do not suppress exceptions.
        return None


@contextmanager
def timed(name: str, *, sink=None) -> Iterator[Timed]:
    """One-shot stopwatch: ``with timed("perceive_ms", sink=print) as t: ...`` then
    read ``t.last_ms``. A thin wrapper over ``Timed`` for callers that don't want
    to hold the instance (the worker reuses a ``Timed`` instead)."""
    m = Timed(name, sink=sink)
    with m:
        yield m


class TimedRetriever(Retriever):
    """A ``Retriever`` decorator that measures wall-clock query latency.

    Wraps any concrete ``Retriever`` implementation. After each ``query``
    call, ``last_query_ms`` holds the elapsed time in milliseconds as measured
    by ``time.perf_counter`` (monotonic, sub-millisecond resolution).

    The decorator also stamps ``Fact.retrieved_at`` on every returned Fact
    using ``time.time()`` (Unix epoch seconds) so the latency-meter UI and any
    downstream consumers always have a fresh timestamp regardless of what the
    wrapped retriever sets (execution §8).

    Args:
        inner: Any concrete ``Retriever``.
    """

    def __init__(self, inner: Retriever) -> None:
        self._inner = inner
        self._last_query_ms: float | None = None

    @property
    def last_query_ms(self) -> float | None:
        """Elapsed milliseconds of the most recent ``query`` call.

        ``None`` before the first query; a positive float thereafter.
        """
        return self._last_query_ms

    async def query(self, q: str, *, k: int = 5) -> list[Fact]:
        """Delegate to the inner retriever and record wall-clock latency.

        Uses ``time.perf_counter`` (monotonic) for the elapsed measurement and
        ``time.time()`` to stamp ``Fact.retrieved_at`` on every returned Fact
        (execution §8 — the latency meter sources both from this stamp).
        """
        t0 = time.perf_counter()
        facts = await self._inner.query(q, k=k)
        elapsed_s = time.perf_counter() - t0
        self._last_query_ms = elapsed_s * 1000.0

        # Stamp retrieved_at on all returned facts (the §8 requirement).
        # We use model_copy so we never mutate the inner retriever's objects.
        now = time.time()
        stamped: list[Fact] = []
        for fact in facts:
            if fact.retrieved_at == 0.0:
                # Only overwrite the default — if the inner retriever set a
                # meaningful timestamp, respect it.
                stamped.append(fact.model_copy(update={"retrieved_at": now}))
            else:
                stamped.append(fact)
        return stamped


__all__ = ["TimedRetriever", "Timed", "timed"]
