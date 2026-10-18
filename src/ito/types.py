"""Value types shared across the pipeline. Pure data, no behaviour."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Features:
    """Pose-normalised scalar features for a single frame.

    Angles are in degrees, ratios are dimensionless. ``t_ms`` is monotonic
    milliseconds from an arbitrary epoch chosen by the source, and is what the
    gate and every log line use.

    ``captured_at`` is a ``time.perf_counter()`` value taken the moment the
    frame left the camera, or 0 for sources with no camera. It exists so that
    frame-to-publish latency can be measured without pretending the feature
    extraction stage is free.
    """

    t_ms: int
    roll_deg: float
    yaw: float
    pitch: float
    ear: float
    brow_raise: float
    ear_baseline: float = 0.0
    captured_at: float = 0.0

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
    confirmed: bool | None = None


@dataclass
class SessionStats:
    """Everything needed to report the three MVP metrics honestly."""

    started_ms: int = 0
    last_ms: int = 0
    frames: int = 0
    frames_scored: int = 0
    fired: int = 0
    published: int = 0
    blocked: int = 0
    cancelled_dwells: int = 0
    dwell_samples: list[int] = field(default_factory=list)
    frame_to_publish_ms: list[float] = field(default_factory=list)
    decision_us: list[float] = field(default_factory=list)
    publish_us: list[float] = field(default_factory=list)
    per_action: dict = field(default_factory=dict)
    baseline_min: float = 0.0
    baseline_max: float = 0.0
    # Availability, not accuracy: a source that yields nothing looks exactly
    # like a working system from the outside, so the gaps are counted.
    face_gaps: int = 0
    max_gap_ms: int = 0
    # True when the sink waited for the broker to acknowledge, i.e. when
    # frame_to_publish_ms is frame -> PUBACK rather than frame -> enqueue.
    publish_acked: bool = False

    def uptime_s(self) -> float:
        if self.frames < 2:
            return 0.0
        return max(0.0, (self.last_ms - self.started_ms) / 1000.0)

    def as_summary(self) -> dict:
        """The record written at the end of a session.

        ``ito review`` needs it to compute false triggers per hour: a count of
        unintended commands is meaningless without the exposure it happened
        over.
        """
        return {
            "kind": "session",
            "frames": self.frames,
            "frames_scored": self.frames_scored,
            "duration_s": round(self.uptime_s(), 3),
            "fired": self.fired,
            "published": self.published,
            "blocked": self.blocked,
            "cancelled_dwells": self.cancelled_dwells,
            "face_gaps": self.face_gaps,
            "max_gap_ms": self.max_gap_ms,
            "publish_acked": self.publish_acked,
            "per_action": self.per_action,
        }
