import json

from ito.audit import AuditLog
from ito.config import load
from ito.pipeline import Pipeline, RollingBaseline
from ito.sinks import NullSink
from ito.sources import replay_features, synthetic_features


def test_scripted_session_exercises_the_whole_chain():
    cfg = load(None)
    pipe = Pipeline(cfg, NullSink(), AuditLog(None))
    stats = pipe.run(synthetic_features(fps=30.0, seed=1))
    assert stats.frames > 300
    assert stats.frames_scored > 0
    assert stats.published >= 1
    assert stats.cancelled_dwells >= 1, "the deliberately-short tilt should cancel"
    assert stats.per_action["tilt_left"]["published"] >= 1


def test_natural_blinks_do_not_fire_a_450ms_dwell():
    cfg = load(None)
    pipe = Pipeline(cfg, NullSink(), AuditLog(None))
    # 12 seconds of rest with physiological blinks every few seconds
    stats = pipe.run(synthetic_features(script=(("idle", 12.0),), fps=30.0, seed=3))
    assert stats.published == 0


def test_baseline_converges_and_ignores_closed_eyes():
    b = RollingBaseline(window_ms=5000, min_samples=10)
    for i in range(60):
        b.update(i * 33, 0.30)
    assert abs(b.value - 0.30) < 0.01
    for i in range(60, 200):
        b.update(i * 33, 0.03)  # eyes shut for a long time
    assert b.value > 0.25, "a held closure must not drag the reference down"


def test_baseline_is_unavailable_before_enough_samples():
    b = RollingBaseline(window_ms=5000, min_samples=10)
    for i in range(5):
        assert b.update(i * 33, 0.30) == 0.0


def test_replay_of_recorded_features_matches_the_live_run(tmp_path):
    frames = list(synthetic_features(fps=30.0, seed=5))
    path = tmp_path / "session.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for f in frames:
            fh.write(json.dumps({"t_ms": f.t_ms, "features": f.as_dict()}) + "\n")

    live = Pipeline(load(None), NullSink(), AuditLog(None)).run(iter(frames))
    replayed = Pipeline(load(None), NullSink(), AuditLog(None)).run(replay_features(path))
    assert live.published == replayed.published
    assert live.frames == replayed.frames
