# Design

Status: describes v0.1 as built. Anything marked *planned* does not exist yet.

## 1. The gap

Digital systems have a well-understood pattern for letting a model take actions: a tool-call gateway with authorisation, a policy decision point, and an audit trail. Physical systems have no equivalent layer for the specific case that matters here — **a person with very little reliable voluntary movement controlling an ordinary object**.

What exists instead:

- **Dedicated assistive hardware.** Eye trackers, sip-and-puff switches, specialised buttons. They work, they cost hundreds to thousands, and each one is bound to one function.
- **Smart-home ecosystems.** They assume hands, and they lock the user into one vendor's app.
- **Robotics and embodied-agent frameworks.** They optimise for making a machine move, not for making an existing lamp respond to 12 degrees of head roll.
- **Perception demos.** Project Gameface and similar proved "face and head movement can drive a cursor" years ago. Almost all of them stop at the cursor.

The missing piece is thin: personal calibration → intent → a bounded physical action → a log a carer can read. That is the whole project.

## 2. What this is not

- Not a medical device, not an alarm, not a nurse call. See [safety.md](safety.md).
- Not a gesture-recognition library. It recognises one movement at a time, on purpose.
- Not an infrared or Bluetooth stack. It publishes MQTT and stops.
- Not a foundation-model application. Nothing here needs an LLM, and adding one would make the failure modes unexplainable.

## 3. Related work and what it does and does not imply

**Assistive switch access.** The single-switch literature is decades old and its central finding is uncomfortable for demo-driven projects: reliability and cognitive load dominate, and a second switch is not twice as good — it is often worse. This is why the default config ships exactly one action (§5, D1).

**Perception.** MediaPipe Face Mesh gives 478 points on a CPU at real time. Project Gameface uses head and face gestures for cursor control; various Flutter/ONNX projects show local 25–30 fps face pipelines with no cloud dependency. So the input side is not the risk.

**Few-shot personalisation.** The original proposal cited a 12-participant study reporting 94.9% mean accuracy at 5 shots (below a 40-sample-per-class retrained SVM by 1.4 points) and 96.7% at 10 shots, with personalisation under 0.04 s and 36.5 ms end-to-end inference. That number should be read as: *few-shot personalisation is plausible in this feature space*, on standardised gestures and (presumably) non-disabled participants. It is **not** evidence about this project, this population, or these movements, and it appears here as context rather than as a target. It is a secondhand citation and has not been checked against the source paper.

**Actuation.** ir2mqtt, OMOTE and OpenMQTTGateway already convert MQTT to infrared, BLE and 433 MHz, with Home Assistant integration. Reimplementing that would be the single easiest way to make this project unmaintainable.

**Proposal-stage competitors.** SecondHands describes a nearly identical vision — remaining voluntary movement controlling lights, curtains, appliances, with local inference and personal calibration — and its README marks it as a proposal. Two consequences, both acted on here: the window is real, and it will not stay open because a competitor is slow. The advantage claimed here is not ambition; it is that this repository runs on a ¥0 webcam and a laptop from 2015, today, and reports its own numbers as absent rather than assumed.

## 4. Architecture

```
sources ──► features ──► scorer ──► gate ──► policy ──► sink
(camera,                (0..1)      (dwell,  (risk,      (MQTT,
 replay,                            release) confirm,    console)
 scripted)                                    rate limit)
```

Four boundaries, each drawn where it is because a specific thing needs to be replaceable:

| Boundary | Why it is a boundary |
|---|---|
| `sources` → `features` | the only file that imports MediaPipe. Swapping perception libraries, or replaying a recording, costs nothing downstream |
| `scorer` | the seam where a learned matcher will live, without touching dwell logic |
| `gate` → `policy` | "the movement happened" and "we are allowed to act on it" are different questions with different owners |
| `policy` → `sink` | "should this be sent" and "how is it sent" — the second is where hardware fragmentation lives |

Consequence, and the reason the layout is defended this strongly: `pytest` runs the whole decision path on a machine with no camera, no broker and no MediaPipe.

### Features

From 478 landmarks, after rescaling y into width units (MediaPipe normalises x by width and y by height; skipping this makes every angle wrong on a 4:3 frame):

- `roll_deg` — in-plane head tilt, from the eye-corner line
- `yaw`, `pitch` — weak-perspective proxies from nose displacement relative to interocular distance
- `ear` — eye aspect ratio, mean of both eyes
- `brow_raise` — brow height above the eye line, measured in a canonical frame

`canonicalize()` applies a similarity transform so the eye centres sit at ±0.5 with interocular distance 1. That removes translation, scale and roll — the three nuisance variables that break ratio features. It does **not** remove yaw or pitch, which is why `brow_raise` is the only feature computed in canonical space and why left/right asymmetry features are not used yet. Full 3D normalisation by PnP is *planned*; claiming it is done would be the kind of thing that gets found out in a review.

## 5. Decisions

Each decision below is stated with what would make it wrong. A decision with no falsifier is a preference.

### D1. One action at a time

The default configuration has exactly one action. A second action is one config block, and the loader actively refuses two actions that would fire together.

*Wrong if:* users report the single action is insufficient rather than unreliable. Then add a second with a distinct movement, not a second threshold on the same one.

### D2. Geometric baseline before a learned matcher

Head tilt is an angle. A held blink is eye-aspect ratio below a personal baseline. Both are handled by two thresholds, a deadband and a dwell timer, at any frame rate, with failure modes a person can see on the camera feed.

*Wrong if:* measured on target users, the geometric baseline has both a high false-trigger rate and a high miss rate at movement amplitudes users can sustain. That combination means the rest and the intent overlap in feature space — the specific condition a prototype matcher addresses. Until that measurement exists, adding a model is guessing.

### D3. Dwell plus a release requirement

Fire once, then refuse to fire again until the signal drops below the release threshold. A user holding the movement for four seconds gets one command.

*Wrong if:* users cannot reliably release (some conditions cause sustained contraction). The mitigation is a rate limit and a different release condition — an explicit second signal — not removing the release requirement.

### D4. Risk tiers enforced at load time

Low publishes, medium asks a human, high refuses to load the configuration.

*Wrong if:* never. If you disagree, fork it, and then the safety analysis is yours.

### D5. Delegate actuation

`publish(topic, payload)` is the entire output contract.

*Wrong if:* a target object cannot be reached by any existing bridge and the gap is small. Then write the adapter outside this repository.

### D6. Re-calibration is part of the main loop, not a settings screen

Progressive conditions mean today's movement is not last month's. The architecture keeps thresholds in configuration that can be changed and verified in under a minute, and a recording can be replayed to check a change without asking the user to perform the movement again.

*Wrong if:* re-calibration stays a hand edit of YAML forever. It currently is a hand edit; the target (≤ 30 s, unaided) is not met, and [metrics.md](metrics.md) says so.

### D7. Handover is a file-layout property

The core imports numpy and the standard library. Perception and transport are single files behind narrow interfaces. The MQTT contract is documented well enough that Home Assistant can replace the mapping layer entirely.

*Wrong if:* the project accumulates a component only one person understands. That is the failure mode this decision exists to prevent, and it is measured by whether a second maintainer can release without the first.

## 6. Scope of v0.1

| In | Out (and why) |
|---|---|
| one action: head tilt (left / right / any) | additional actions — until the first is clean for weeks |
| held-blink scorer, configurable, off by default | facial-expression input — brow tracking quality is not there |
| dwell / hysteresis / release gate | multi-gesture sequences |
| risk tiers, confirmation, rate limit | nurse call, locks, beds — refused by design |
| JSONL audit log + plain-language rendering | a web dashboard |
| camera, replay, and scripted sources | a mobile app |
| `ito doctor` for dependency diagnosis | auto-install of anything |
| scripted `ito demo` and CI | any claim of measured accuracy |

## 7. Roadmap

Each phase has an exit criterion that is a measurement, not a deliverable.

**P1 — the chain exists** *(done)*
Camera → features → gate → policy → MQTT, with the whole decision path testable without hardware.
*Exit:* `ito demo` and `pytest` both green on a clean machine. ✅

**P2 — first real numbers**
Three to five non-author participants, the protocol in [metrics.md](metrics.md).
*Exit:* latency, false-trigger rate and re-calibration time reported for each, including the failures. No number, no P3.

**P3 — guided re-fit**
A command that takes 20–30 s of neutral and target movement and writes thresholds, with the old ones kept for rollback.
*Exit:* a participant who has never seen the config file re-fits alone in ≤ 30 s.

**P4 — adapter ecosystem**
Two independent MQTT outputs demonstrated (Home Assistant, one hardware bridge), each documented without touching the core.
*Exit:* a third party adds an output without opening a PR against `src/ito`.

**P5 — second action** *(conditional)*
Only after P2 numbers are stable and a user asks for it.
*Exit:* the second action does not degrade the first action's false-trigger rate.

## 8. Deferred: prototypical matching

The plan, recorded so it is not re-litigated from memory:

- Features: pose-normalised landmark vectors, plus the scalars above.
- Method: prototype = mean feature vector over 5 demonstrations; inference = nearest prototype, confidence from distance.
- Constraint: must run in under one frame interval on a CPU, and must be re-fittable in seconds.
- Evaluation: **against the geometric baseline, on the same recorded sessions**, using `ito replay`. A learned matcher that cannot beat two thresholds on recorded data has not earned its complexity.

The blocker is data, not code. Building it before P2 would produce a model tuned on the authors' faces, which is the exact failure this document is trying to avoid.

## 9. Risks

| Risk | Mitigation | Residual |
|---|---|---|
| Landmark quality on target users is worse than in published work | measure at P2 on the actual population, not on authors | unknown until measured |
| False triggers erode trust faster than misses | fail closed at low confidence, tier-based confirmation, cancellable dwells | a tired user at 3 a.m. is still a false trigger |
| Yaw/pitch break asymmetry features | only use roll-invariant features, document the limit | expression input blocked |
| Hardware fragmentation | delegate to bridges, adapters outside the core | some objects remain out of reach |
| Solo maintainer | narrow dependency surface, documented handover, governance triggers | bus factor is 1 until it is 2 |
| Being mistaken for a medical device | explicit tier refusal in code and docs | people will still ask |
| A better-resourced project ships first | ship the running slice and the numbers | real |

## 10. What changed from the original proposal

Recorded because the differences are the design work, and future readers deserve to know which version they are reading.

| Original | Now | Why |
|---|---|---|
| 「少样本原型匹配」 as principle one | geometric baseline is the default; prototype matcher deferred to P5 | the MVP movements are geometric quantities; a model first would make failures undiagnosable, and the supporting number comes from a different population |
| 94.9% / 36.5 ms presented as project-relevant | moved to related work, marked secondhand and not verified | borrowed numbers in a README invite "show me yours", and yours will differ |
| head-pose normalisation as a mandatory component | 2D similarity normalisation implemented and used; 3D PnP marked planned | claiming pose invariance with a 2D transform would be false |
| "护理呼叫" as a main-scenario function, and "护理呼叫" as a high-risk exclusion | nurse call is out of scope at every phase | the original contradicted itself in the two places that matter most |
| BOM "under ¥10" | ¥20–45, itemised | the people who check this are the ones you want to trust you |
| "误触发次数" as a metric | false triggers per hour, per activity, with the movement amplitude and a fatigue rating | a count with no denominator cannot be evaluated |
| "Eyes-off 意图触发器" | dropped; the action is named by what it is (`tilt_left`) | "eyes-off" means driver disengagement in every autonomy context; wrong connotation for a safety-adjacent tool |
| "organisation account + two admins" at v0.1 | single maintainer now, with a written trigger for the second (see GOVERNANCE.md) | an org with one member and two admin slots is theatre |
| 15-section strategy document as the README | short README + this file | a reader decides in thirty seconds whether a repository is alive, and a strategy document does not answer that |
