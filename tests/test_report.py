"""The session summary is the only place a user reads the numbers.

The label matters as much as the value: `frame->enqueue` and `frame->PUBACK`
are different claims, and the summary must not make the stronger one by
accident.
"""

from ito.report import format_summary
from ito.types import SessionStats


def build(**kwargs) -> SessionStats:
    stats = SessionStats()
    for key, value in kwargs.items():
        setattr(stats, key, value)
    return stats


def test_latency_is_labelled_as_an_enqueue_at_qos_zero():
    out = format_summary(
        build(
            frames=900,
            last_ms=60_000,
            published=2,
            frame_to_publish_ms=[12.0, 14.0],
            publish_acked=False,
        )
    )
    assert "frame->enqueue" in out
    assert "PUBACK" not in out
    assert "no broker acknowledgement" in out


def test_latency_is_labelled_as_ack_when_the_sink_waited():
    out = format_summary(
        build(
            frames=900,
            last_ms=60_000,
            published=2,
            frame_to_publish_ms=[12.0, 14.0],
            publish_acked=True,
        )
    )
    assert "frame->PUBACK" in out
    assert "enqueue" not in out


def test_a_short_session_refuses_to_quote_a_rate():
    out = format_summary(build(frames=100, last_ms=15_000, published=2))
    assert "too short to quote a rate" in out


def test_face_loss_appears_only_when_it_happened():
    quiet = format_summary(build(frames=900, last_ms=60_000))
    assert "face lost" not in quiet

    lost = format_summary(build(frames=900, last_ms=60_000, face_gaps=2, max_gap_ms=4200))
    assert "face lost" in lost
    assert "4.2 s" in lost


def test_a_live_session_is_not_labelled_as_synthetic():
    stats = build(frames=900, last_ms=60_000, published=1)
    synthetic = format_summary(stats)
    assert "not a user measurement" in synthetic

    live = format_summary(stats, label="live session", user_session=True)
    assert "not a user measurement" not in live
    assert "one sitting" in live
