"""Clarion contracts — the six ports (execution §18.2).

These are the ONLY things the kernel sees (foundation §6 / execution §1). Every
real provider — LiveKit, Moss, Minimax, Playwright, Unsiloed, Atlas — sits behind
one of these ABCs in a Wave-1 adapter. This module is **pure**: ``abc`` + typing
+ our own pure ``state`` value objects. It imports ZERO provider SDKs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Protocol, runtime_checkable

from clarion.contracts.state import (
    Action,
    Fact,
    Observation,
    PageDiff,
    Passage,
    Profile,
    SelectorMap,
)


@runtime_checkable
class SpeechHandle(Protocol):
    """The handle returned by ``VoiceTransport.say`` (execution §18.2 footnote).

    Mirrors LiveKit's ``SpeechHandle`` surface that the seam relies on, but is a
    pure structural Protocol so the kernel never imports livekit. ``advance_task``
    awaits ``wait_if_not_interrupted([task])`` to overlap a graph step with the
    spoken sentence; on barge-in ``interrupted`` flips True and the task is
    cancelled (execution §5).
    """

    @property
    def interrupted(self) -> bool: ...

    async def wait_if_not_interrupted(self, tasks: list) -> None: ...


class VoiceTransport(ABC):
    """The voice plane (LiveKit). Owns the <800ms turn budget; the kernel never
    reimplements STT/turn/TTS (execution §1)."""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    def on_partial(self, cb: Callable[[str], None]) -> None:
        """Register the observer hook → fires speculative retrieval on partial
        STT, while the user is still talking (execution §5)."""
        ...

    @abstractmethod
    def on_final(self, cb: Callable[[str], None]) -> None: ...

    @abstractmethod
    def on_barge_in(self, cb: Callable[[], None]) -> None: ...

    @abstractmethod
    async def say(self, text: str, *, interruptible: bool = True) -> SpeechHandle:
        """Speak ``text``. ``interruptible=False`` wraps an atomic act so a stray
        "um" can't fracture an irreversible step (execution §5)."""
        ...

    @abstractmethod
    async def play_filler(self, key: str) -> None:
        """Play timed dead-air cover while a web action runs (execution §5)."""
        ...


class Retriever(ABC):
    """The grounding source (Moss). ``query`` returns ranked, source-referenced
    facts — the epistemic clause's supply (foundation §1, execution §2.2 GROUND)."""

    @abstractmethod
    async def query(self, q: str, *, k: int = 5) -> list[Fact]: ...


class Synthesizer(ABC):
    """Text → audio stream (Minimax). Stub-swappable (execution §15 R2)."""

    @abstractmethod
    async def synthesize(self, text: str) -> AsyncIterator[bytes]: ...


class Actuator(ABC):
    """The a11y-tree actuator (Playwright/CDP). The kernel sees only
    ``action -> observation`` (foundation §6, execution §4)."""

    @abstractmethod
    async def perceive(self) -> SelectorMap:
        """Build the merged, numbered AXTree for the current viewport (§4.1)."""
        ...

    @abstractmethod
    async def act(self, action: Action) -> Observation:
        """Execute click/fill/navigate/read, then re-perceive (§4.3)."""
        ...

    @abstractmethod
    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        """Page-diff to detect a silently-failed step (§4.3)."""
        ...


class Ingest(ABC):
    """Parse company docs → indexed passages (Unsiloed). ``doc`` is raw bytes
    (PDF/binary) or text (execution §6, §15 R3)."""

    @abstractmethod
    async def ingest(self, doc: bytes | str) -> list[Passage]: ...


class Memory(ABC):
    """Durable write-back of verified facts + profile read (Moss/Atlas)
    (execution §2.2 CONFIRM, §6)."""

    @abstractmethod
    async def write(self, fact: Fact) -> None: ...

    @abstractmethod
    async def read_profile(self, user_id: str) -> Profile: ...


__all__ = [
    "SpeechHandle",
    "VoiceTransport",
    "Retriever",
    "Synthesizer",
    "Actuator",
    "Ingest",
    "Memory",
]
