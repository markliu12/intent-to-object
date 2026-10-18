import json

import pytest

from ito.review import INTENDED, UNINTENDED, review


def write_log(path, records):
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def command(t_ms, allowed=True):
    return {
        "ts": "2026-10-18T09:00:00.000",
        "t_ms": t_ms,
        "action": "tilt_left",
        "score": 0.9,
        "dwell_ms": 467,
        "tier": "low",
        "allowed": allowed,
        "reason": "ok",
    }


def session(duration_s):
    return {"kind": "session", "duration_s": duration_s, "frames": 10000, "published": 2}


def test_rate_is_computed_over_the_session_exposure(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_log(path, [command(1000), command(2000), command(3000), session(3600.0)])

    answers = iter([True, False, False])
    report = review(
        path,
        ask=lambda record, i, n: next(answers),
        log=lambda *a, **k: None,
    )

    assert report["commands"] == 3
    assert report["unintended"] == 2
    assert report["exposure_s"] == 3600.0
    assert report["false_triggers_per_hour"] == pytest.approx(2.0)


def test_long_exposure_makes_a_single_false_trigger_small(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_log(path, [command(1000), command(2000), session(6 * 3600.0)])
    answers = iter([True, False])
    report = review(path, ask=lambda r, i, n: next(answers), log=lambda *a, **k: None)
    assert report["false_triggers_per_hour"] == pytest.approx(1 / 6, abs=0.01)


def test_missing_session_record_yields_an_upper_bound(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_log(path, [command(0), command(10_000)])
    report = review(path, ask=lambda r, i, n: False, log=lambda *a, **k: None)
    assert report["exposure_source"].startswith("span between")
    assert report["false_triggers_per_hour"] == pytest.approx(720.0)


def test_marks_are_written_next_to_the_log(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_log(path, [command(1000), session(60.0)])
    review(path, ask=lambda r, i, n: True, log=lambda *a, **k: None)
    out = path.with_suffix(path.suffix + ".review.json")
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["marks"][0]["verdict"] == INTENDED
    assert data["unintended"] == 0


def test_quitting_stops_without_writing(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_log(path, [command(1000), command(2000), session(60.0)])

    def ask(record, i, n):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        review(path, ask=ask, log=lambda *a, **k: None)
    assert not path.with_suffix(path.suffix + ".review.json").exists()


def test_a_log_without_commands_is_an_error(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_log(path, [session(60.0)])
    with pytest.raises(ValueError, match="no commands"):
        review(path, ask=lambda r, i, n: True, log=lambda *a, **k: None)


def test_unintended_verdict_constant_is_used(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_log(path, [command(1000), session(60.0)])
    report = review(path, ask=lambda r, i, n: False, log=lambda *a, **k: None)
    assert report["marks"][0]["verdict"] == UNINTENDED
