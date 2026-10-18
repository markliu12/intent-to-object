"""The command surface: the part of the project a first-time user actually meets.

It had no tests, which is why a traceback on a missing broker and a session
record rendered as a "declined command called unknown" both survived to 0.2.
"""

import json
from pathlib import Path

import yaml

from ito.cli import _mqtt_credentials, main
from ito.config import default_dict, load
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


def test_run_can_record_the_session_it_just_saw(monkeypatch, tmp_path, capsys):
    """The whole point of the project is the command that arrives when nobody
    asked. Without a recording, that command cannot be replayed."""
    seen = {}

    def fake_camera(**kwargs):
        seen.update(kwargs)
        return iter(())

    monkeypatch.setattr("ito.cli.camera_features", fake_camera)
    out = tmp_path / "live.jsonl"

    assert main(["run", "--no-audit", "--record", str(out)]) == 0

    assert seen["landmarks_out"] == out
    captured = capsys.readouterr().out
    assert "recording face landmarks" in captured
    assert "treat it like a photo" in captured
    assert "replay this session with" in captured


def test_explain_names_the_line_it_could_not_read(tmp_path, capsys):
    path = tmp_path / "audit.jsonl"
    path.write_text(json.dumps(command(1000)) + "\n" + '{"ts": "2026-10-18T09:0', encoding="utf-8")

    assert main(["explain", str(path)]) == 2

    err = capsys.readouterr().err
    assert ":2:" in err
    assert "not valid JSON" in err


def test_replay_names_the_line_it_could_not_read(tmp_path, capsys):
    path = tmp_path / "recording.jsonl"
    path.write_text(
        '{"t_ms": 0, "features": {"roll_deg": 0, "ear": 0.3}}\n{"t_ms": 33,', encoding="utf-8"
    )

    assert main(["replay", str(path), "--no-audit"]) == 2

    err = capsys.readouterr().err
    assert "cut off mid-write" in err


def test_config_init_prints_something_that_loads(capsys):
    import yaml

    from ito.config import from_dict

    assert main(["config", "init"]) == 0
    text = capsys.readouterr().out
    assert from_dict(yaml.safe_load(text)).actions


def test_setup_writes_a_config_that_is_found_again_without_a_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["setup", "--source", "synthetic", "--choice", "1"]) == 0

    written = tmp_path / "config.yaml"
    assert written.exists()
    assert load(written).actions[0].name == "tilt_left"
    assert main(["config", "check"]) == 0


def test_setup_offers_three_movements_and_they_are_not_the_same_action():
    from ito.cli import _setup_action

    left, right, blink = (_setup_action("1"), _setup_action("2"), _setup_action("3"))
    assert left["scorer_params"]["direction"] == "left"
    assert right["scorer_params"]["direction"] == "right"
    assert left["topic"] != right["topic"]
    assert blink["scorer"] == "blink"


def test_setup_refuses_a_choice_it_does_not_have(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["setup", "--source", "synthetic", "--choice", "9"]) == 2

    assert "choose 1, 2 or 3" in capsys.readouterr().err
    assert not (tmp_path / "config.yaml").exists()


def test_setup_asks_rather_than_guessing_when_there_is_no_terminal(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["setup", "--source", "synthetic"]) == 2

    assert "--choice" in capsys.readouterr().err


def test_a_config_in_the_working_directory_is_used_without_a_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    raw = default_dict()
    raw["actions"][0]["name"] = "probe"
    raw["actions"][0]["topic"] = "ito/intent/probe"
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")

    assert main(["demo", "--no-audit"]) == 0

    out, err = capsys.readouterr()
    assert "probe" in out
    assert "using config.yaml" in err


def test_setup_can_configure_a_held_blink(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["setup", "--source", "synthetic", "--choice", "3"]) == 0

    action = load(tmp_path / "config.yaml").actions[0]
    assert action.scorer == "blink"
    assert action.topic == "ito/intent/hold_blink"


def test_mqtt_credentials_prefer_the_environment_over_the_file(monkeypatch):
    """A password in the config is one copy-paste away from leaking; the
    environment is the safer home, so it must win when both are present."""
    raw = default_dict()
    raw["mqtt"]["username"] = "file_user"
    raw["mqtt"]["password"] = "file_pass"
    config = load(None)
    config.mqtt.update(raw["mqtt"])

    monkeypatch.setenv("ITO_MQTT_USERNAME", "env_user")
    monkeypatch.setenv("ITO_MQTT_PASSWORD", "env_pass")
    assert _mqtt_credentials(config) == ("env_user", "env_pass")


def test_mqtt_credentials_fall_back_to_the_file_without_environment(monkeypatch):
    raw = default_dict()
    raw["mqtt"]["username"] = "file_user"
    raw["mqtt"]["password"] = "file_pass"
    config = load(None)
    config.mqtt.update(raw["mqtt"])

    monkeypatch.delenv("ITO_MQTT_USERNAME", raising=False)
    monkeypatch.delenv("ITO_MQTT_PASSWORD", raising=False)
    assert _mqtt_credentials(config) == ("file_user", "file_pass")


def test_mqtt_credentials_environment_fills_only_what_it_sets(monkeypatch):
    """Set only the password in the environment: the username still comes from
    the file, so a half-migrated setup is not silently broken."""
    raw = default_dict()
    raw["mqtt"]["username"] = "file_user"
    raw["mqtt"]["password"] = "file_pass"
    config = load(None)
    config.mqtt.update(raw["mqtt"])

    monkeypatch.delenv("ITO_MQTT_USERNAME", raising=False)
    monkeypatch.setenv("ITO_MQTT_PASSWORD", "env_pass")
    assert _mqtt_credentials(config) == ("file_user", "env_pass")


def test_record_warns_and_cancels_without_confirmation(monkeypatch, tmp_path, capsys):
    """Face landmarks are a measurement of one person's face, so the command must
    say so before writing, and stop when a person does not confirm."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def fake_input(prompt=""):
        return "n"

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("ito.sources.record_landmarks", lambda *a, **k: (0, 0.0))

    assert main(["record", str(tmp_path / "r.jsonl")]) == 0

    captured = capsys.readouterr().out
    assert "measurement of one person's face" in captured
    assert "cancelled" in captured
    assert not (tmp_path / "r.jsonl").exists()


def test_record_proceeds_when_confirmed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    written = {}

    def fake_record(path, **kwargs):
        written["path"] = str(path)
        return (5, 30.0)

    monkeypatch.setattr("ito.sources.record_landmarks", fake_record)

    assert main(["record", str(tmp_path / "r.jsonl")]) == 0

    assert written["path"] == str(tmp_path / "r.jsonl")
    assert "wrote 5 frames" in capsys.readouterr().out
