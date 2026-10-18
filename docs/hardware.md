# Wiring an object

This project ships no firmware, no infrared codes and no hardware driver. It publishes a message. Whatever can subscribe to your broker is the actuator.

That is a deliberate constraint: infrared encodings, Bluetooth quirks and GPIO timing are enormous, well-served problem spaces, and maintaining them would double the size of a project whose actual contribution is the intent layer.

## The contract

```
topic:   whatever you configured (default: ito/intent/<action>)
payload: UTF-8 JSON
qos:     0 by default, 1 if you need acknowledgement
```

```json
{"intent":"tilt_left","confidence":0.83,"held_ms":467,"ts_ms":12100,"source":"intent-to-object"}
```

`confidence` is the scorer output at the moment of firing, `held_ms` is how long the movement was actually held (always ≥ the configured dwell). A subscriber that wants to gate harder can use both and ignore anything under an arbitrary bar.

Only one message is published per completed movement. There is no "still held" stream and no "released" event. Keeping the contract at one message is what makes a dozen different subscribers possible.

## The 30-minute path: something that is already smart

If the object can already be controlled by Home Assistant, there is nothing to build.

```yaml
# configuration.yaml -- note: `trigger:` is the 2024.10+ spelling;
# older installs accept `platform: mqtt`
automation:
  - alias: "Bedside lamp follows tilt left"
    triggers:
      - trigger: mqtt
        topic: ito/intent/tilt_left
    actions:
      - action: light.toggle
        target:
          entity_id: light.bedside
```

Test it before wiring anything:

```bash
mosquitto_sub -h localhost -t 'ito/intent/#' -v
ito run --mqtt          # in another terminal
```

## The other path: an object with only an infrared remote

Use a mature bridge rather than writing one:

- **ir2mqtt** — ESP32 infrared bridge with Home Assistant discovery
- **OMOTE** — ESP32 universal-remote firmware (IR, BLE, MQTT)
- **OpenMQTTGateway** — BLE / 433 MHz / IR to MQTT, one firmware, many radios

Their payload formats are theirs, not ours. Two ways to connect:

1. **Map in Home Assistant** (recommended). `ito` publishes `tilt_left`; an automation translates that into whatever command the bridge expects. No code, and the mapping is visible in the UI.
2. **Write a thin adapter.** Twenty lines of `paho` that subscribes to `ito/intent/#` and republishes in the bridge's format. This is the intended way to add an output, and it does not require changing this repository.

If you write an adapter, open an issue and we will link it from here. Do not send a pull request that adds an infrared encoder to the core.

## What it actually costs

The "under ¥10" figure that circulates for ESP32 infrared bridges is the price of a bare module in bulk, and it is not what anyone pays:

| Item | Realistic single-unit price (CNY) |
|---|---|
| ESP32-C3 SuperMini or similar | 10–18 |
| 940 nm IR LED + 2N2222 + resistors | 2–4 |
| 5 V supply (or a USB port you already have) | 0–10 |
| Enclosure, wire, headers | 5–15 |
| **Total** | **~20–45** |

Still cheap. Still an order of magnitude below an eye tracker. But quote the number you can actually buy at, because the people who check it are the people you most want to trust you.

Emitter wiring, if you are rolling your own: IR LED anode to 5 V through a current-limiting resistor sized for ~100 mA (≈39 Ω, ¼ W, and re-do the arithmetic for your LED's forward voltage), cathode to the collector of an NPN transistor, emitter to ground, base to the ESP32 pin through 1 kΩ. Check the transistor's dissipation; a small-signal part is fine at these currents, a power part is not required.

## What not to connect it to

Door locks, gas valves, beds, heaters, anything that moves a person, anything whose failure mode is "someone is now in danger". The policy layer refuses these by configuration, but the relay will not check that for you. See [safety.md](safety.md).
