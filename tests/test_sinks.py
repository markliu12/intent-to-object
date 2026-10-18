import io
import sys
import types

import pytest

from ito.sinks import ConsoleSink, MqttSink, MultiSink, NullSink, render_payload


def test_payload_substitution_survives_json_braces():
    template = '{"intent":"${action}","confidence":${score}}'
    out = render_payload(template, {"action": "tilt_left", "score": 0.81})
    assert out == '{"intent":"tilt_left","confidence":0.81}'


def test_missing_placeholder_is_loud():
    with pytest.raises(KeyError):
        render_payload("${nope}", {"action": "x"})


def test_plain_text_template_is_untouched():
    assert render_payload("hello", {}) == "hello"


class _Info:
    """Stand-in for paho's MQTTMessageInfo."""

    def __init__(self, rc: int = 0) -> None:
        self.rc = rc
        self.waited = False

    def wait_for_publish(self, timeout=None) -> None:
        self.waited = True


def _install_fake_paho(monkeypatch, *, connect_exc=None, rc: int = 0) -> dict:
    """Put a fake paho in sys.modules so the MQTT sink can be tested with no broker.

    The point is not to test paho; it is to pin the contract this project
    makes with it: unreachable broker becomes a sentence, QoS 1 means the
    latency includes an acknowledgement, and a rejected publish is not silent.
    """
    client_mod = types.ModuleType("paho.mqtt.client")
    seen: dict = {}

    class CallbackAPIVersion:
        VERSION2 = 2

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            self.info = _Info(rc)

        def username_pw_set(self, *args, **kwargs) -> None:
            pass

        def connect(self, host, port, keepalive=30) -> None:
            seen["endpoint"] = f"{host}:{port}"
            if connect_exc is not None:
                raise connect_exc

        def loop_start(self) -> None:
            pass

        def loop_stop(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def publish(self, topic, payload, qos=0, retain=False):
            seen["publish"] = (topic, payload, qos)
            seen["info"] = self.info
            return self.info

    client_mod.Client = Client
    client_mod.CallbackAPIVersion = CallbackAPIVersion
    paho = types.ModuleType("paho")
    mqtt_pkg = types.ModuleType("paho.mqtt")
    mqtt_pkg.client = client_mod
    paho.mqtt = mqtt_pkg
    monkeypatch.setitem(sys.modules, "paho", paho)
    monkeypatch.setitem(sys.modules, "paho.mqtt", mqtt_pkg)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", client_mod)
    return seen


def test_an_unreachable_broker_is_a_sentence_not_a_traceback(monkeypatch):
    _install_fake_paho(monkeypatch, connect_exc=ConnectionRefusedError(10061, "refused"))
    with pytest.raises(RuntimeError, match="could not reach an MQTT broker at localhost:1883"):
        MqttSink()


def test_qos_one_waits_for_the_broker_to_acknowledge(monkeypatch):
    seen = _install_fake_paho(monkeypatch)
    sink = MqttSink(qos=1)
    assert sink.acknowledges is True
    sink.publish("ito/intent/x", "{}")
    assert seen["info"].waited is True


def test_qos_zero_does_not_pretend_to_be_acknowledged(monkeypatch):
    seen = _install_fake_paho(monkeypatch)
    sink = MqttSink(qos=0)
    assert sink.acknowledges is False
    sink.publish("ito/intent/x", "{}")
    assert seen["info"].waited is False


def test_a_rejected_publish_is_loud(monkeypatch):
    _install_fake_paho(monkeypatch, rc=4)
    sink = MqttSink(qos=0)
    with pytest.raises(RuntimeError, match="failed with rc=4"):
        sink.publish("ito/intent/x", "{}")


def test_the_sink_without_a_transport_never_claims_delivery():
    assert NullSink().acknowledges is False
    assert ConsoleSink(stream=io.StringIO()).acknowledges is False


def test_a_multi_sink_only_claims_acknowledgement_if_every_part_does():
    quiet = ConsoleSink(stream=io.StringIO())
    assert MultiSink(NullSink(), quiet).acknowledges is False

    class Acking(NullSink):
        acknowledges = True

    assert MultiSink(Acking(), Acking()).acknowledges is True
