# Intent-to-Object

**Turn the small voluntary movements a person still has into control signals for ordinary objects.**

A local-first adapter layer. A webcam watches for one deliberate movement — a head tilt, a held blink — and publishes an intent over MQTT. Your lamp, fan or curtain does not need to get smarter, and you do not need to buy an eye tracker.

```
webcam -> face landmarks -> pose normalisation -> geometric scorer -> dwell gate -> risk policy -> MQTT
```

Nothing leaves the machine. No account, no subscription, no cloud.

---

## Status: v0.2.1, working end to end, and it measures itself

| | |
|---|---|
| Perception | MediaPipe Face Mesh (478 landmarks), CPU, no GPU |
| Intent | geometric baseline: head roll, eye-aspect ratio, both with a dwell gate |
| Output | MQTT publish, or console only |
| Calibration | `ito calibrate` fits thresholds from 20 seconds of movement, lowers the dwell to what you can actually hold, and refuses when the movement is not separable from rest |
| Measurement | latency, false triggers per hour, missed commands, and re-calibration time are produced by the tool, not estimated by hand |
| Platforms | Python 3.10+ for the core; Python 3.10–3.12 for the camera |
| Users tested | **zero.** There are no human-subject numbers in this repository yet |

What is deliberately *not* here: multiple simultaneous gestures, a trained classifier, mobile app, nurses' station, or any support for locks, gas, beds and nurse-call. See [docs/safety.md](docs/safety.md).

---

## Quickstart — 30 seconds, no camera, no hardware

```bash
pip install -e .
ito demo
```

```text
intent-to-object 0.2.1  |  scripted session, 1 action(s): tilt_left
(no camera, no model -- this exercises the gate/policy/publish chain only)

  -> ito/intent/tilt_left  {"intent":"tilt_left","confidence":1.0,"held_ms":467,"ts_ms":3667,...}
  09:00:50  tilt_left held 467 ms at confidence 1.00 -> sent to ito/intent/tilt_left (low risk, no confirmation needed)

--- synthetic session, seed 7 ---
frames            451  (30.0 fps)   usable 437
duration          15.0 s
gates fired       2
published         2
dwells cancelled  1  (movement started, released too early)
```

That session is generated, not recorded. It exists so you can watch a command travel the whole chain on your first run, and so CI catches a regression in the chain — **it is not evidence that anything works on a person**, and the report says so every time.

## The three metrics, as commands

The project is judged on three numbers, so the tooling produces all three and states which ones it cannot.

```bash
ito calibrate                 # 20 s: fit thresholds, lower the dwell to what you can hold, verify
ito run --mqtt                # live; the summary carries frame->enqueue (qos 0) or frame->PUBACK (qos 1)
ito review logs/audit.jsonl   # mark each command intended/unintended, count the misses -> two rates
```

`calibrate` will **not** write thresholds when the movement cannot be separated from resting sway. That refusal is the point: a quietly written bad threshold is worse than no threshold, because nobody finds out until it misfires at 3 a.m. It also lowers the dwell when the measured hold cannot fill the configured one — a threshold you can reach but cannot hold is the same failure wearing a better disguise. And it keeps the session it measured (`--session`, on by default for the camera), so a later threshold change can be checked with `ito replay` instead of asking the person to perform the movement again.

`review` needs a person to answer one question per command, plus one question for the whole session: how many times did you make the movement and nothing happened. It computes both rates against the exposure recorded in the session, because a count without a denominator is not a measurement — and because a false-trigger rate reported alone rewards a gate that has simply become too hard to satisfy. Protocols for running this properly are in [docs/metrics.md](docs/metrics.md), including what still has to be measured by hand: the interval between the movement itself and a frame, which is the camera's, not this project's.

## With a camera

MediaPipe has no wheels for Python 3.13+ yet, so use 3.10–3.12:

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[camera]"
ito doctor            # tells you what is missing, if anything
ito run --show        # dry run: prints and logs intents, publishes nothing
```

`ito run` without `--mqtt` never sends anything. Publishing is opt-in, because the first time you point this at a real lamp you want to have watched it for a while first.

## With a real object

```bash
ito run --mqtt                  # publish to the broker in your config
ito run --mqtt --confirm        # also ask a human before any medium-risk action
```

Any MQTT subscriber is a valid output. A common one:

```yaml
# Home Assistant -> automations.yaml
- alias: "Lamp follows head tilt"
  trigger:
    - platform: mqtt
      topic: ito/intent/tilt_left
  action:
    - service: light.toggle
      target: { entity_id: light.bedside }
```

For a lamp that is not smart, put an ESP32 running an infrared-bridge firmware (ir2mqtt, OMOTE) or [OpenMQTTGateway](https://github.com/1technophile/OpenMQTTGateway) in front of it and let it subscribe to the same topic. This project writes no infrared or Bluetooth stack of its own — see [docs/hardware.md](docs/hardware.md).

---

## Why this exists

Eye trackers and switch interfaces work, but they cost hundreds and each one is locked to a single function. Smart-home ecosystems work, but they assume hands. Robotics frameworks assume the goal is a robot. The person who can still tilt their head 12 degrees is served by none of them.

The gap is not perception (Project Gameface and others proved "head movement to cursor" years ago) and not actuation (ir2mqtt and Home Assistant are mature). The gap is the thin, auditable, replaceable layer in between: personal calibration → intent → a bounded physical action → a log somebody else can read.

## Design decisions worth arguing about

**One movement, not five.** A reliable single switch beats four half-reliable gestures, because the user pays for every false trigger with trust. The default config ships one action.

**Geometric baseline before machine learning.** Head tilt is an angle; a held blink is eye-aspect ratio against a personal baseline. Two scalars, a deadband and a dwell timer handle both, and when they fail you can see why by looking at the camera. A few-shot prototype matcher is planned for people whose motion no threshold separates from their rest — it will be benchmarked against these numbers, not assumed to beat them. `ito.scorers.Scorer` is the seam where it plugs in.

**A dwell gate, plus a release requirement.** Firing once and then refusing to fire again until the signal drops is what makes the thing usable by someone who cannot make a quick movement. Holding for four seconds produces one command, not twelve — and "drops" means *stays below the release threshold for 150 ms*, because a held posture dips through that boundary by noise alone.

**Risk tiers in the policy, not in the docs.** Low risk publishes. Medium risk asks a human first. High risk is refused when the config loads, with an explanation — so the refusal is impossible to miss and impossible to enable by typo.

**The audit log is a feature, not logging.** Every decision is written with the numbers behind it and can be rendered as a sentence: `14:02:11 tilt_left held 467 ms at confidence 0.81 -> sent to ito/intent/tilt_left`. The acceptance test is that a care worker can read a log without reading code.

## The three metrics this project is judged on

1. **End-to-end latency** — movement onset to MQTT PUBACK. The software's share of it is measured by every run; the movement-to-frame share is the camera's. At qos 0 there is no PUBACK by protocol, so the tool reports frame→enqueue and labels it that way rather than quietly relabelling an enqueue as a delivery.
2. **False-trigger rate** — commands per hour of defined activity, reported next to the movement amplitude it demanded.
3. **Re-calibration time** — seconds from "my movement is different today" to a verified working threshold, target ≤ 30 s.

Protocols, denominators and a reporting template are in [docs/metrics.md](docs/metrics.md). None of the three has been measured on a user yet; that is the next milestone, and it is stated as an absence rather than a claim.

## Documentation

- [docs/design.md](docs/design.md) — the design, and what changed from the first proposal
- [docs/safety.md](docs/safety.md) — risk tiers, exclusions, failure behaviour, data handling
- [docs/metrics.md](docs/metrics.md) — how to measure the three numbers honestly
- [docs/hardware.md](docs/hardware.md) — MQTT contract, wiring an ESP32, what it costs
- [GOVERNANCE.md](GOVERNANCE.md) — how this survives its first maintainer
- [CHANGELOG.md](CHANGELOG.md) — what changed, and why
- [README.zh-CN.md](README.zh-CN.md) — 中文说明

## Layout

```
src/ito/
  geometry.py    landmark indices, aspect correction, pose normalisation, features
  scorers/       Scorer protocol + geometric baseline (tilt, blink)
  gate.py        dwell / hysteresis / release state machine
  policy.py      risk tiers, confirmation, rate limit
  pipeline.py    wiring, timing and the session record
  calibrate.py   guided re-fit, and its own verification
  review.py      intended/unintended marking -> false triggers per hour
  sources.py     camera, replay, scripted -- the only file that imports MediaPipe
  sinks.py       console, MQTT, payload templating
  audit.py       decision log + human-readable rendering
  report.py      session summary
  cli.py         doctor / demo / run / calibrate / review / record / replay / explain / config / selftest
```

Only `sources.py` touches MediaPipe and only `sinks.py` touches paho, which is why `pytest` runs green on a machine with no camera and no broker.

---

## Safety

This is **not a medical device**, is not life-safety equipment, and is not a substitute for a nurse-call system. It is built for low-risk conveniences — a lamp, a fan, a television — and it refuses to control anything else. Read [docs/safety.md](docs/safety.md) before pointing it at something that matters.

## License

MIT for code, CC BY 4.0 for documentation. No warranty, no liability — see [LICENSE](LICENSE).
