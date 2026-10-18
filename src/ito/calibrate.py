"""Guided re-fit: turn twenty seconds of movement into two numbers.

The reason this is a module and not a settings screen: for a progressive
condition, "my movement is different today" is a normal Tuesday, not an
exception. The re-fit has to be fast enough and boring enough that somebody
does it without asking for help, and it has to tell them when the movement is
not separable instead of quietly writing thresholds that will misfire.

The command also measures itself. The third MVP metric is "seconds from deciding
to re-calibrate to a verified working configuration", and the only trustworthy
place to measure that is inside the tool that does it.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import yaml

from .audit import AuditLog
from .config import ActionConfig, Config, ConfigError, default_dict
from .pipeline import Pipeline
from .sinks import NullSink
from .types import Features

USABLE = "usable"
MARGINAL = "marginal"
NOT_SEPARABLE = "not separable"


@dataclass
class Suggestion:
    action: str
    kind: str
    params: dict = field(default_factory=dict)
    gate: dict = field(default_factory=dict)
    verdict: str = USABLE
    notes: list[str] = field(default_factory=list)
    measurements: dict = field(default_factory=dict)

    @property
    def min_dwell_ms(self) -> int:
        """Shortest dwell that still works for the movement that was measured.

        A movement held for 900 ms can safely use a 450 ms dwell; one that the
        user can only hold for 300 ms cannot. Suggesting a dwell longer than
        the achieved hold is the classic way to build a system that never fires.
        """
        hold = float(self.measurements.get("hold_ms", 0) or 0)
        if hold <= 0:
            return 450
        return int(max(200, min(600, hold * 0.45)))


def _robust(values) -> tuple[float, float]:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        raise ValueError("no samples")
    return float(np.median(arr)), float(np.percentile(np.abs(arr - np.median(arr)), 95))


def _longest_run_ms(frames: list[Features], above: list[bool]) -> float:
    """Duration of the longest unbroken stretch where ``above`` is true.

    This is "how long the person can actually hold this", as opposed to "how
    long the recording was", and it is the number a dwell has to fit inside.
    """
    best = 0.0
    start_t: int | None = None
    last_t: int | None = None
    for frame, hit in zip(frames, above, strict=True):
        if hit:
            if start_t is None:
                start_t = frame.t_ms
            last_t = frame.t_ms
        elif start_t is not None and last_t is not None:
            best = max(best, float(last_t - start_t))
            start_t = None
    if start_t is not None and last_t is not None:
        best = max(best, float(last_t - start_t))
    return best


def _signed_offset(values, direction: str):
    """Project roll offsets onto the configured direction, signed by "toward intent"."""
    if direction == "left":
        return values
    if direction == "right":
        return -values
    return np.abs(values)


def fit_tilt(rest: list[Features], move: list[Features], direction: str = "any") -> Suggestion:
    rest_roll = np.array([f.roll_deg for f in rest], dtype=float)
    move_roll = np.array([f.roll_deg for f in move], dtype=float)
    neutral, rest_band = _robust(rest_roll)
    noise = float(np.std(rest_roll)) or 0.5

    offset = move_roll - neutral
    if direction == "left":
        offset = offset[offset > 0]
    elif direction == "right":
        offset = np.abs(offset[offset < 0])
    else:
        offset = np.abs(offset)

    s = Suggestion(action="", kind="tilt")
    s.measurements = {
        "rest_median_deg": round(neutral, 2),
        "rest_band_deg": round(rest_band, 2),
        "rest_noise_deg": round(noise, 2),
        "samples_rest": int(rest_roll.size),
        "samples_move": int(offset.size),
    }

    if offset.size < 5:
        s.verdict = NOT_SEPARABLE
        s.notes.append(
            "the movement did not go in the configured direction; check `direction` "
            "in the config or try `--direction any`"
        )
        return s

    # p90, not the median: this is "the best this movement gets", not "what it
    # sustains most of the time". Somebody who can reach 14 degrees and hold it
    # for 600 ms inside a six-second capture is exactly who this is for, and a
    # median would read their movement as indistinguishable from rest and refuse
    # them -- the opposite of useful. How long they can hold it is measured
    # separately, below, and is what the dwell has to fit inside.
    hold = float(np.percentile(offset, 90))
    start = round(max(2.0, rest_band + 2.0 * noise), 1)
    full = round(max(start + 2.0, min(hold, start + 12.0)), 1)

    signed = _signed_offset(move_roll - neutral, direction)
    s.params = {"neutral_deg": round(neutral, 1), "start_deg": start, "full_deg": full}
    s.measurements["hold_deg"] = round(hold, 2)
    s.measurements["hold_ms"] = int(_longest_run_ms(move, [v >= start for v in signed]))

    if hold < start + 2.0:
        s.verdict = NOT_SEPARABLE
        s.notes.append(
            f"the movement reached {hold:.1f}deg above rest, but resting sway needs the "
            f"threshold at {start:.1f}deg. There is no band where the intent is "
            "distinguishable from rest with this movement."
        )
    elif hold < start + 4.0:
        s.verdict = MARGINAL
        s.notes.append(
            f"the movement only clears the threshold by {hold - start:.1f}deg; expect "
            "misses on tired days. A larger movement, or a longer dwell, would help"
        )

    s.notes.append(
        f"resting sway stays inside +/-{rest_band:.1f}deg, so `start_deg` sits at "
        f"{start:.1f}deg and the score saturates at {full:.1f}deg"
    )
    return s


def fit_blink(rest: list[Features], move: list[Features]) -> Suggestion:
    rest_ear = np.array([f.ear for f in rest], dtype=float)
    move_ear = np.array([f.ear for f in move], dtype=float)
    baseline = float(np.median(rest_ear))
    noise = float(np.std(rest_ear)) or 0.01

    s = Suggestion(action="", kind="blink")
    s.measurements = {
        "rest_ear_median": round(baseline, 3),
        "rest_ear_noise": round(noise, 4),
        "move_ear_median": round(float(np.median(move_ear)), 3),
        "samples_rest": int(rest_ear.size),
        "samples_move": int(move_ear.size),
    }

    if baseline <= 0:
        s.verdict = NOT_SEPARABLE
        s.notes.append("no usable open-eye baseline: were the eyes open during the rest phase?")
        return s

    closure = 1.0 - move_ear / baseline
    # Same reasoning as fit_tilt: the depth they reach, not the depth they can
    # sustain for most of the window.
    hold = float(np.percentile(closure, 90))
    s.measurements["hold_closure"] = round(hold, 3)

    if hold < 0.25:
        s.verdict = NOT_SEPARABLE
        s.notes.append(
            f"the eyes only closed to {hold * 100:.0f}% of baseline; a deliberate hold "
            "should exceed 40%. Either the movement was not held, or the camera lost the "
            "eye contours."
        )
        return s

    start = round(max(0.15, min(0.60, 0.45 * hold)), 2)
    full = round(min(0.95, max(start + 0.10, 0.85 * hold)), 2)
    s.params = {"start_ratio": start, "full_ratio": full}
    s.measurements["hold_ms"] = int(_longest_run_ms(move, [c >= start for c in closure]))
    if hold < 0.45:
        s.verdict = MARGINAL
        s.notes.append(
            "eye closure is shallow; a smaller face in frame or a lower camera will lose it"
        )
    s.notes.append(
        f"eyes rest at {baseline:.3f} and close to {hold * 100:.0f}% below that; "
        f"`start_ratio` sits at {start} so ordinary blinks stay below it"
    )
    return s


def fit(
    config: Config, action_name: str | None, rest: list[Features], move: list[Features]
) -> Suggestion:
    if not config.actions:
        raise ConfigError("no actions configured")
    action = None
    for candidate in config.actions:
        if action_name is None or candidate.name == action_name:
            action = candidate
            break
    if action is None:
        raise ConfigError(
            f"no action named {action_name!r}; available: {[a.name for a in config.actions]}"
        )

    if action.scorer == "tilt":
        s = fit_tilt(rest, move, str(action.scorer_params.get("direction", "any")))
    elif action.scorer == "blink":
        s = fit_blink(rest, move)
    else:
        raise ConfigError(f"scorer {action.scorer!r} has no fitting rule yet")
    s.action = action.name
    s.gate = _suggest_gate(action, s)
    return s


def _suggest_gate(action: ActionConfig, s: Suggestion) -> dict:
    """Lower the dwell when the measured hold cannot fill the configured one.

    Never raises it. A fit that lengthened the dwell would be a change nobody
    asked for, and the person has just demonstrated they can hold the shorter
    one; what must not happen is a dwell longer than the hold, which produces a
    configuration that is correct and can never fire.
    """
    if s.verdict == NOT_SEPARABLE or not s.measurements.get("hold_ms"):
        return {}
    suggested = s.min_dwell_ms
    if suggested >= action.gate.dwell_ms:
        return {}
    s.notes.append(
        f"the movement was held for {s.measurements['hold_ms']} ms, which cannot fill the "
        f"configured dwell of {action.gate.dwell_ms} ms -- dwell lowered to {suggested} ms"
    )
    return {"dwell_ms": suggested}


def verify(
    config: Config,
    action_name: str,
    params: dict,
    rest: list[Features],
    move: list[Features],
    gate: dict | None = None,
) -> tuple[bool, int]:
    """Replay the just-collected movement through the proposed thresholds.

    This is what makes the reported re-calibration time mean "a working
    configuration", rather than "a file that was written".
    """
    trial = copy.deepcopy(config)
    for action in trial.actions:
        if action.name == action_name:
            action.scorer_params = {**action.scorer_params, **params}
            if gate:
                action.gate = replace(action.gate, **gate)
    trial.validate()

    rebased = []
    t = 0
    for frame in list(rest) + list(move):
        rebased.append(
            Features(
                t_ms=t,
                roll_deg=frame.roll_deg,
                yaw=frame.yaw,
                pitch=frame.pitch,
                ear=frame.ear,
                brow_raise=frame.brow_raise,
            )
        )
        t += 33

    pipe = Pipeline(trial, NullSink(), AuditLog(None))
    pipe.run(iter(rebased))
    published = pipe.stats.published
    dwell = pipe.stats.dwell_samples[0] if pipe.stats.dwell_samples else 0
    return published >= 1, dwell


def write_session(path: Path, rest: list[Features], move: list[Features], gap_ms: int = 33) -> Path:
    """Write a calibration's two phases as one log that ``ito replay`` accepts.

    Relative timing inside each phase is preserved and the movement is offset
    past the rest phase, so replaying the file reproduces the session a
    threshold decision was made on. Without it, checking that decision later
    means asking the person to perform the movement again -- which is the thing
    recording exists to avoid, and what the handover note in GOVERNANCE.md
    assumes is possible.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    offset = 0
    with path.open("w", encoding="utf-8") as fh:
        for phase in (rest, move):
            if not phase:
                continue
            base = phase[0].t_ms
            for frame in phase:
                row = frame.as_dict()
                row.pop("t_ms", None)
                fh.write(json.dumps({"t_ms": offset + (frame.t_ms - base), "features": row}) + "\n")
            offset += (phase[-1].t_ms - base) + gap_ms
    return path


def write_config(
    config: Config,
    action_name: str,
    params: dict,
    out: Path,
    source: str | None,
    gate: dict | None = None,
) -> Path:
    """Write a complete config with the new thresholds.

    A full file rather than an in-place edit: the user's original is evidence of
    what was tried, and a calibration that silently rewrote it would make the
    next regression impossible to explain. Comments in the original are not
    preserved -- YAML does not carry them -- so the header says where this came
    from.
    """
    if source and Path(source).exists():
        raw = yaml.safe_load(Path(source).read_text(encoding="utf-8")) or {}
    else:
        raw = default_dict()

    found = False
    for item in raw.get("actions", []):
        if item.get("name") == action_name:
            item["scorer_params"] = {**(item.get("scorer_params") or {}), **params}
            if gate:
                item["gate"] = {**(item.get("gate") or {}), **gate}
            found = True
    if not found:
        raise ConfigError(
            f"action {action_name!r} is not present in {source or 'the default config'}"
        )

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# generated by `ito calibrate` -- thresholds fitted to one session\n"
        "# the file it was derived from keeps its own values; diff the two before adopting\n"
    )
    out.write_text(
        header + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return out
