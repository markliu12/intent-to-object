"""Marking a session's commands, and turning that into a rate.

The second MVP metric is "false triggers per hour". A count is not a metric: a
log with six lines does not say whether six commands in a minute or in a week.
The session record written at the end of every run carries the exposure, and
this module divides by it.

The marking itself is deliberately human. Nobody has found a way to detect
"the user did not mean that" from a camera, and a system that guesses wrong
about intent and then reports its own guess as accuracy is worse than a
stopwatch and a carer.

The log only contains commands that fired, so the tool asks the opposite
question too: how many deliberate attempts produced nothing. A false-trigger
rate reported without it rewards a gate that is simply too hard to satisfy,
which is the regression [docs/metrics.md](../../docs/metrics.md) warns about.
"""

from __future__ import annotations

import json
from pathlib import Path

from .audit import read_jsonl, render_record

INTENDED = "intended"
UNINTENDED = "unintended"


def _ask_command(record: dict, index: int, total: int) -> bool:
    prompt = f"  [{index}/{total}] {render_record(record)}\n      was this intended? [Y/n/q] "
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        answer = "q"
    if answer == "q":
        raise KeyboardInterrupt
    return answer not in ("n", "no")


def ask_missed_count() -> int | None:
    """Ask how many deliberate attempts produced nothing at all.

    The log can only contain commands that fired. Without this question the
    project measures one direction of the error and optimises it: every change
    that makes the gate harder lowers the false-trigger rate and raises the
    number of times a person moved and got no answer, and nothing in the tool
    would show the second effect. An empty answer is "not reported", not zero.
    """
    prompt = (
        "\n  How many times did you make the movement and nothing happened?\n"
        "  (missed commands -- blank = not reported) "
    )
    try:
        answer = input(prompt).strip()
    except EOFError:
        return None
    if not answer:
        return None
    try:
        return max(0, int(answer))
    except ValueError:
        return None


def review(
    path: Path,
    ask=_ask_command,
    log=print,
    ask_missed=None,
) -> dict:
    """Walk a decision log, asking about each command. Returns the report."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no such log: {path}")
    records = list(read_jsonl(path))
    events = [r for r in records if r.get("action")]
    session = next((r for r in reversed(records) if r.get("kind") == "session"), None)

    if not events:
        raise ValueError(f"{path} contains no commands to review")

    log(f"reviewing {len(events)} command(s) from {path}")
    if session is None:
        log(
            "! no session record in this log (written by an older version), so the\n"
            "! exposure is unknown and the rate below is an upper bound only"
        )

    marks = []
    unintended = 0
    for i, record in enumerate(events, start=1):
        intended = ask(record, i, len(events))
        marks.append(
            {
                "t_ms": record.get("t_ms"),
                "action": record.get("action"),
                "score": record.get("score"),
                "dwell_ms": record.get("dwell_ms"),
                "allowed": record.get("allowed"),
                "verdict": INTENDED if intended else UNINTENDED,
            }
        )
        if not intended:
            unintended += 1

    exposure_s = None
    exposure_source = "unknown"
    if session and session.get("duration_s"):
        exposure_s = float(session["duration_s"])
        exposure_source = "session record"
    elif len(events) > 1:
        exposure_s = max(0.0, (events[-1].get("t_ms", 0) - events[0].get("t_ms", 0)) / 1000.0)
        exposure_source = "span between first and last command (upper bound)"

    rate = None
    if exposure_s and exposure_s > 0:
        rate = unintended / (exposure_s / 3600.0)

    # The other half of the error: attempts that produced no command at all.
    missed = ask_missed() if ask_missed else None
    attempts = None if missed is None else len(events) + missed
    miss_rate = (
        None if attempts in (None, 0) or missed is None else round(100.0 * missed / attempts, 1)
    )
    missed_per_hour = (
        None
        if missed is None or not exposure_s or exposure_s <= 0
        else round(missed / (exposure_s / 3600.0), 2)
    )

    report = {
        "log": str(path),
        "commands": len(events),
        "unintended": unintended,
        "missed": missed,
        "attempts": attempts,
        "miss_rate_pct": miss_rate,
        "missed_per_hour": missed_per_hour,
        "exposure_s": exposure_s,
        "exposure_source": exposure_source,
        "false_triggers_per_hour": None if rate is None else round(rate, 2),
        "marks": marks,
    }
    out = path.with_suffix(path.suffix + ".review.json")
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    log("")
    log(f"commands          {len(events)}")
    log(f"unintended        {unintended}")
    if exposure_s:
        log(f"exposure          {exposure_s / 60.0:.1f} min  ({exposure_source})")
    if rate is not None:
        log(f"false triggers    {rate:.1f} /hour")
    else:
        log("false triggers    not computable: no exposure in this log")
    if missed is None:
        log("missed commands   not reported -- the false-trigger rate alone cannot say")
        log("                  whether the gate has become too hard to satisfy")
    else:
        log(f"missed commands   {missed} of {attempts} attempts  ({miss_rate:.1f}%)")
        if missed_per_hour is not None:
            log(f"missed rate       {missed_per_hour:.1f} /hour")
        log("                  report this next to the false-trigger rate: a config that")
        log("                  trades one for the other is a regression, not an improvement")
    log("")
    log(f"written to {out}")
    log("self-report: this number is only as good as the person answering, and it is")
    log("the user or their carer who must answer -- not the person who tuned the system.")
    return report
