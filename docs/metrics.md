# Measuring the three numbers

This project is judged on three measurements, taken on people who are not its authors. This file exists so that the numbers are comparable between sessions and cannot be quietly flattered.

A number without a denominator, an exposure duration and a description of the movement demanded is not a measurement. It is a claim.

## 1. End-to-end latency

**Definition.** Time from the *onset of the movement* to the broker acknowledging the publish.

Not the time from "the camera happened to capture a frame that already showed the movement", and not the model's inference time. Those are the two numbers that get quoted and neither one is what a user experiences.

**How to measure**

Mark onset with something you can detect in the video, not with a human stopwatch:

- Tap a key on the same machine at the moment you begin the movement; the CLI records the key timestamp.
- Or use a rig with a visible cue in frame (an LED that lights with the movement cue) and read the frame time of first change.

Then:

| Segment | Instrumentation |
|---|---|
| capture + landmark | frame timestamp at `cv2.read()` vs. `Features.t_ms` |
| scoring + gate | already in `Fire.t_ms` and `dwell_ms` |
| publish | wall clock at `client.publish()`; PUBACK for QoS 1 |

**Report** median and p90 over at least 50 deliberate movements, plus the frame interval of the camera. On a 30 fps camera the frame quantisation floor alone is ~33 ms, and at 15 fps it is ~67 ms — say which one you used.

**Do not report the scorer's inference time as latency.** It is one row of the table.

## 2. False-trigger rate

**Definition.** Publications that the user did not intend, per hour, during a named activity.

**Why per hour and not per session.** "Three false triggers" is unfalsifiable. Three in an hour of television is a broken product; three in a week of use is a working one. The denominator is the measurement.

**Protocol.** One session per activity, at least 60 minutes each, with the user doing what they would normally do:

| Activity | Session length | What counts as a false trigger |
|---|---|---|
| Watching television | ≥ 60 min | any publication the user says they did not intend |
| Conversation, seated | ≥ 30 min | as above — head turns while talking are the hard case |
| Resting / reading | ≥ 30 min | as above |
| Deliberate use | ≥ 20 commands | missed commands (the opposite failure) |

**Report three numbers together**, never one alone:

1. false triggers per hour, per activity;
2. the *amplitude* of the movement the system requires — the degrees of roll, or the closure ratio, at the trigger threshold;
3. the user's own rating of how tiring that movement is, 1–5.

A configuration that halves the false-trigger rate by doubling the required movement is a regression for anyone with a progressive condition. Say so when you report it, even if the first number looks better.

## 3. Re-calibration time

**Definition.** Seconds from the user deciding "my movement is not being recognised today" to the new threshold being in effect and verified by a successful command.

**How to measure.** Start a stopwatch at the moment the user says the words, not at the moment you open the config file. Stop it after a command succeeds.

Include: finding the file, understanding the two numbers, the edit, the restart, and the first good command. If it takes a maintainer 40 seconds because they know which line to edit, that is not the measurement — the measurement involves the user or their carer, alone.

**Target: ≤ 30 s**, without the maintainer. Today this is a hand edit of `start_deg` / `full_deg` in a YAML file, which is honest but does not meet the target. A guided re-fit is the milestone that closes it.

## Reporting template

Copy this into an issue or a `docs/studies/<date>.md`:

```markdown
### Session — <date>, <participant code, not a name>

- Hardware: <camera, machine, CPU>
- Software: ito <version>, config <commit or paste>
- Movement: head tilt, direction <>, threshold <start_deg>/<full_deg>
- Dwell: <ms>; cooldown: <ms>
- Camera frame interval: <ms>

| Metric | Value |
|---|---|
| latency median / p90 (ms) | |
| false triggers per hour — television | |
| false triggers per hour — conversation | |
| false triggers per hour — rest | |
| missed commands per 20 attempts | |
| movement amplitude required (deg) | |
| user fatigue rating (1–5) | |
| re-calibration time (s) | |

Notes on anything that surprised you. Include failures.
```

## What not to report

- Numbers from `ito demo`. It is scripted; its statistics describe the script.
- Numbers from able-bodied authors as if they described the target population. Label them as a smoke test.
- Accuracy percentages on synthetic data.
- Anything measured after the participant has been asked to perform the movement repeatedly until it "works" — that measures the practice effect, not the system.
