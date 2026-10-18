# Changelog

## 0.2.0

The theme: the three MVP metrics existed only as documentation. Now the code
produces all three, and says which ones it cannot.

### Fixed

- **Medium-risk actions could never publish.** `policy.allow_medium` existed in
  the config but nothing ever constructed a confirmer, so every medium-risk
  command was silently declined while the README described a confirmation
  prompt. Confirmation is now `ito run --confirm`, and the key is gone from the
  config schema (old files get an error that says so).

### Added

- `ito calibrate` — a 20-second guided re-fit. Records rest and movement,
  suggests `start_deg` / `full_deg` (or the blink ratios), **refuses to write
  anything when the movement cannot be separated from rest**, and replays the
  captured movement through the proposed thresholds to prove they fire. Prints
  its own re-calibration time, which is the third MVP metric.
- `ito review` — walks a decision log asking whether each command was intended,
  and reports false triggers **per hour** using the exposure from the session
  record. A count without a denominator is not a measurement; this is where the
  denominator comes from.
- Every run now appends a `kind: "session"` record: frames, duration, publishes,
  cancellations. That record is what makes `ito review` able to divide by time.
- Latency instrumentation: the camera stamps each frame, and every published
  record carries `decision_us`, `publish_us` and `frame_to_publish_ms`. The
  session summary reports the median and p90 of frame-to-publish, split into
  scoring and publish, and labels it as *software latency* — the movement-to-frame
  interval is the camera's, not this project's.
- `ito run` announces medium-risk actions and whether a person is available to
  confirm them.

### Changed

- `requires-python` is now `>=3.10`. 3.9 is out of support and MediaPipe never
  ran on it; the floor was fiction.
- Added `ruff` (config in `pyproject.toml`) and a CI lint step. 74 mechanical
  fixes, no behaviour change.

## 0.1.0

First working slice: camera → landmarks → pose-normalised features → geometric
scorer → dwell gate → risk policy → MQTT, with the whole decision path testable
on a machine that has no camera, no broker and no MediaPipe.
