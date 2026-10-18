"""The pipeline: features in, decisions and published intents out."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Callable, Deque, Iterator, List, Optional, Tuple

import numpy as np

from .audit import AuditLog
from .config import ActionConfig, Config
from .gate import Gate
from .policy import Policy, RateLimit
from .scorers import build_scorer
from .sinks import Sink, render_payload
from .types import Decision, Features, Fire, SessionStats


class RollingBaseline:
    """Median of recent open-eye EAR, used as the blink reference.

    A rolling median rather than a fixed calibrated constant for three reasons:
    it needs no calibration ritual, it survives a slow drift in face-to-camera
    distance, and a median ignores the very downward spikes (blinks) it is
    supposed to detect.

    Frames that are *well* below the current baseline are not appended -- a user
    holding their eyes shut for longer than the window would otherwise drag the
    reference down to their closed value and then report them as open-eyed.
    """

    def __init__(self, window_ms: int = 20_000, min_samples: int = 15, guard: float = 0.5):
        self.window_ms = window_ms
        self.min_samples = min_samples
        self.guard = guard
        self._buf: Deque[Tuple[int, float]] = deque()
        self.value = 0.0

    def update(self, t_ms: int, ear: float) -> float:
        if ear > 0 and (self.value <= 0 or ear >= self.guard * self.value):
            self._buf.append((t_ms, ear))
        while self._buf and t_ms - self._buf[0][0] > self.window_ms:
            self._buf.popleft()
        if len(self._buf) >= self.min_samples:
            self.value = float(np.median([v for _, v in self._buf]))
        return self.value


@dataclass
class Bound:
    action: ActionConfig
    scorer: Any
    gate: Gate


class Pipeline:
    def __init__(
        self,
        config: Config,
        sink: Sink,
        audit: Optional[AuditLog] = None,
        confirmer: Optional[Callable[[str, Fire], bool]] = None,
        on_event: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.config = config
        self.sink = sink
        self.audit = audit or AuditLog(None)
        self.on_event = on_event
        self.bound: List[Bound] = [
            Bound(a, build_scorer(a.scorer, a.scorer_params), Gate(a.gate, a.name))
            for a in config.actions
        ]
        self.policy = Policy(
            RateLimit(int(config.policy.get("rate_limit_per_min", 20))), confirmer
        )
        window = int(float(config.perception.get("baseline_window_s", 20.0)) * 1000)
        self.baseline = RollingBaseline(
            window_ms=window,
            min_samples=int(config.perception.get("baseline_min_samples", 15)),
        )
        self.stats = SessionStats()
        self._published: dict = {}
        self._t0: Optional[int] = None
        self._b_min = 0.0
        self._b_max = 0.0

    def process(self, frame: Features) -> List[dict]:
        stats = self.stats
        stats.frames += 1
        if self._t0 is None:
            self._t0 = frame.t_ms
            stats.started_ms = frame.t_ms
        stats.last_ms = frame.t_ms

        baseline = self.baseline.update(frame.t_ms, frame.ear)
        if baseline > 0:
            frame = replace(frame, ear_baseline=baseline)
            stats.frames_scored += 1
            self._b_min = baseline if self._b_min == 0 else min(self._b_min, baseline)
            self._b_max = max(self._b_max, baseline)

        out: List[dict] = []
        for bound in self.bound:
            score = bound.scorer.score(frame)
            fire = bound.gate.update(frame.t_ms, score)
            if fire is None:
                continue
            stats.fired += 1
            decision = self.policy.evaluate(bound.action.risk, bound.action.name, fire)
            record = self._apply(bound, fire, decision)
            out.append(record)
            if self.on_event:
                self.on_event(record)

        stats.cancelled_dwells = sum(b.gate.cancelled for b in self.bound)
        for bound in self.bound:
            name = bound.action.name
            entry = stats.per_action.setdefault(
                name, {"fired": 0, "published": 0, "cancelled": 0}
            )
            entry["fired"] = bound.gate.fired
            entry["cancelled"] = bound.gate.cancelled
            entry["published"] = self._published.get(name, 0)
        stats.baseline_min = self._b_min
        stats.baseline_max = self._b_max
        return out

    def _apply(self, bound: Bound, fire: Fire, decision: Decision) -> dict:
        action = bound.action
        payload = ""
        if decision.allowed:
            payload = render_payload(
                action.payload,
                {
                    "action": action.name,
                    "score": round(fire.score, 3),
                    "dwell_ms": fire.dwell_ms,
                    "t_ms": fire.t_ms,
                },
            )
            self.sink.publish(action.topic, payload)
            self.stats.published += 1
            self._published[action.name] = self._published.get(action.name, 0) + 1
            self.stats.dwell_samples.append(fire.dwell_ms)
        else:
            self.stats.blocked += 1

        return self.audit.write(
            {
                "t_ms": fire.t_ms,
                "action": action.name,
                "scorer": action.scorer,
                "score": round(fire.score, 4),
                "dwell_ms": fire.dwell_ms,
                "tier": decision.tier,
                "allowed": decision.allowed,
                "reason": decision.reason,
                "confirmed": decision.confirmed,
                "topic": action.topic if decision.allowed else None,
                "payload": payload or None,
            }
        )

    def run(
        self,
        source: Iterator[Features],
        duration_s: Optional[float] = None,
    ) -> SessionStats:
        started = time.monotonic()
        try:
            for frame in source:
                self.process(frame)
                if duration_s is not None and time.monotonic() - started >= duration_s:
                    break
        finally:
            self.sink.close()
            self.audit.close()
        return self.stats
