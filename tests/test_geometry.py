import math

import numpy as np
import pytest

from ito.geometry import (
    BROW_POINTS,
    canonicalize,
    extract_features,
    ramp,
    suggest_thresholds,
)

W, H = 640.0, 480.0
CENTER = np.array([320.0, 240.0])


def frontal_face_pixels(eye_open: float = 7.2) -> np.ndarray:
    """A 478-point face built in pixel space. Only the used indices are set.

    Building the fixture in pixels and then normalising by (W, H) is the whole
    point: it reproduces the anisotropic scaling that MediaPipe actually hands
    you, so the aspect-correction path is exercised rather than assumed.
    """
    pts = np.zeros((478, 2))
    up = dn = eye_open

    pts[33] = (216, 240)
    pts[133] = (264, 240)
    pts[160] = (224, 240 - up)
    pts[158] = (256, 240 - up)
    pts[144] = (224, 240 + dn)
    pts[153] = (256, 240 + dn)

    pts[362] = (376, 240)
    pts[263] = (424, 240)
    pts[387] = (416, 240 - up)
    pts[385] = (384, 240 - up)
    pts[373] = (416, 240 + dn)
    pts[380] = (384, 240 + dn)

    pts[1] = (320, 285)
    pts[152] = (320, 400)
    pts[61] = (290, 340)
    pts[291] = (350, 340)
    pts[13] = (320, 332)
    pts[14] = (320, 348)

    for i, (x, y) in enumerate([(232, 205), (240, 202), (248, 205)]):
        pts[BROW_POINTS[i]] = (x, y)
    for i, (x, y) in enumerate([(392, 205), (400, 202), (408, 205)]):
        pts[BROW_POINTS[3 + i]] = (x, y)
    return pts


def rotated(pts: np.ndarray, deg: float) -> np.ndarray:
    a = math.radians(deg)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return (pts - CENTER) @ rot.T + CENTER


def normalised(pts: np.ndarray) -> np.ndarray:
    out = pts.copy()
    out[:, 0] /= W
    out[:, 1] /= H
    return out


@pytest.mark.parametrize("deg", [0.0, 7.5, 20.0, -18.0])
def test_roll_is_recovered_after_aspect_correction(deg):
    face = normalised(rotated(frontal_face_pixels(), deg))
    f = extract_features(face, aspect=W / H)
    assert abs(f.roll_deg - deg) < 0.05


def test_ignoring_aspect_distorts_roll():
    """The failure mode this guards against: a tilted head misread because
    landmarks were treated as isotropic."""
    face = normalised(rotated(frontal_face_pixels(), 20.0))
    wrong = extract_features(face, aspect=1.0)
    right = extract_features(face, aspect=W / H)
    assert abs(wrong.roll_deg - right.roll_deg) > 2.0


def test_open_and_closed_eye_differ_in_ear():
    open_f = extract_features(normalised(frontal_face_pixels(7.2)), aspect=W / H)
    shut_f = extract_features(normalised(frontal_face_pixels(0.8)), aspect=W / H)
    assert abs(open_f.ear - 0.30) < 0.01
    assert shut_f.ear < 0.06
    assert open_f.ear > 4 * shut_f.ear


def test_canonical_frame_puts_eye_centres_on_the_unit_interval():
    face = normalised(rotated(frontal_face_pixels(), 12.0))
    canon, _ = canonicalize(face)
    right_c = canon[[33, 133]].mean(axis=0)
    left_c = canon[[362, 263]].mean(axis=0)
    assert abs(right_c[0] + 0.5) < 1e-6
    assert abs(left_c[0] - 0.5) < 1e-6
    assert abs(right_c[1]) < 1e-6


def test_brow_raise_is_scale_invariant():
    face = normalised(frontal_face_pixels())
    scaled = face.copy()
    scaled[:, 0] *= 0.5
    scaled[:, 1] *= 0.5
    a = extract_features(face, aspect=W / H)
    b = extract_features(scaled, aspect=W / H)
    assert abs(a.brow_raise - b.brow_raise) < 1e-6


def test_rejects_frames_without_iris_refinement():
    with pytest.raises(ValueError, match="at least 468"):
        extract_features(np.zeros((300, 2)))


def test_ramp_clamps():
    assert ramp(0.0, 5.0, 13.0) == 0.0
    assert ramp(5.0, 5.0, 13.0) == 0.0
    assert ramp(9.0, 5.0, 13.0) == pytest.approx(0.5)
    assert ramp(99.0, 5.0, 13.0) == 1.0


def test_suggest_thresholds_sits_above_resting_noise():
    quiet = [0.2, -0.1, 0.4, -0.3, 0.1, 0.0, 0.2, -0.2, 0.3, 0.0]
    start, full = suggest_thresholds(quiet)
    assert start >= 2.0
    assert full > start
