"""I1 — the integration capstone (execution §7 I1, §13 critical path).

OWNS ``clarion/app/``. Wires the FROZEN contracts + Wave-1 adapters + the ST1
stage graph + the instrument into the runnable live hero flow on the demo site:

  - ``runtime``      — HeroRuntime: stage graph + TimedRetriever(HeroRetriever) +
                       PlaywrightActuator + PanelPublisher + policy/mode.
  - ``voice_entry``  — the LiveKit worker entrypoint (V1 transport + the
                       non-blocking advance_task seam driving the stage graph).
  - ``hero_harness`` — drives the full hero run for verification (AUTH/RESCUE →
                       LOCATE → FILL → REVIEW → ⟨PAY⟩ hard-stop → CONFIRM).

Imports the kernel/stages/actuator/instrument read-only; never modifies them.
"""

__all__ = ["runtime", "voice_entry", "hero_harness"]
