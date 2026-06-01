"""In-memory fakes for every Clarion port (execution §15 C1: "every port gets an
ABC + an in-memory fake").

These are REAL, working, deterministic implementations — no network, no provider
SDKs. They let the kernel, stages, and the contract smoke test run end-to-end
before any real adapter exists, and they are the demo-mode fallback's substrate
(execution §9 / §17 scope-shed: "use fakes").
"""

from clarion.fakes.adapters import (
    FakeActuator,
    FakeIngest,
    FakeMemory,
    FakeRetriever,
    FakeSpeechHandle,
    FakeSynthesizer,
    FakeVoiceTransport,
)

__all__ = [
    "FakeVoiceTransport",
    "FakeSpeechHandle",
    "FakeRetriever",
    "FakeSynthesizer",
    "FakeActuator",
    "FakeIngest",
    "FakeMemory",
]
