import pytest

from ito.sinks import render_payload


def test_payload_substitution_survives_json_braces():
    template = '{"intent":"${action}","confidence":${score}}'
    out = render_payload(template, {"action": "tilt_left", "score": 0.81})
    assert out == '{"intent":"tilt_left","confidence":0.81}'


def test_missing_placeholder_is_loud():
    with pytest.raises(KeyError):
        render_payload("${nope}", {"action": "x"})


def test_plain_text_template_is_untouched():
    assert render_payload("hello", {}) == "hello"
