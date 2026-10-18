"""Outputs. The core never drives hardware directly -- it publishes.

``publish(topic, payload)`` is the whole contract. Anything that can subscribe
to a local MQTT broker is a supported output, which is why this project does not
need to own an infrared protocol stack, a Bluetooth stack, or a GPIO driver.
"""

from __future__ import annotations

import re
import sys
from typing import Any, Protocol

_PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


def render_payload(template: str, context: dict[str, Any]) -> str:
    """Substitute ``${name}`` placeholders.

    Deliberately not ``str.format``: payloads are usually JSON, and JSON is full
    of braces. A template language that collides with the thing it carries is a
    bug factory.
    """

    def sub(match: re.Match) -> str:
        key = match.group(1)
        if key not in context:
            raise KeyError(f"payload placeholder ${{{key}}} has no value")
        return str(context[key])

    return _PLACEHOLDER.sub(sub, template)


class Sink(Protocol):
    name: str
    # True when publish() returns only after the transport has confirmed the
    # message. It is what decides whether the latency number in the report is
    # "frame -> broker acknowledged" or "frame -> handed to the network stack",
    # and those two are not the same claim.
    acknowledges: bool

    def publish(self, topic: str, payload: str) -> None: ...

    def close(self) -> None: ...


class NullSink:
    name = "null"
    acknowledges = False

    def __init__(self) -> None:
        self.sent = 0

    def publish(self, topic: str, payload: str) -> None:
        self.sent += 1

    def close(self) -> None:
        pass


class ConsoleSink:
    name = "console"
    acknowledges = False

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stdout

    def publish(self, topic: str, payload: str) -> None:
        print(f"  -> {topic}  {payload}", file=self.stream)

    def close(self) -> None:
        pass


class MqttSink:
    name = "mqtt"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1883,
        client_id: str = "intent-to-object",
        username: str | None = None,
        password: str | None = None,
        qos: int = 0,
        retain: bool = False,
        publish_timeout_s: float = 2.0,
    ) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise RuntimeError("MQTT output needs paho-mqtt: pip install '.[mqtt]'") from exc

        self.qos = qos
        self.retain = retain
        self.publish_timeout_s = publish_timeout_s
        # QoS 0 is fire-and-forget by protocol: there is no PUBACK to wait for,
        # so a latency measured here is an enqueue, not a delivery.
        self.acknowledges = qos > 0
        self._mqtt = mqtt
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if username:
            self.client.username_pw_set(username, password)
        try:
            self.client.connect(host, port, keepalive=30)
        except OSError as exc:
            raise RuntimeError(
                f"could not reach an MQTT broker at {host}:{port} ({exc}). "
                "Start one (for example `mosquitto -d`), or change mqtt.host / mqtt.port "
                "in the config. `ito doctor` checks this too."
            ) from exc
        self.client.loop_start()

    def publish(self, topic: str, payload: str) -> None:
        info = self.client.publish(topic, payload, qos=self.qos, retain=self.retain)
        if getattr(info, "rc", 0) != 0:
            raise RuntimeError(
                f"MQTT publish to {topic} failed with rc={info.rc}; the broker connection "
                "is probably down. Nothing was sent, and this run should not be trusted."
            )
        if self.acknowledges:
            try:
                info.wait_for_publish(timeout=self.publish_timeout_s)
            except (RuntimeError, ValueError) as exc:
                raise RuntimeError(
                    f"the broker did not acknowledge {topic} within "
                    f"{self.publish_timeout_s:.1f}s; treat this command as not delivered."
                ) from exc

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


class MultiSink:
    name = "multi"

    def __init__(self, *sinks: Sink) -> None:
        self.sinks = list(sinks)
        self.acknowledges = bool(sinks) and all(
            getattr(sink, "acknowledges", False) for sink in self.sinks
        )

    def publish(self, topic: str, payload: str) -> None:
        for sink in self.sinks:
            sink.publish(topic, payload)

    def close(self) -> None:
        for sink in self.sinks:
            sink.close()
