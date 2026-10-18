# Changelog

## 0.3.0

The theme: **the first run is one command and one question.** Everything in
0.2.1 made the tool honest; none of it made the tool reachable by the person it
is for. This release is about the distance between "there is a repository" and
"a carer is running it".

### Added

- **`ito setup`.** From an installed package to a calibrated `config.yaml`:
  checks the machine, asks which movement will be used (tilt left, tilt right,
  or eyes held closed — in those words, not scorer names), records rest and
  movement, fits, verifies that the fitted thresholds actually fire, and writes
  the file with a comment per number. It replaces a sequence of five commands
  and one YAML edit, which was defensible step by step and absurd as a whole.
- **`config.yaml` in the working directory is discovered automatically**, so the
  file `setup` just wrote is the file `run` reads. Before this, `setup` would
  have written a config that `ito run` silently ignored in favour of the
  built-in defaults — the exact "looks calibrated, is not" failure this project
  keeps arguing against.
- The generated config carries a one-line explanation of every threshold, so the
  person who edits it in a month does not have to open the documentation.

### Changed

- `calibrate` and `setup` share one capture path, so the sentence a person hears
  before each phase exists in one place.
- Piped output is line-buffered. A transcript pasted into a message used to show
  the error before the banner that explained what was running.

## 0.2.1

Everything here came out of an adversarial read of 0.2 rather than from a
feature list. The theme: **the tool was making claims it had not implemented,
in a project whose whole argument is that it does not do that.** Each entry
names the claim that was false.

### Fixed

- **The version number was 0.1.0 in the code and 0.2.0 everywhere else.** The
  README's own pasted transcript could not be reproduced by running the command
  it documents. `__version__` now comes from package metadata with a fallback,
  and `tests/test_version.py` fails if pyproject, the fallback and the README
  disagree.
- **An unreachable broker produced a traceback.** `cli.main` catches config and
  source errors, not `OSError`, so the first run with a forgotten `mosquitto -d`
  printed a stack trace — in a CLI whose own opening paragraph says every command
  either works or tells you what is missing. It is now a sentence with the
  host, the port and the next step.
- **The latency metric measured an enqueue and called it a PUBACK.** At the
  default `qos: 0` there is no acknowledgement in the protocol, and
  `publish()` returned without checking even the client-side return code. With
  `qos >= 1` the sink now waits for PUBACK and a failed publish raises; the
  session summary labels the line `frame->enqueue` or `frame->PUBACK` according
  to what was actually measured.
- **`ito explain` rendered the end-of-session record as a command.** It came out
  as `unknown held 0 ms at confidence 0.00 -> declined ()`, and added an `?` to
  the declined-by-reason table — on the one surface a care worker is meant to
  read. Session records now render as one summary sentence.
- **`ito calibrate` never touched the dwell.** `Suggestion.min_dwell_ms` and
  `hold_ms()` existed and were called by nothing, so a person who can hold for
  300 ms received thresholds they could reach and a 450 ms dwell they could
  never fill: correct, verified against the wrong criterion, and permanently
  silent. The fit now measures the longest continuous hold above threshold and
  lowers `dwell_ms` when it cannot fill the configured one. It never raises it.
- **The gate re-armed on a single noisy frame.** After the cooldown, one frame
  below `off` was enough, so a held posture that dipped through the boundary by
  noise could fire twice. `gate.release_ms` (150 ms by default) requires the
  release to be sustained.
- **Misspelt keys in four config sections were silently ignored.** `mqtt.hots`
  fell back to localhost while the loader's docstring promised that config
  errors are louder than runtime errors. `camera`, `perception`, `mqtt` and
  `output` are now checked like `policy` and `actions`, and `qos`/`port` are
  validated.
- **Losing the face was invisible.** The source counted missing frames into a
  variable nothing read, and a session where the person had walked away was
  indistinguishable from a working one. Gaps between usable frames longer than
  `perception.face_lost_warn_s` are now warned about live and counted in the
  session summary (`face_gaps`, `max_gap_ms`).
- **A log with a half-written last line raised a JSON traceback.** The log is
  flushed per record, so a crash leaves one truncated line and `ito explain`
  died with a decoder error. `explain`, `review` and `replay` now name the file
  and line, and say that the earlier records are still readable.

### Added

- **`ito review` asks about the opposite error.** The log can only contain
  commands that fired, so every threshold change that reduces false triggers
  also increases silent misses — and nothing in the tool showed the second
  effect. One extra question per session produces `missed`, `miss_rate_pct` and
  `missed_per_hour` next to the false-trigger rate. Blank means "not reported",
  never zero.
- **`ito calibrate` keeps what it captured.** It writes the session next to the
  fitted config (gitignored) and prints the `ito replay` command, so a threshold
  decision can be re-examined later without asking the person to perform the
  movement again. GOVERNANCE.md already assumed this was possible; it was not.
- **`ito run --record`** keeps the landmarks of a live session, because the
  command that arrived at 3 a.m. is the one that cannot be reproduced on
  demand. `ito replay` then re-runs it through the current thresholds.
- A warning when two actions publish to the same topic: the payload still says
  which action fired, but a subscriber that switches on the topic alone will
  treat them as one.
- A warning when `mqtt.username` is set, because the config is plain text and
  `docs/safety.md` asks people to paste it into issues.
- `tests/test_cli.py`, and `tests/test_sinks.py` now drives `MqttSink` through a
  fake paho: the command surface and the transport contract were the two places
  the bugs above actually lived, and neither had a test.

### Changed

- `*.review.json`, `*.calibrated.yaml` are gitignored. The review file is a
  per-command human judgement about a person's intent; it should not be one
  `git add -A` away from a public repository.
- `mediapipe` is pinned `<0.11`, matching what `ito doctor` already told people
  to do. The packaging and the diagnostic disagreed about the supported range.
- Formatting is now enforced in CI (`ruff format --check`), and the test matrix
  includes 3.11 instead of skipping it.
- `mypy` runs in CI and is clean on `src/`, and `pytest` has a 75% coverage
  floor (78% today). Both are about the same thing: the decision path is the
  product, and it should not be possible to lose its tests without noticing.

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
