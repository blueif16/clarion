"""clarion.instrument — Wave-1b latency instrumentation (execution §8, L1).

Owned entirely by L1; never imported by kernel/graph.py or contracts/.

Public surface:
  - ``TimedRetriever``  (timed.py)   wraps any Retriever, measures wall-clock ms
  - ``COLD_RAG_BASELINE_MS``         the greyed cold-RAG baseline constant
  - ``SlowFakeRetriever``            (baseline.py) simulates cold-RAG timing
  - ``to_panel_state``               (publisher.py) maps ClarionState → PanelState
"""

from clarion.instrument.baseline import COLD_RAG_BASELINE_MS, SlowFakeRetriever
from clarion.instrument.publisher import to_panel_state
from clarion.instrument.timed import TimedRetriever

__all__ = [
    "TimedRetriever",
    "COLD_RAG_BASELINE_MS",
    "SlowFakeRetriever",
    "to_panel_state",
]
