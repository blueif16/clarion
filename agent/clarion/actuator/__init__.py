"""A1 — the a11y-tree Actuator package (execution §4).

The real ``Actuator`` port impl: parallel CDP triple-fetch → merge →
PaintOrderRemover + bbox-containment filter → numbered ``SelectorMap``;
native-setter fill / paint-order-honest click / navigate / read; page diff.
``perceive_vision`` is the named, deferred fallback (§4.2)."""

from clarion.actuator.actuator import PaintOrderRemover, PlaywrightActuator
from clarion.actuator.extension_actuator import ExtensionActuator
from clarion.actuator.relay import CdpRelay

__all__ = [
    "PlaywrightActuator",
    "PaintOrderRemover",
    "ExtensionActuator",
    "CdpRelay",
]
