# Contributing

The most useful contribution right now is not code. It is a measured session: three to five people, the protocol in [docs/metrics.md](docs/metrics.md), and the numbers reported even when they are bad. The project has no user data and that is its largest hole.

## Before you write code

Open an issue. Especially for anything that touches the core, because the core is deliberately small and the answer to "should this be in the core?" is usually no.

Two rules that shape most review comments:

1. **The core stays thin.** `src/ito` may import numpy and the standard library. MediaPipe lives only in `sources.py`; paho only in `sinks.py`. A pull request that adds a dependency to the core needs a better argument than convenience.
2. **No new physical-safety surface.** Adding an output, a tier or a confirmation path is welcome. Making a high-risk action possible is not, and will be closed.

## Adding an output

The intended path, in order of preference:

1. **Home Assistant automation.** No code. Document it in an issue and it gets linked.
2. **A standalone adapter** (a separate repository or a `contrib/` script) that subscribes to `ito/intent/#` and republishes in your target's format. This does not require changing anything here.
3. **A `Sink` in `sinks.py`.** Only for transports, not for device protocols: `WebhookSink`, `UnixSocketSink`, that kind of thing.

Infrared encoders, BLE pairing logic and GPIO timing do not go in this repository. They exist, they are mature, and duplicating them here would make the intent layer unmaintainable — which is the one thing that would actually kill the project.

## Adding an action

New actions need three things:

- a scorer in `scorers/` returning `[0, 1]`, with the physical quantity it measures documented;
- evidence that it does not degrade the existing action's false-trigger rate;
- a note in [docs/design.md](docs/design.md) §6 scope table.

A second action that fires on the same movement as the first will be refused by the config loader, which is not a bug.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Tests must pass on a machine with no camera, no broker and no MediaPipe — that is the design, and a test that needs hardware is in the wrong repository.

Anything that changes thresholds, filtering or timing needs a test that states the property being protected, not just the current output. `tests/test_gate.py` is the model: it asserts *"holding for four seconds produces one command"*, which is a promise, not a snapshot.

The bar CI enforces, so run it before opening a pull request:

```bash
ruff check src tests && ruff format --check src tests
mypy
pytest -q --cov=ito --cov-fail-under=75
```

`mypy` is clean on the core, and the coverage floor is a ratchet: it exists so the decision path cannot quietly lose the tests that make it believable. If a change cannot clear it, the conversation is about the change, not about lowering the number. `sources.py` is the one file that will never be fully covered — the camera path needs a camera, and a test that needs hardware belongs in your own repository.

## Style

- Comments explain *why*, and are expected next to anything surprising. The code says what it does.
- No comments restating the line below them.
- Prefer a slightly longer name to an abbreviation that needs a comment.
- Error messages are addressed to the person who will see them, and say what to do next.

## Reporting a safety problem

Label the issue `safety`. If there was an injury or a near miss, say so in the title. Include the config, the audit lines around the event and the hardware.

Do not attach a landmark recording of a person without their written consent, and never attach video of a participant.
