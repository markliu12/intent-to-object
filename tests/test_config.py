from pathlib import Path

import pytest

from ito.config import ConfigError, default_dict, from_dict, load

REPO = Path(__file__).resolve().parents[1]


def test_builtin_default_is_valid():
    cfg = load(None)
    assert len(cfg.actions) == 1
    assert cfg.actions[0].risk == "low"


def test_shipped_example_config_is_valid():
    path = REPO / "configs" / "example.yaml"
    assert path.exists(), "configs/example.yaml is referenced by the README"
    cfg = load(path)
    assert cfg.actions


def test_high_risk_action_is_refused_with_an_explanation():
    raw = default_dict()
    raw["actions"][0]["risk"] = "high"
    with pytest.raises(ConfigError, match="unsupported by design"):
        from_dict(raw)


def test_unknown_keys_are_rejected():
    raw = default_dict()
    raw["actions"][0]["dwell"] = 400  # typo for gate.dwell_ms
    with pytest.raises(ConfigError, match="unknown key"):
        from_dict(raw)


def test_unknown_gate_key_is_rejected():
    raw = default_dict()
    raw["actions"][0]["gate"]["dwell"] = 400
    with pytest.raises(ConfigError, match="unknown key"):
        from_dict(raw)


def test_two_identical_actions_are_refused():
    raw = default_dict()
    raw["actions"].append(dict(raw["actions"][0], name="tilt_left_again"))
    with pytest.raises(ConfigError, match="fire together"):
        from_dict(raw)


def test_wildcard_topic_is_refused():
    raw = default_dict()
    raw["actions"][0]["topic"] = "ito/intent/#"
    with pytest.raises(ConfigError, match="wildcard"):
        from_dict(raw)


def test_unquoted_yaml_on_off_keys_are_accepted():
    """YAML 1.1 turns bare on/off into booleans; users write it anyway."""
    import yaml

    raw = yaml.safe_load(
        "actions:\n"
        "  - name: t\n"
        "    scorer: tilt\n"
        "    gate:\n"
        "      on: 0.5\n"
        "      off: 0.35\n"
        "      dwell_ms: 300\n"
    )
    cfg = from_dict(raw)
    assert cfg.actions[0].gate.on == 0.5
    assert cfg.actions[0].gate.off == 0.35


def test_short_dwell_produces_a_warning_not_an_error():
    raw = default_dict()
    raw["actions"][0]["gate"]["dwell_ms"] = 120
    cfg = from_dict(raw)
    assert any("dwell_ms" in w for w in cfg.warnings)
