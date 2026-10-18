"""Append-only decision log, and a renderer that makes it readable by humans.

The log exists for two audiences. Machines read the JSONL to compute rates and
to reconstruct why something happened. Care workers read the rendered lines, and
the stage-3 acceptance criterion is that they can do so without reading code --
which is why every record carries the numbers behind the decision, not just the
decision.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import IO, Any

RISK_NOTE = {
    "low": "low risk, no confirmation needed",
    "medium": "medium risk, confirmation required",
    "high": "high risk, unsupported by design",
}


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._fh: IO[str] | None = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")

    def write(self, record: dict[str, Any]) -> dict[str, Any]:
        record = dict(record)
        record.setdefault("ts", datetime.now().isoformat(timespec="milliseconds"))
        if self._fh:
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()
        return record

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> AuditLog:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def is_session(record: dict[str, Any]) -> bool:
    """True for the end-of-session summary, which is a record but not an event."""
    return record.get("kind") == "session"


def render_session(record: dict[str, Any]) -> str:
    """The session summary as a sentence, so it is not rendered as a command.

    Before this existed, ``ito explain`` printed the summary through
    ``render_record`` and produced a line that read like a declined command
    called "unknown" -- on the one surface a care worker is supposed to read.
    """
    duration = float(record.get("duration_s", 0.0) or 0.0)
    gaps = int(record.get("face_gaps", 0) or 0)
    text = (
        f"session ended  {duration:.1f}s, {record.get('frames', 0)} frames "
        f"({record.get('frames_scored', 0)} usable), {record.get('published', 0)} sent, "
        f"{record.get('blocked', 0)} blocked, "
        f"{record.get('cancelled_dwells', 0)} cancelled"
    )
    if gaps:
        text += f", face lost {gaps}x (longest {int(record.get('max_gap_ms', 0)) / 1000.0:.1f}s)"
    return text


def render_record(record: dict[str, Any]) -> str:
    """One record as a sentence a non-programmer can act on."""
    ts = str(record.get("ts", ""))[11:19] or "??:??:??"
    action = record.get("action", "unknown")
    score = float(record.get("score", 0.0))
    dwell = int(record.get("dwell_ms", 0))
    tier = record.get("tier", "low")
    allowed = record.get("allowed", False)
    reason = record.get("reason", "")

    if allowed:
        dest = record.get("topic") or "no topic"
        confirmed = record.get("confirmed")
        extra = "confirmed by a person" if confirmed else RISK_NOTE.get(tier, tier)
        return (
            f"{ts}  {action} held {dwell} ms at confidence {score:.2f} -> sent to {dest} ({extra})"
        )

    if reason == "not_confirmed":
        return (
            f"{ts}  {action} held {dwell} ms at confidence {score:.2f}"
            " -> declined (nobody confirmed)"
        )
    if reason == "rate_limited":
        return f"{ts}  {action} -> declined (too many commands per minute, possible stuck input)"
    if reason == "tier_high_unsupported":
        return f"{ts}  {action} -> refused (high-risk action, not supported)"
    return f"{ts}  {action} held {dwell} ms at confidence {score:.2f} -> declined ({reason})"


class AuditLogError(RuntimeError):
    """A log that cannot be trusted to describe what happened."""


def read_jsonl(path: Path):
    """Read records, naming the line that is broken.

    A log is appended to and flushed per record, so a crash or a full disk
    leaves a half-written last line. That is expected; a traceback out of
    `ito explain` is not -- the person reading the log is a care worker, and
    "line 1431 is not valid JSON, the log was probably cut off mid-write" is
    something they can act on.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise AuditLogError(
                    f"{path}:{lineno}: this line is not valid JSON. The file was probably "
                    "cut off mid-write; the records before it are still readable with a "
                    "text editor."
                ) from exc
