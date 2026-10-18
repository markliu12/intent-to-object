# Session report — TEMPLATE

Copy this file to `docs/studies/YYYY-MM-DD-<participant-code>.md`.
Use a code, never a name, and never attach video or a landmark recording.

## Setup

- **Date:**
- **Participant code:** `P01`
- **Description of the participant's movement, in their own terms:**
  (e.g. "can tilt head ~15° to the left, holds 2–3 s, tires after about ten")
- **Hardware:** camera model, machine, CPU
- **Software:** `ito --version`, config path, commit hash
- **Movement:** head tilt, direction, `start_deg` / `full_deg`
- **Gate:** `dwell_ms`, `cooldown_ms`
- **Camera frame interval:** ms (from `ito doctor` or the session summary)

## Results

| Metric | Value |
|---|---|
| frame→publish latency, median / p90 (ms) | |
| movement onset → frame interval (ms, measured with the LED or key-tap method) | |
| false triggers per hour — watching television (≥ 60 min) | |
| false triggers per hour — conversation (≥ 30 min) | |
| false triggers per hour — resting (≥ 30 min) | |
| missed commands per 20 attempts | |
| movement amplitude required (deg or closure ratio) | |
| participant fatigue rating of that movement (1–5) | |
| re-calibration time (s), unaided | |

## What surprised us

Include the failures. A session with no surprises is usually a session that was
not measured carefully enough.

## What we changed afterwards

And what we would have needed to measure to know whether the change helped.
