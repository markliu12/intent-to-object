"""Value types shared across the pipeline. Pure data, no behaviour."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class Features:
    """Pose-normalised scalar features for a single frame.

    Angles are in degrees, ratios are dimensionless. ``t_ms`` is monotonic
    milliseconds from an arbitrary epoch chosen by the source.
    """

    t_ms: int
    roll_deg: float
    yaw: float
    pitch: float
    ear: float
    brow_raise: float
    ear_baseline: float = 0.0

    def as_dict(self) -> dict:
        return {
            "t_ms": self.t_ms,
            "roll_deg": round(self.roll_deg, 4),
            "yaw": round(self.yaw, 4),
            "pitch": round(self.pitch, 4),
            "ear": round(self.ear, 4),
            "brow_raise": round(self.brow_raise, 4),
            "ear_baseline": round(self.ear_baseline, 4),
        }


@dataclass(frozen=True)
class Fire:
    """A gate decided the dwell requirement was met."""

    t_ms: int
    score: float
    dwell_ms: int
    action: str


@dataclass(frozen=True)
class Decision:
    """Result of applying the risk policy to a fired intent."""

    allowed: bool
    reason: str
    tier: str = "low"
    confirmed: Optional[bool] = None


@dataclass
class SessionStats:
    """Everything needed to report the three MVP metrics honestly."""

    started_ms: int = 0
    frames: int = 0
    frames_scored: int = 0
    fired: int = 0
    published: int = 0
    blocked: int = 0
    cancelled_dwells: int = 0
    dwell_samples: List[int] = field(default_factory=list)
    per_action: dict = field(default_factory=dict)
    baseline_min: float = 0.0
    baseline_max: float = 0.0

    def uptime_s(self) -> float:
        if self.frames < 2:
            return 0.0
        return max(0.0, (self.last_ms - self.started_ms) / 1000.0)

    last_ms: int = 0
