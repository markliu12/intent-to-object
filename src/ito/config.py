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
from typing import Any, Dict, List, Optional

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


@dataclass
class ActionConfig:
    name: str
    scorer: str
    scorer_params: Dict[str, Any] = field(default_factory=dict)
    gate: GateConfig = field(default_factory=GateConfig)
    risk: str = "low"
    topic: str = "ito/intent"
    payload: str = DEFAULT_PAYLOAD


@dataclass
class Config:
    camera: Dict[str, Any] = field(
        default_factory=lambda: {"index": 0, "width": 640, "height": 480}
    )
    perception: Dict[str, Any] = field(
        default_factory=lambda: {"baseline_window_s": 20.0, "baseline_min_samples": 15}
    )
    mqtt: Dict[str, Any] = field(
        default_factory=lambda: {"host": "localhost", "port": 1883, "qos": 0, "retain": False}
    )
    policy: Dict[str, Any] = field(
        default_factory=lambda: {"rate_limit_per_min": 20, "allow_medium": False}
    )
    output: Dict[str, Any] = field(default_factory=lambda: {"audit_path": "logs/audit.jsonl"})
    actions: List[ActionConfig] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

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
        if self.policy.get("allow_medium") and not self.actions:
            self.warnings.append("allow_medium is set but no medium-risk action exists")


def _check_keys(obj: Dict[str, Any], allowed: set, where: str) -> None:
    unknown = set(obj) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}; allowed: {sorted(allowed)}")


def from_dict(raw: Dict[str, Any]) -> Config:
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
    default = Config()
    for key in ("camera", "perception", "mqtt", "policy", "output"):
        merged = getattr(default, key).copy()
        merged.update(getattr(cfg, key))
        setattr(cfg, key, merged)
    cfg.validate()
    return cfg


def load(path: Optional[Path]) -> Config:
    if path is None:
        return from_dict(default_dict())
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return from_dict(raw)


def default_dict() -> Dict[str, Any]:
    """The config used by ``ito demo`` and written by ``ito config init``.

    One movement, one action. Adding a second action before the first one is
    reliable is how single-switch projects stop being reliable.
    """
    return {
        "camera": {"index": 0, "width": 640, "height": 480},
        "perception": {"baseline_window_s": 20.0, "baseline_min_samples": 15},
        "mqtt": {"host": "localhost", "port": 1883, "qos": 0, "retain": False},
        "policy": {"rate_limit_per_min": 20, "allow_medium": False},
        "output": {"audit_path": "logs/audit.jsonl"},
        "actions": [
            {
                "name": "tilt_left",
                "scorer": "tilt",
                "scorer_params": {"start_deg": 5.0, "full_deg": 13.0, "direction": "left"},
                "gate": {"on": 0.5, "off": 0.35, "dwell_ms": 450, "cooldown_ms": 1800},
                "risk": "low",
                "topic": "ito/intent/tilt_left",
                "payload": DEFAULT_PAYLOAD,
            }
        ],
    }
