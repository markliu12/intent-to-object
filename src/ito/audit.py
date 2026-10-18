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


def read_jsonl(path: Path):
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
