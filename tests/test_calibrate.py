from dataclasses import replace

import pytest

from ito.calibrate import (
    MARGINAL,
    NOT_SEPARABLE,
    USABLE,
    Suggestion,
    _suggest_gate,
    fit,
    fit_blink,
    fit_tilt,
    verify,
    write_config,
)
from ito.config import load
from ito.sources import synthetic_features
from ito.types import Features


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


def test_fit_measures_how_long_the_movement_could_be_held():
    """The third number a dwell has to fit inside is a duration, not an angle."""
    s = fit_tilt(rest_frames(), move_frames())
    assert s.measurements["hold_ms"] > 1000
    assert 200 <= s.min_dwell_ms <= 600


def test_dwell_is_lowered_when_the_measured_hold_cannot_fill_it():
    action = load(None).actions[0]  # dwell_ms = 450
    s = Suggestion(action="tilt_left", kind="tilt", measurements={"hold_ms": 700})
    gate = _suggest_gate(action, s)
    assert gate == {"dwell_ms": 315}
    assert any("dwell lowered" in note for note in s.notes)


def test_dwell_is_never_raised_by_a_fit():
    action = load(None).actions[0]
    s = Suggestion(action="tilt_left", kind="tilt", measurements={"hold_ms": 5_000})
    assert _suggest_gate(action, s) == {}


def test_a_refused_movement_suggests_no_dwell_change():
    action = load(None).actions[0]
    s = Suggestion(
        action="tilt_left", kind="tilt", verdict=NOT_SEPARABLE, measurements={"hold_ms": 700}
    )
    assert _suggest_gate(action, s) == {}


def test_verification_uses_the_dwell_it_is_about_to_write():
    config = load(None)
    rest, move = rest_frames(), move_frames()
    s = fit(config, "tilt_left", rest, move)
    ok, dwell = verify(config, "tilt_left", s.params, rest, move, {"dwell_ms": 300})
    assert ok
    assert 300 <= dwell <= 380


def test_written_config_carries_the_lowered_dwell(tmp_path):
    config = load(None)
    rest, move = rest_frames(), move_frames()
    s = fit(config, "tilt_left", rest, move)
    out = write_config(
        config, "tilt_left", s.params, tmp_path / "fitted.yaml", None, gate={"dwell_ms": 315}
    )
    assert load(out).actions[0].gate.dwell_ms == 315


def short_hold(degrees=14.0, hold_ms=600, window_ms=6000):
    """A movement the user can only sustain for part of the capture window."""
    frames = []
    t = 0
    while t < window_ms:
        frames.append(
            Features(
                t_ms=t,
                roll_deg=degrees if t < hold_ms else 0.0,
                yaw=0.0,
                pitch=0.0,
                ear=0.30,
                brow_raise=0.28,
            )
        )
        t += 33
    return frames


def test_a_short_hold_is_usable_and_gets_a_dwell_it_can_fill():
    """The person this project is for often cannot hold for a whole capture.

    Refusing them is wrong; handing them thresholds they can reach and a dwell
    they cannot fill is worse, because it verifies as "correct" and then never
    fires.
    """
    config = load(None)
    quiet, move = rest_frames(), short_hold()

    s = fit(config, "tilt_left", quiet, move)
    assert s.verdict == USABLE
    assert s.measurements["hold_ms"] >= 500
    assert s.gate["dwell_ms"] < config.actions[0].gate.dwell_ms

    ok, dwell = verify(config, "tilt_left", s.params, quiet, move, s.gate)
    assert ok
    assert dwell <= s.gate["dwell_ms"] + 40
