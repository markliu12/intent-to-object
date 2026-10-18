from dataclasses import replace

import pytest

from ito.calibrate import (
    MARGINAL,
    NOT_SEPARABLE,
    USABLE,
    fit,
    fit_blink,
    fit_tilt,
    verify,
    write_config,
)
from ito.config import load
from ito.sources import synthetic_features


def rest_frames(seconds=8.0, seed=7):
    return list(synthetic_features(script=(("idle", seconds),), fps=30.0, seed=seed))


def move_frames(phase="tilt", seconds=6.0, seed=8):
    return list(synthetic_features(script=((phase, seconds),), fps=30.0, seed=seed))


def test_tilt_fit_places_the_start_above_resting_sway():
    s = fit_tilt(rest_frames(), move_frames())
    assert s.verdict == USABLE
    assert s.params["start_deg"] > s.measurements["rest_band_deg"]
    assert s.params["full_deg"] > s.params["start_deg"]
    assert abs(s.params["neutral_deg"]) < 5.0


def test_tilt_fit_refuses_a_movement_smaller_than_the_sway():
    quiet = rest_frames()
    tiny = []  # a movement that barely leaves the resting band
    for frames in move_frames():
        tiny.append(replace(frames, roll_deg=frames.roll_deg * 0.15))
    s = fit_tilt(quiet, tiny)
    assert s.verdict == NOT_SEPARABLE
    assert s.notes


def test_tilt_fit_reports_a_marginal_band():
    quiet = rest_frames()
    weak = []
    for frames in move_frames():
        weak.append(replace(frames, roll_deg=frames.roll_deg * 0.4))
    s = fit_tilt(quiet, weak)
    assert s.verdict in (MARGINAL, NOT_SEPARABLE)


def test_tilt_fit_needs_movement_in_the_configured_direction():
    s = fit_tilt(rest_frames(), move_frames(), direction="right")
    assert s.verdict == NOT_SEPARABLE
    assert "direction" in s.notes[0]


def test_blink_fit_needs_a_real_closure():
    s = fit_blink(rest_frames(), move_frames())
    assert s.verdict == NOT_SEPARABLE  # the tilt script never closes the eyes


def test_blink_fit_accepts_a_held_closure():
    s = fit_blink(rest_frames(), move_frames(phase="blink"))
    assert s.verdict in (USABLE, MARGINAL)
    assert 0 < s.params["start_ratio"] < s.params["full_ratio"] <= 0.95


def test_fit_dispatches_by_scorer_name():
    config = load(None)
    s = fit(config, "tilt_left", rest_frames(), move_frames())
    assert s.action == "tilt_left" and s.kind == "tilt"


def test_fit_rejects_an_unknown_action():
    with pytest.raises(Exception, match="no action named"):
        fit(load(None), "nope", rest_frames(), move_frames())


def test_verification_reuses_the_collected_movement():
    config = load(None)
    rest, move = rest_frames(), move_frames()
    s = fit(config, "tilt_left", rest, move)
    ok, dwell = verify(config, "tilt_left", s.params, rest, move)
    assert ok
    assert dwell >= 200


def test_written_config_is_valid_and_carries_the_new_thresholds(tmp_path):
    config = load(None)
    rest, move = rest_frames(), move_frames()
    s = fit(config, "tilt_left", rest, move)
    out = write_config(config, "tilt_left", s.params, tmp_path / "fitted.yaml", None)
    reloaded = load(out)
    params = reloaded.actions[0].scorer_params
    assert params["start_deg"] == s.params["start_deg"]
    assert params["full_deg"] == s.params["full_deg"]
    # the gate and topic must survive the rewrite untouched
    assert reloaded.actions[0].topic == config.actions[0].topic
    assert reloaded.actions[0].gate.dwell_ms == config.actions[0].gate.dwell_ms
