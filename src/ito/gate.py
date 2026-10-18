"""Dwell gate: the difference between a gesture detector and a usable switch.

A single-switch device must not fire on every frame where the signal is high.
It fires once, after the posture has been *held*, and then refuses to fire again
until the user has released. That release requirement is what stops a slow,
continuous movement (which is exactly what many users have) from producing a
stream of identical commands.

Hysteresis (``off`` < ``on``) keeps a signal sitting near the boundary from
chattering, and the cancelled-dwell counter is the cheapest false-positive
diagnostic in the system: a user who produces many cancellations is producing
movement the thresholds are only half-reading.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import Fire

IDLE = "idle"
CANDIDATE = "candidate"
COOLDOWN = "cooldown"


@dataclass(frozen=True)
class GateConfig:
    on: float = 0.50
    off: float = 0.35
    dwell_ms: int = 400
    cooldown_ms: int = 1500

    def validate(self) -> None:
        if not 0.0 < self.off < self.on <= 1.0:
            raise ValueError(f"gate requires 0 < off < on <= 1, got off={self.off} on={self.on}")
        if self.dwell_ms < 0:
            raise ValueError("dwell_ms must be >= 0")
        if self.cooldown_ms < 0:
            raise ValueError("cooldown_ms must be >= 0")


class Gate:
    def __init__(self, config: GateConfig, action: str = "") -> None:
        config.validate()
        self.config = config
        self.action = action
        self.state = IDLE
        self.cancelled = 0
        self.fired = 0
        self._t_on: int | None = None
        self._t_release: int | None = None
        self._last_fire: int = 0

    def update(self, t_ms: int, score: float) -> Fire | None:
        cfg = self.config

        if self.state == IDLE:
            if score >= cfg.on:
                self.state = CANDIDATE
                self._t_on = t_ms
            return None

        if self.state == CANDIDATE:
            if score < cfg.off:
                self.cancelled += 1
                self.state = IDLE
                self._t_on = None
                return None
            held = t_ms - (self._t_on or 0)
            if held >= cfg.dwell_ms:
                self.fired += 1
                self.state = COOLDOWN
                self._t_release = None
                fire = Fire(t_ms=t_ms, score=score, dwell_ms=max(0, held), action=self.action)
                self._t_on = None
                self._last_fire = t_ms
                return fire
            return None

        # COOLDOWN: wait out the cooldown, then wait for release before re-arming.
        if score < cfg.off:
            if self._t_release is None:
                self._t_release = t_ms
            if t_ms - self._last_fire >= cfg.cooldown_ms:
                self.state = IDLE
                self._t_release = None
        else:
            self._t_release = None
        return None

    def reset(self) -> None:
        self.state = IDLE
        self._t_on = None
        self._t_release = None
