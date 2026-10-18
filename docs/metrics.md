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
| publish | `publish_us`: with QoS 1 it returns after PUBACK; at QoS 0 it returns at enqueue |

**Report** median and p90 over at least 50 deliberate movements, plus the frame interval of the camera. On a 30 fps camera the frame quantisation floor alone is ~33 ms, and at 15 fps it is ~67 ms — say which one you used.

**Do not report the scorer's inference time as latency.** It is one row of the table.

**What the tool already reports.** Every published record carries `decision_us`, `publish_us` and `frame_to_publish_ms`, and the session summary prints the median and p90 of frame-to-publish, split into scoring/gate and publish. The heading of that line is the claim: `frame->PUBACK` at QoS 1, `frame->enqueue` at QoS 0, because QoS 0 has no acknowledgement to wait for. Reporting an enqueue as a PUBACK is the kind of quiet flattery this file exists to prevent, so if you need the acknowledged number, set `mqtt.qos: 1`. It deliberately does not claim to cover *onset → frame*: that interval is set by the camera and has to be measured as described above.

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

**The opposite error, which the log cannot see.** A decision log contains only commands that fired. Raising a threshold therefore improves every number in the file while making the device less usable, and no amount of reading the log will show that. So `ito review` ends with one more question — *how many times did you make the movement and nothing happened* — and reports missed commands as a count, a percentage of attempts, and a rate per hour next to the false-trigger rate. Two configurations are comparable only when both numbers are present. A blank answer means "not reported", never zero.

**What the tool already does.** Every run appends a session record carrying the exposure, and `ito review <log>` asks about each command one at a time and divides by that exposure. The asking cannot be automated: nobody has found a way to infer "the user did not mean that" from a camera, and a system that guesses about intent and then reports its own guess as accuracy is worse than a clipboard. The review must be answered by the user or their carer, not by whoever tuned the thresholds.

## 3. Re-calibration time

**Definition.** Seconds from the user deciding "my movement is not being recognised today" to the new threshold being in effect and verified by a successful command.

**How to measure.** Start a stopwatch at the moment the user says the words, not at the moment you open the config file. Stop it after a command succeeds.

Include: finding the file, understanding the two numbers, the edit, the restart, and the first good command. If it takes a maintainer 40 seconds because they know which line to edit, that is not the measurement — the measurement involves the user or their carer, alone.

**Target: ≤ 30 s**, without the maintainer. Today this is a hand edit of `start_deg` / `full_deg` in a YAML file, which is honest but does not meet the target. A guided re-fit is the milestone that closes it.

**What the tool already does.** `ito calibrate` prints its own elapsed time, measured from invocation to a configuration that has been *verified* by replaying the captured movement through it. Both halves matter: the time is only meaningful if the configuration at the end actually fires, so adopting the numbers without the verification would make the measurement a lie. It keeps the captured session next to the fitted config, which is how the same recording can be re-scored after a later change without the user performing anything. On `--source synthetic` the elapsed time is not a measurement and the command says so.

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
