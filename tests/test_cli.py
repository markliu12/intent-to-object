"""The command surface: the part of the project a first-time user actually meets.

It had no tests, which is why a traceback on a missing broker and a session
record rendered as a "declined command called unknown" both survived to 0.2.
"""

import json
from pathlib import Path

from ito.cli import main
from ito.sources import SourceError

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "configs" / "example.yaml"


def write_jsonl(path: Path, records) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def command(t_ms: int = 1000) -> dict:
    return {
        "ts": "2026-10-18T09:00:00.000",
        "t_ms": t_ms,
        "action": "tilt_left",
        "score": 0.9,
        "dwell_ms": 467,
        "tier": "low",
        "allowed": True,
        "reason": "ok",
        "topic": "ito/intent/tilt_left",
    }


def session_record(duration_s: float = 15.0) -> dict:
    return {
        "kind": "session",
        "duration_s": duration_s,
        "frames": 451,
        "frames_scored": 437,
        "published": 2,
        "blocked": 0,
        "cancelled_dwells": 1,
        "face_gaps": 0,
        "max_gap_ms": 0,
    }


def test_explain_renders_the_session_record_as_a_session_not_a_command(tmp_path, capsys):
    path = tmp_path / "audit.jsonl"
    write_jsonl(path, [command(1000), session_record()])

    assert main(["explain", str(path)]) == 0

    out = capsys.readouterr().out
    assert "unknown" not in out
    assert "session ended" in out
    assert "declined, by reason" not in out


def test_explain_still_reports_a_declined_command(tmp_path, capsys):
    path = tmp_path / "audit.jsonl"
    declined = dict(command(1000), allowed=False, reason="rate_limited", topic=None)
    write_jsonl(path, [declined, session_record()])

    assert main(["explain", str(path)]) == 0

    out = capsys.readouterr().out
    assert "rate_limited" in out
    assert "declined, by reason" in out


def test_demo_runs_the_whole_chain_without_a_camera(capsys):
    assert main(["demo", "--no-audit"]) == 0

    out = capsys.readouterr().out
    assert "sent to ito/intent/tilt_left" in out
    assert "not a user measurement" in out


def test_config_check_accepts_the_shipped_example(capsys):
    assert main(["config", "check", str(EXAMPLE)]) == 0
    assert "action(s)" in capsys.readouterr().out


def test_config_check_rejects_a_misspelt_section_key(tmp_path, capsys):
    path = tmp_path / "bad.yaml"
    path.write_text("mqtt:\n  hots: localhost\nactions:\n  - scorer: tilt\n", encoding="utf-8")
    assert main(["config", "check", str(path)]) == 1
    assert "unknown key" in capsys.readouterr().err


def test_run_says_what_is_missing_instead_of_dying_in_a_traceback(monkeypatch, capsys):
    def no_camera(**kwargs):
        raise SourceError("camera input needs OpenCV: pip install '.[camera]'")

    monkeypatch.setattr("ito.cli.camera_features", no_camera)

    assert main(["run", "--no-audit"]) == 2
    assert "OpenCV" in capsys.readouterr().err


def test_selftest_passes(capsys):
    assert main(["selftest"]) == 0
    assert "ok:" in capsys.readouterr().out


def test_calibrate_synthetic_writes_a_config_that_loads(tmp_path, capsys):
    out = tmp_path / "fitted.yaml"
    assert main(["calibrate", "--source", "synthetic", "-o", str(out)]) == 0
    assert out.exists()
    assert main(["config", "check", str(out)]) == 0


def test_calibrate_keeps_the_session_it_measured(tmp_path, capsys):
    """Without the recording, checking a threshold later means asking the person
    to perform the movement again -- the thing recording exists to avoid."""
    out = tmp_path / "fitted.yaml"
    session = tmp_path / "session.jsonl"

    assert (
        main(["calibrate", "--source", "synthetic", "--session", str(session), "-o", str(out)]) == 0
    )

    assert session.exists()
    assert main(["replay", str(session), "--no-audit"]) == 0
