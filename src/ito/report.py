"""Session reports.

The three MVP metrics are latency, false-trigger rate and re-calibration time.
A report is only honest if it says what the denominator was, so every rate here
is printed with its exposure: commands per hour, cancellations per thousand
frames. A raw count of "false triggers" without an exposure time is not a
measurement.
"""

from __future__ import annotations

import numpy as np

from .config import Config
from .types import SessionStats


def _pct(values, q: float) -> float:
    if not len(values):
        return 0.0
    return float(np.percentile(values, q))


def format_summary(
    stats: SessionStats,
    config: Config | None = None,
    *,
    label: str = "session",
    fps: float | None = None,
) -> str:
    lines = []
    duration = stats.uptime_s()
    hours = duration / 3600.0 if duration > 0 else 0.0

    lines.append(f"--- {label} ---")
    lines.append(
        f"frames            {stats.frames}"
        + (f"  ({fps:.1f} fps)" if fps else "")
        + f"   usable {stats.frames_scored}"
    )
    lines.append(f"duration          {duration:.1f} s")
    lines.append(f"gates fired       {stats.fired}")
    lines.append(f"published         {stats.published}")
    lines.append(f"blocked by policy {stats.blocked}")
    lines.append(
        f"dwells cancelled  {stats.cancelled_dwells}"
        "  (movement started, released too early)"
    )

    if hours > 0:
        if duration >= 60:
            lines.append(
                f"publish rate      {stats.published / hours:.1f} /hour"
                f"   over {duration:.0f}s of exposure"
            )
        else:
            lines.append(
                f"publish rate      not reported: {duration:.0f}s of exposure is too "
                "short to quote a rate"
            )
        if stats.frames:
            per_k = 1000.0 * stats.cancelled_dwells / stats.frames
            lines.append(f"cancel rate       {per_k:.1f} /1000 frames")

    if stats.dwell_samples:
        d = np.asarray(stats.dwell_samples, dtype=float)
        lines.append(
            f"hold time         median {np.median(d):.0f} ms"
            f"   p90 {_pct(d, 90):.0f} ms   max {d.max():.0f} ms"
        )
    if stats.frame_to_publish_ms:
        lat = np.asarray(stats.frame_to_publish_ms, dtype=float)
        lines.append(
            f"frame->publish    median {np.median(lat):.1f} ms"
            f"   p90 {_pct(lat, 90):.1f} ms   n={lat.size}"
        )
        if stats.decision_us and stats.publish_us:
            decision = np.median(np.asarray(stats.decision_us)) / 1000.0
            publish = np.median(np.asarray(stats.publish_us)) / 1000.0
            lines.append(
                f"  of which        scoring+gate {decision:.2f} ms, publish {publish:.2f} ms,"
                " rest is landmark extraction"
            )
        lines.append(
            "                  this is software latency only: the time from the movement"
            " itself to a frame is set by the camera interval"
        )
    else:
        lines.append("frame->publish    not measurable: this source has no camera timestamp")
    if stats.baseline_max > 0:
        lines.append(
            f"ear baseline      {stats.baseline_min:.3f} .. {stats.baseline_max:.3f}"
        )

    if stats.per_action:
        lines.append("")
        lines.append(f"{'action':<18}{'fired':>7}{'sent':>7}{'cancelled':>11}")
        for name, e in stats.per_action.items():
            lines.append(
                f"{name:<18}{e['fired']:>7}{e['published']:>7}{e['cancelled']:>11}"
            )

    if config and config.warnings:
        lines.append("")
        lines.append("config warnings:")
        for w in config.warnings:
            lines.append(f"  ! {w}")

    lines.append("")
    lines.append(
        "not a user measurement  |  synthetic and replayed sessions exercise the "
        "chain; they say nothing about accuracy on a person"
    )
    return "\n".join(lines)
