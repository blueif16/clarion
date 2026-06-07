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

import os

# Silence the benign `transformers` "PyTorch was not found" advisory at worker
# startup. The LiveKit turn-detector runs its EOU model on **onnxruntime**
# (installed), NOT torch — transformers only logs this because torch is absent, and
# nothing in Clarion needs torch. `setdefault` so an explicit env override still
# wins; this `clarion.app` package init runs BEFORE `voice_entry` imports the
# LiveKit plugins (which pull in transformers), so the advisory is suppressed.
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")

__all__ = ["runtime", "voice_entry", "hero_harness"]
