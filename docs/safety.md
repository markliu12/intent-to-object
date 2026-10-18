# Safety boundary

This document is not boilerplate. It is the reason the project is allowed to exist in public with a permissive licence, and the reason the code has a `risk` field that can refuse to start.

## What this software is

A convenience layer for **low-risk** actuators. It is **not** a medical device, **not** life-safety equipment, **not** an alarm, and **not** a substitute for a nurse-call system, a fall detector, or a person. It is published under a licence that disclaims all liability, and that disclaimer is doing real work rather than decorating the bottom of a file.

Do not use it where a missed command or a spurious command could cause injury. If you are unsure whether your use case is in that category, it is.

## Risk tiers

| Tier | Behaviour in the code | Examples | Rationale |
|---|---|---|---|
| **low** | publishes immediately | lamp, fan, television, music | a misfire costs an annoyance |
| **medium** | requires a human confirmation before publishing | curtain, input switching, a reminder chime | a misfire costs money, comfort or trust |
| **high** | **refused at config load** | door lock, gas valve, medical bed, nurse call, anything that moves a person | a misfire can hurt someone |

`ito` will not start with a high-risk action in the config. The error message says this is deliberate, because "unsupported" and "not yet implemented" are very different promises and only one of them will still be true in a year.

```text
error: action 'unlock_door' is marked high risk. High-risk outputs (locks, gas,
medical beds, nurse call) are unsupported by design; this is not a missing
feature. See docs/safety.md.
```

If you fork this and re-enable high-risk outputs, you are no longer running this project, and the safety analysis in this file no longer applies to what you built.

## Failure behaviour

The system fails **closed**: an intent that is not published does nothing.

| Failure | What happens |
|---|---|
| camera unplugged / covered | no landmarks, no features, no publications; the process logs nothing and keeps waiting |
| face lost for a moment | frames are skipped, the dwell state machine is unaffected |
| laptop sleeps | the process is suspended with the OS; nothing is sent while suspended |
| MQTT broker down | `--mqtt` fails loudly at startup; it does not silently continue without publishing |
| process crashes | no publications, by construction — there is no daemon watching it |
| sensor stuck at a constant value | the dwell gate still fires once and then requires a release, so the rate cannot run away; the rate limit is the second backstop |
| user holds the movement | one command, not a stream |

There is no watchdog and no remote kill switch. That is a limitation, not an oversight: a remote kill switch is another network surface and another thing to secure. If you need one, put it in the broker and the actuator, which are the components you already trust.

## Confidence and false triggers

Low confidence produces **no** output rather than a guess. That is a deliberate trade: the system will sometimes be unresponsive, and that failure is recoverable, whereas an unexplained lamp turning on at 3 a.m. is not.

The false-trigger rate is measured with an exposure denominator and reported next to the movement amplitude it demanded ([docs/metrics.md](metrics.md)). Any tuning that lowers the false-trigger rate by requiring bigger, more tiring movements is treated as a regression, not an improvement — fatigue is a safety issue for the people this is built for.

## Data handling

- **Video frames are never written to disk.** They exist in memory for the duration of a frame.
- **`ito record` writes face landmarks, not images.** They are still personal data: a landmark file is a measurement of a specific person's face. Treat it like a photo. Do not commit one, do not send one to a maintainer.
- **Nothing is transmitted** except the MQTT payload you configured, to the broker you configured. There is no telemetry, no analytics, no update check.
- **The audit log** contains timestamps, action names, confidence values and dwell times. It contains no imagery. It does reveal when a person was awake and issuing commands — treat it as health-adjacent data and keep it where you keep other care records.

## Reporting a safety problem

Open an issue with the `safety` label. If it involves a real injury or a near miss, say so in the title; those are read first. Please include the config, the audit log lines around the event, and the hardware. Do not include a landmark recording of a person without their consent.
