"""Face-landmark geometry.

This module is the only place that knows about MediaPipe landmark indices, and
it knows nothing about MediaPipe itself: it takes an ``(N, 2|3)`` array.

Two coordinate decisions matter and are easy to get wrong:

1. *Aspect correction.* MediaPipe returns x normalised by frame width and y by
   frame height. Angles computed on those raw values are wrong on any non-square
   frame, so y is rescaled into width units first.

2. *Sign conventions.* Image y grows downward. We flip once, here, and then
   never again: positive roll means the head tilts toward the subject's own
   left; positive yaw means the face turns toward the subject's own left;
   positive pitch means looking down.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from .types import Features

# Six-point eye contours, ordered (outer, upper-1, upper-2, inner, lower-1, lower-2).
# EAR = (|p2-p6| + |p3-p5|) / (2 |p1-p4|)
RIGHT_EYE_EAR = (33, 160, 158, 133, 153, 144)
LEFT_EYE_EAR = (263, 387, 385, 362, 380, 373)
RIGHT_EYE_CORNERS = (33, 133)
LEFT_EYE_CORNERS = (362, 263)
NOSE_TIP = 1
CHIN = 152
BROW_POINTS = (63, 105, 66, 293, 334, 296)
MOUTH_CORNERS = (61, 291)

MIN_LANDMARKS = 468
DEFAULT_ASPECT = 4.0 / 3.0
_EPS = 1e-9


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b))


def _eye_aspect_ratio(xy: np.ndarray, idx: Sequence[int]) -> float:
    p = [xy[i] for i in idx]
    vertical = _dist(p[1], p[5]) + _dist(p[2], p[4])
    horizontal = _dist(p[0], p[3])
    if horizontal < _EPS:
        return 0.0
    return vertical / (2.0 * horizontal)


def canonicalize(xy: np.ndarray) -> tuple[np.ndarray, dict]:
    """Remove translation, scale and in-plane rotation from landmarks.

    Returns points in a canonical frame where the eye centres sit at
    ``(-0.5, 0)`` and ``(+0.5, 0)``, i.e. interocular distance is exactly 1.
    This is the 2D half of the head-pose normalisation described in the design
    doc: it makes roll, distance-from-camera and framing irrelevant to any
    feature expressed in these units.

    It does *not* compensate yaw or pitch. Features that are sensitive to those
    (left/right asymmetry) must be corrected upstream or documented as
    approximate. Full 3D normalisation via PnP is a later-phase upgrade.
    """
    r = xy[list(RIGHT_EYE_CORNERS)].mean(axis=0)
    left_c = xy[list(LEFT_EYE_CORNERS)].mean(axis=0)
    v = left_c - r
    dist = float(np.linalg.norm(v))
    if dist < _EPS:
        raise ValueError("degenerate eye corners: cannot establish a canonical frame")
    angle = math.atan2(v[1], v[0])
    scale = 1.0 / dist
    ca, sa = math.cos(-angle), math.sin(-angle)
    rot = np.array([[ca, -sa], [sa, ca]], dtype=float)
    mid = (left_c + r) / 2.0
    out = (xy - mid) @ rot.T * scale
    return out, {"angle_rad": angle, "scale": scale, "origin": mid.tolist()}


def aspect_correct(points: np.ndarray, aspect: float = DEFAULT_ASPECT) -> np.ndarray:
    """Rescale y from height-normalised to width-normalised units."""
    if aspect <= 0:
        raise ValueError("aspect must be positive")
    out = np.array(points, dtype=float, copy=True)
    out[:, 1] = out[:, 1] / aspect
    return out


def extract_features(
    landmarks: np.ndarray,
    t_ms: int = 0,
    aspect: float = DEFAULT_ASPECT,
    ear_baseline: float = 0.0,
    captured_at: float = 0.0,
) -> Features:
    """Reduce a landmark frame to the scalar features the scorers consume."""
    pts = np.asarray(landmarks, dtype=float)
    if pts.ndim != 2 or pts.shape[1] < 2:
        raise ValueError(f"landmarks must be (N,2|3), got {pts.shape}")
    if pts.shape[0] < MIN_LANDMARKS:
        raise ValueError(
            f"need at least {MIN_LANDMARKS} face landmarks, got {pts.shape[0]}; "
            "enable refine_landmarks / iris refinement in the perception backend"
        )

    xy = aspect_correct(pts[:, :2], aspect)

    ear = 0.5 * (_eye_aspect_ratio(xy, RIGHT_EYE_EAR) + _eye_aspect_ratio(xy, LEFT_EYE_EAR))

    right_c = xy[list(RIGHT_EYE_CORNERS)].mean(axis=0)
    left_c = xy[list(LEFT_EYE_CORNERS)].mean(axis=0)
    eye_mid = (right_c + left_c) / 2.0
    iod = _dist(right_c, left_c)
    if iod < _EPS:
        raise ValueError("zero interocular distance")

    dv = left_c - right_c
    roll_deg = math.degrees(math.atan2(dv[1], dv[0]))

    nose = xy[NOSE_TIP]
    yaw = float((nose[0] - eye_mid[0]) / iod)
    pitch = float((nose[1] - eye_mid[1]) / iod)

    canon, _ = canonicalize(xy)
    brow_y = float(canon[list(BROW_POINTS), 1].mean())
    # Eye centres are at y = 0 by construction, so brow height is already relative.
    brow_raise = -brow_y

    return Features(
        t_ms=t_ms,
        roll_deg=roll_deg,
        yaw=yaw,
        pitch=pitch,
        ear=ear,
        brow_raise=brow_raise,
        ear_baseline=ear_baseline,
        captured_at=captured_at,
    )


def mouth_openness(landmarks: np.ndarray, aspect: float = DEFAULT_ASPECT) -> float:
    """Vertical mouth opening in canonical units. Unused by v0.1 scorers.

    Kept because it is the cheapest next feature for stage 5 and it costs three
    lines; feel free to delete it if that never happens.
    """
    xy = aspect_correct(np.asarray(landmarks, dtype=float)[:, :2], aspect)
    lo, ro = MOUTH_CORNERS
    width = _dist(xy[lo], xy[ro])
    if width < _EPS:
        return 0.0
    upper, lower = xy[13], xy[14]
    return _dist(upper, lower) / width


def ramp(value: float, start: float, full: float) -> float:
    """Linear 0..1 ramp, clamped. ``start`` maps to 0, ``full`` maps to 1."""
    if full <= start:
        raise ValueError("full must be greater than start")
    return float(min(1.0, max(0.0, (value - start) / (full - start))))
