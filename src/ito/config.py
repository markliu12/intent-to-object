"""Configuration loading and the checks that are worth failing on.

Principle: a config error must be louder than a runtime error. A typo in
``dwell_ms`` that silently falls back to a default produces a system that looks
calibrated and is not, and the user has no way to find out except by being
misfired at. So loading is strict: unknown keys are rejected, high-risk actions
are refused, and dubious-but-legal settings produce warnings rather than silence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .gate import GateConfig
from .policy import TIERS
from .scorers import build_scorer
from .sinks import render_payload


class ConfigError(ValueError):
    pass


DEFAULT_PAYLOAD = (
    '{"intent":"${action}","confidence":${score},'
    '"held_ms":${dwell_ms},"ts_ms":${t_ms},"source":"intent-to-object"}'
)

_TOP_KEYS = {"camera", "perception", "mqtt", "policy", "output", "actions"}
_ACTION_KEYS = {"name", "scorer", "scorer_params", "gate", "risk", "topic", "payload"}
_SECTION_KEYS = {
    "camera": {"index", "width", "height"},
    "perception": {"baseline_window_s", "baseline_min_samples", "face_lost_warn_s"},
    "mqtt": {"host", "port", "client_id", "username", "password", "qos", "retain"},
    "policy": {"rate_limit_per_min"},
    "output": {"audit_path"},
}


@dataclass
class ActionConfig:
    name: str
    scorer: str
    scorer_params: dict[str, Any] = field(default_factory=dict)
    gate: GateConfig = field(default_factory=GateConfig)
    risk: str = "low"
    topic: str = "ito/intent"
    payload: str = DEFAULT_PAYLOAD


@dataclass
class Config:
    camera: dict[str, Any] = field(
        default_factory=lambda: {"index": 0, "width": 640, "height": 480}
    )
    perception: dict[str, Any] = field(
        default_factory=lambda: {"baseline_window_s": 20.0, "baseline_min_samples": 15}
    )
    mqtt: dict[str, Any] = field(
        default_factory=lambda: {"host": "localhost", "port": 1883, "qos": 0, "retain": False}
    )
    policy: dict[str, Any] = field(default_factory=lambda: {"rate_limit_per_min": 20})
    output: dict[str, Any] = field(default_factory=lambda: {"audit_path": "logs/audit.jsonl"})
    actions: list[ActionConfig] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not self.actions:
            raise ConfigError("no actions configured; nothing to detect")

        names = [a.name for a in self.actions]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ConfigError(f"duplicate action names: {sorted(duplicates)}")

        for action in self.actions:
            if action.risk not in TIERS:
                raise ConfigError(f"action {action.name!r}: unknown risk tier {action.risk!r}")
            if action.risk == "high":
                raise ConfigError(
                    f"action {action.name!r} is marked high risk. High-risk outputs "
                    "(locks, gas, medical beds, nurse call) are unsupported by design; "
                    "this is not a missing feature. See docs/safety.md."
                )
            if not action.topic or any(c in action.topic for c in "#+"):
                raise ConfigError(
                    f"action {action.name!r}: topic must be a concrete publish topic "
                    f"without wildcards, got {action.topic!r}"
                )
            try:
                build_scorer(action.scorer, action.scorer_params)
            except (KeyError, TypeError, ValueError) as exc:
                raise ConfigError(f"action {action.name!r}: {exc}") from exc
            try:
                action.gate.validate()
            except ValueError as exc:
                raise ConfigError(f"action {action.name!r}: {exc}") from exc
            render_payload(
                action.payload,
                {"action": action.name, "score": 0.0, "dwell_ms": 0, "t_ms": 0},
            )

        if len(self.actions) > 1:
            kinds = [(a.scorer, str(a.scorer_params.get("direction", "any"))) for a in self.actions]
            if len(set(kinds)) != len(kinds):
                raise ConfigError(
                    "two actions use the same scorer with the same direction; "
                    "they would fire together. Give each action its own movement."
                )
            topics = [a.topic for a in self.actions]
            shared = sorted({t for t in topics if topics.count(t) > 1})
            if shared:
                # Legal -- the payload still says which action fired -- but a
                # subscriber that switches on the topic alone will toggle the
                # lamp for both movements, which is never what was meant.
                self.warnings.append(
                    f"two actions publish to the same topic {shared[0]!r}; a subscriber "
                    "that looks only at the topic cannot tell them apart"
                )

        for action in self.actions:
            g = action.gate
            if g.dwell_ms < 200:
                self.warnings.append(
                    f"{action.name}: dwell_ms={g.dwell_ms} is short enough that "
                    "ordinary blinks and reflexes can trigger it"
                )
            if g.cooldown_ms < 0.5 * g.dwell_ms:
                self.warnings.append(
                    f"{action.name}: cooldown_ms is short relative to dwell_ms; "
                    "a held posture may re-fire quickly"
                )
            if action.risk == "medium":
                self.warnings.append(
                    f"{action.name}: medium risk -- it publishes only when `ito run` is "
                    "given --confirm, so a person is present to answer"
                )


def _check_keys(obj: dict[str, Any], allowed: set, where: str) -> None:
    unknown = set(obj) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}; allowed: {sorted(allowed)}")


def from_dict(raw: dict[str, Any]) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    _check_keys(raw, _TOP_KEYS, "config")

    actions = []
    for i, item in enumerate(raw.get("actions") or []):
        if not isinstance(item, dict):
            raise ConfigError(f"actions[{i}] must be a mapping")
        _check_keys(item, _ACTION_KEYS, f"actions[{i}]")
        if "scorer" not in item:
            raise ConfigError(f"actions[{i}] is missing 'scorer'")
        gate_raw = item.get("gate") or {}
        # YAML 1.1 turns bare ``on:`` / ``off:`` into the booleans True / False.
        # Everybody writes it unquoted at least once, so accept both spellings
        # instead of failing with "unknown key True", which is unhelpful.
        gate_raw = {
            "on" if k is True else "off" if k is False else k: v for k, v in gate_raw.items()
        }
        _check_keys(gate_raw, set(GateConfig.__annotations__), f"actions[{i}].gate")
        actions.append(
            ActionConfig(
                name=str(item.get("name") or item["scorer"]),
                scorer=str(item["scorer"]),
                scorer_params=dict(item.get("scorer_params") or {}),
                gate=GateConfig(**gate_raw),
                risk=str(item.get("risk", "low")),
                topic=str(item.get("topic", "ito/intent")),
                payload=str(item.get("payload", DEFAULT_PAYLOAD)),
            )
        )

    cfg = Config(
        camera=dict(raw.get("camera") or {}),
        perception=dict(raw.get("perception") or {}),
        mqtt=dict(raw.get("mqtt") or {}),
        policy=dict(raw.get("policy") or {}),
        output=dict(raw.get("output") or {}),
        actions=actions,
    )
    if "allow_medium" in cfg.policy:
        raise ConfigError(
            "policy.allow_medium was removed: whether a human is present is now stated on "
            "the command line (`ito run --confirm`), not in the config. Delete the key."
        )
    # Every section, not just the ones with interesting rules: a misspelt
    # `mqtt.hots` that silently falls back to localhost is the exact failure this
    # module's opening paragraph promises to prevent.
    for section, allowed in _SECTION_KEYS.items():
        _check_keys(getattr(cfg, section), allowed, section)
    default = Config()
    for key in ("camera", "perception", "mqtt", "policy", "output"):
        merged = getattr(default, key).copy()
        merged.update(getattr(cfg, key))
        setattr(cfg, key, merged)
    _validate_sections(cfg)
    cfg.validate()
    return cfg


def _validate_sections(cfg: Config) -> None:
    qos = cfg.mqtt.get("qos", 0)
    if isinstance(qos, bool) or not isinstance(qos, int) or qos not in (0, 1, 2):
        raise ConfigError(f"mqtt.qos must be 0, 1 or 2, got {qos!r}")
    port = cfg.mqtt.get("port", 1883)
    if not isinstance(port, int) or not 0 < port < 65536:
        raise ConfigError(f"mqtt.port must be a TCP port, got {port!r}")
    if not isinstance(cfg.mqtt.get("host", ""), str) or not cfg.mqtt.get("host"):
        raise ConfigError("mqtt.host must be a non-empty string")
    for key in ("index", "width", "height"):
        value = cfg.camera.get(key)
        if not isinstance(value, int) or value < 0:
            raise ConfigError(f"camera.{key} must be a non-negative integer, got {value!r}")
    if cfg.camera["width"] <= 0 or cfg.camera["height"] <= 0:
        raise ConfigError("camera.width and camera.height must be positive")
    if cfg.mqtt.get("username"):
        cfg.warnings.append(
            "mqtt.username/password are stored in plain text in this file; delete them "
            "before pasting the config into an issue or a bug report"
        )


def load(path: Path | None) -> Config:
    if path is None:
        return from_dict(default_dict())
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return from_dict(raw)


def default_dict() -> dict[str, Any]:
    """The config used by ``ito demo`` and written by ``ito config init``.

    One movement, one action. Adding a second action before the first one is
    reliable is how single-switch projects stop being reliable.
    """
    return {
        "camera": {"index": 0, "width": 640, "height": 480},
        "perception": {
            "baseline_window_s": 20.0,
            "baseline_min_samples": 15,
            "face_lost_warn_s": 3.0,
        },
        "mqtt": {"host": "localhost", "port": 1883, "qos": 0, "retain": False},
        "policy": {"rate_limit_per_min": 20},
        "output": {"audit_path": "logs/audit.jsonl"},
        "actions": [
            {
                "name": "tilt_left",
                "scorer": "tilt",
                "scorer_params": {
                    "start_deg": 5.0,
                    "full_deg": 13.0,
                    "direction": "left",
                    "neutral_deg": 0.0,
                },
                "gate": {
                    "on": 0.5,
                    "off": 0.35,
                    "dwell_ms": 450,
                    "cooldown_ms": 1800,
                    "release_ms": 150,
                },
                "risk": "low",
                "topic": "ito/intent/tilt_left",
                "payload": DEFAULT_PAYLOAD,
            }
        ],
    }
