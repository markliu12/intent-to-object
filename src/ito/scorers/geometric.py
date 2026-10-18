"""Deterministic geometric scorers: the v0.1 default.

These exist because the two MVP actions are geometric quantities, not
classifiable patterns. Head tilt is an angle; a sustained blink is eye-aspect
ratio held below a personal baseline. Both are described by two numbers and a
dwell timer, both run at any frame rate, and both fail in ways a human can
diagnose by looking at the camera feed.

The stage-2 prototypical matcher is *not* a replacement for these. It is an
addition for users whose motion is idiosyncratic enough that no threshold
separates their intent from their rest, and it should be benchmarked against
these numbers rather than assumed to beat them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Type

from ..geometry import ramp
from ..types import Features


@dataclass
class TiltScorer:
    """Head roll beyond a personal resting band.

    ``start_deg`` is where the score leaves zero (so resting sway does not
    trigger) and ``full_deg`` is where it saturates at 1.0. Both are measured
    from the calibrated neutral posture, not from absolute vertical.
    """

    name: str = "tilt"
    start_deg: float = 5.0
    full_deg: float = 13.0
    direction: str = "any"  # any | left | right
    neutral_deg: float = 0.0

    def __post_init__(self) -> None:
        if self.direction not in ("any", "left", "right"):
            raise ValueError("direction must be any, left or right")
        if self.full_deg <= self.start_deg:
            raise ValueError("full_deg must exceed start_deg")

    def score(self, features: Features) -> float:
        signed = features.roll_deg - self.neutral_deg
        if self.direction == "left" and signed <= 0:
            return 0.0
        if self.direction == "right" and signed >= 0:
            return 0.0
        return ramp(abs(signed), self.start_deg, self.full_deg)


@dataclass
class BlinkScorer:
    """Sustained eye closure relative to a rolling personal baseline.

    The baseline removes the need for a blink calibration ritual: eyes differ
    by a factor of two in open-EAR between people, but the *fraction* of
    closure during a deliberate hold is fairly stable. Expressed as a fraction
    it also survives slow drift in face-to-camera distance.
    """

    name: str = "blink"
    start_ratio: float = 0.30
    full_ratio: float = 0.62

    def __post_init__(self) -> None:
        if self.full_ratio <= self.start_ratio:
            raise ValueError("full_ratio must exceed start_ratio")

    def score(self, features: Features) -> float:
        if features.ear_baseline <= 0:
            return 0.0
        closure = 1.0 - (features.ear / features.ear_baseline)
        return ramp(closure, self.start_ratio, self.full_ratio)


REGISTRY: Dict[str, Type] = {
    "tilt": TiltScorer,
    "blink": BlinkScorer,
}
