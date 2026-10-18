from ito.policy import Policy, RateLimit
from ito.types import Fire


def fire(t=0, score=0.9):
    return Fire(t_ms=t, score=score, dwell_ms=500, action="a")


def test_high_risk_is_refused_at_runtime_too():
    p = Policy()
    d = p.evaluate("high", "unlock_door", fire())
    assert not d.allowed
    assert d.reason == "tier_high_unsupported"


def test_low_risk_publishes_without_asking():
    p = Policy()
    d = p.evaluate("low", "lamp", fire())
    assert d.allowed and d.confirmed in (False, None)


def test_medium_risk_denied_when_nobody_confirms():
    p = Policy(confirmer=lambda action, f: False)
    d = p.evaluate("medium", "curtain", fire())
    assert not d.allowed
    assert d.reason == "not_confirmed"


def test_medium_risk_allowed_after_confirmation():
    p = Policy(confirmer=lambda action, f: True)
    d = p.evaluate("medium", "curtain", fire())
    assert d.allowed and d.confirmed is True


def test_rate_limit_stops_a_runaway_source():
    p = Policy(rate_limit=RateLimit(max_per_minute=2, window_ms=60_000))
    assert p.evaluate("low", "lamp", fire(t=1000)).allowed
    assert p.evaluate("low", "lamp", fire(t=2000)).allowed
    blocked = p.evaluate("low", "lamp", fire(t=3000))
    assert not blocked.allowed
    assert blocked.reason == "rate_limited"


def test_rate_limit_window_slides():
    p = Policy(rate_limit=RateLimit(max_per_minute=1, window_ms=10_000))
    assert p.evaluate("low", "lamp", fire(t=0)).allowed
    assert not p.evaluate("low", "lamp", fire(t=5000)).allowed
    assert p.evaluate("low", "lamp", fire(t=11_000)).allowed
