"""Risk policy: what the system is allowed to do with a recognised intent.

The tiers are not decoration. Low-risk actions are things where a misfire costs
an annoyance. Medium-risk actions are things where a misfire costs a little
money, comfort, or trust, so they cost a confirmation. High-risk actions are
things where a misfire can hurt someone, and those are refused at config load
time rather than at runtime, so the refusal is impossible to overlook.
"""

from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Optional

from .types import Decision, Fire

TIERS = ("low", "medium", "high")
BLOCKED_TIERS = ("high",)


@dataclass(frozen=True)
class RateLimit:
    """A blunt ceiling on how much the system can do per minute.

    Not a safety mechanism on its own -- it exists so that a stuck sensor or a
    looping replay cannot flood a topic while a user is not looking.
    """

    max_per_minute: int = 20
    window_ms: int = 60_000


class Policy:
    def __init__(
        self,
        rate_limit: Optional[RateLimit] = None,
        confirmer: Optional[Callable[[str, Fire], bool]] = None,
    ) -> None:
        self.rate_limit = rate_limit or RateLimit()
        self.confirmer = confirmer or deny
        self._stamps: Deque[int] = deque()

    def evaluate(self, tier: str, action: str, fire: Fire) -> Decision:
        if tier not in TIERS:
            return Decision(False, f"unknown risk tier {tier!r}", tier)
        if tier in BLOCKED_TIERS:
            return Decision(False, "tier_high_unsupported", tier)

        if self._rate_limited(fire.t_ms):
            return Decision(False, "rate_limited", tier)

        if tier == "medium":
            ok = bool(self.confirmer(action, fire))
            if not ok:
                return Decision(False, "not_confirmed", tier, confirmed=False)
            self._stamps.append(fire.t_ms)
            return Decision(True, "ok", tier, confirmed=True)

        self._stamps.append(fire.t_ms)
        return Decision(True, "ok", tier, confirmed=False)

    def _rate_limited(self, now_ms: int) -> bool:
        while self._stamps and now_ms - self._stamps[0] > self.rate_limit.window_ms:
            self._stamps.popleft()
        return len(self._stamps) >= self.rate_limit.max_per_minute


def deny(action: str, fire: Fire) -> bool:
    """Default confirmer: refuse anything that asks. Safe in unattended runs."""
    return False


class StdinConfirmer:
    """Terminal confirmation for medium-risk actions.

    Blocking on purpose: a confirmation prompt with no answer must not resolve
    to "yes" on a timer, and a background thread reading stdin on Windows is a
    reliable way to produce a hung process. Non-interactive sessions deny.
    """

    def __init__(self, timeout_note: str = "press enter within 10s") -> None:
        self.timeout_note = timeout_note

    def __call__(self, action: str, fire: Fire) -> bool:
        if not sys.stdin or not sys.stdin.isatty():
            return False
        try:
            answer = input(
                f"[medium risk] {action} (score {fire.score:.2f}, "
                f"held {fire.dwell_ms}ms) -- confirm? [y/N] "
            )
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().lower() in ("y", "yes")
