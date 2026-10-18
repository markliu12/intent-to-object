import pytest

from ito.gate import Gate, GateConfig


def feed(gate, scores, dt=33, t0=0):
    fires = []
    t = t0
    for s in scores:
        f = gate.update(t, s)
        if f:
            fires.append(f)
        t += dt
    return fires, t


def test_hold_beyond_dwell_fires_once():
    gate = Gate(GateConfig(on=0.5, off=0.35, dwell_ms=400, cooldown_ms=1000), "a")
    fires, _ = feed(gate, [0.9] * 30)
    assert len(fires) == 1
    assert 400 <= fires[0].dwell_ms <= 460
    assert fires[0].action == "a"


def test_continuing_to_hold_does_not_repeat():
    """The single-switch property: a user who cannot release must not be
    punished with a stream of commands."""
    gate = Gate(GateConfig(on=0.5, off=0.35, dwell_ms=400, cooldown_ms=800), "a")
    fires, _ = feed(gate, [0.9] * 120)  # ~4 seconds of holding
    assert len(fires) == 1


def test_release_rearms():
    gate = Gate(GateConfig(on=0.5, off=0.35, dwell_ms=300, cooldown_ms=500), "a")
    fires, t = feed(gate, [0.9] * 20)
    assert len(fires) == 1
    more, _ = feed(gate, [0.0] * 40, t0=t)
    assert more == []
    more, _ = feed(gate, [0.9] * 20, t0=t + 40 * 33)
    assert len(more) == 1


def test_short_pulse_is_cancelled_not_fired():
    gate = Gate(GateConfig(on=0.5, off=0.35, dwell_ms=400, cooldown_ms=500), "a")
    fires, _ = feed(gate, [0.9, 0.9, 0.9] + [0.0] * 20)
    assert fires == []
    assert gate.cancelled == 1
    assert gate.fired == 0


def test_hysteresis_holds_through_a_dip():
    gate = Gate(GateConfig(on=0.5, off=0.35, dwell_ms=300, cooldown_ms=500), "a")
    scores = [0.9] * 2 + [0.40] * 4 + [0.9] * 12
    fires, _ = feed(gate, scores)
    assert len(fires) == 1
    assert gate.cancelled == 0


def test_invalid_gate_config_is_refused():
    with pytest.raises(ValueError):
        Gate(GateConfig(on=0.35, off=0.5))
    with pytest.raises(ValueError):
        Gate(GateConfig(on=1.2, off=0.3))
