# Home Assistant Daikin Madoka

Integration for Daikin Madoka BRC1H Bluetooth thermostats. This repository provides **two independent approaches** — choose one based on your setup.

![](images/madoka.png)

---

## Which approach should I use?

| | Option 1: HA Integration | Option 2: ESPHome |
|---|---|---|
| **Hardware needed** | None (BLE from HA host or any ESPHome Bluetooth proxy) | ESP32 (e.g. M5Stack Atom) |
| **HA server location** | Anywhere (since v2.4.0, works through Bluetooth proxies) | Anywhere on your network |
| **Docker/VM** | Works via Bluetooth proxy; local adapter needs DBUS config | Works out of the box |
| **Install via** | HACS | ESPHome dashboard |

Both options are now equally capable. Option 1 keeps everything inside Home Assistant (discovery, options, diagnostics); Option 2 gives the thermostat its own dedicated ESP32 bridge.

---

## Option 1 — Home Assistant Integration (Direct Bluetooth)

> ✨ **New in v2.4.0**: connections go through Home Assistant's Bluetooth stack, so the integration works through **ESPHome Bluetooth proxies** — your HA server no longer needs to be within BLE range. Thermostats in range are **discovered automatically**, the poll interval is configurable, and diagnostics can be downloaded from the device page.

The integration connects to the Madoka thermostat via Bluetooth (local adapter or ESPHome Bluetooth proxy), using the [pymadoka](https://github.com/dasimon135/pymadoka) library.

### Installation

**Via HACS (recommended):**
1. Add this repository as a custom HACS integration repository.
2. Install **Daikin Madoka** from HACS.
3. Restart Home Assistant.

**Manual:**
Copy `custom_components/daikin_madoka/` into your HA `custom_components/` directory, then restart.

### Setup

If a thermostat is advertising nearby (directly or via a Bluetooth proxy), Home Assistant will discover it and offer to add it — just confirm and optionally give it a name. Otherwise go to **Settings → Devices & Services → Add Integration → Daikin Madoka** and pick it from the dropdown (or type its MAC address).

The poll interval (default 60 s) can be changed from the integration's **Configure** dialog.

### Entities exposed

Each thermostat creates:
- `climate.*` — thermostat (mode, setpoint, fan speed, current temperature; separate heating/cooling setpoints in AUTO mode when the device has range mode enabled)
- `sensor.*_indoor_temperature` — indoor temperature
- `sensor.*_outdoor_temperature` — outdoor temperature
- `sensor.*_operating_time` — cumulative hours the unit has been running (coarse, poll-interval granularity; persisted across restarts)
- `sensor.*_signal_strength` — Bluetooth RSSI (diagnostic, disabled by default)
- `sensor.*_connection_source` — which BLE path serves the thermostat: active proxy while connected, preferred (bonded) proxy otherwise (diagnostic, disabled by default)
- `binary_sensor.*_clean_filter` — filter alert (device_class: problem)
- `button.*_reset_filter` — reset filter timer
- `button.*_reconnect` — drop and re-establish the Bluetooth connection (diagnostic)
- `number.*_eye_brightness` — display LED brightness 0–19

### Requirements

The BRC1H requires an **authenticated (MITM) pairing** — it silently ignores every command, and even notification subscriptions, on an unauthenticated link. How you satisfy that depends on your Bluetooth path:

#### Via an ESPHome Bluetooth proxy (validated on hardware)

The **stock bluetooth-proxy firmware cannot pair with the BRC1H** (it runs `io_capability: none` and nothing answers the numeric-comparison confirmation). Add this to the proxy's YAML and reflash:

```yaml
# io_capability is the only required change: the Bluedroid stack already
# ships with SMP enabled and persists bonds to NVS by default
# (CONFIG_BT_BLE_SMP_ENABLE and CONFIG_BT_BLE_SMP_BOND_NVS_FLASH are
# both default y).
esp32_ble:
  io_capability: display_yes_no

# Pairing responder: never connects (auto_connect: false), only auto-confirms
# the numeric-comparison pairing for the thermostat's address.
ble_client:
  - mac_address: "AA:BB:CC:DD:EE:FF"   # your BRC1H MAC
    id: madoka_pairing
    auto_connect: false
    on_numeric_comparison_request:
      then:
        - ble_client.numeric_comparison_reply:
            id: madoka_pairing
            accept: true
```

Then add the integration: on the first connection the thermostat shows a pairing prompt on its display — **accept it within a few seconds**. Notes:
- The bond is stored **per proxy**: if several proxies can reach the thermostat, each one triggers its own (one-time) pairing prompt, and each needs the YAML above.
- If pairing loops (prompt appears, then fails, then re-appears), un-pair on the thermostat (Bluetooth menu → forget) and retry.

📘 **Reference proxy setup**: for the complete, annotated configuration — including a pairing responder per thermostat that pushes the 6-digit pairing code to Home Assistant as a notification (so you know *which* thermostat is pairing through *which* proxy), the passive-proxy alternative, and a troubleshooting table for multi-proxy homes — see **[docs/esphome-proxy.md](docs/esphome-proxy.md)**.

#### Via the HA host's own adapter

Pair the device once from the host:

```bash
bluetoothctl
agent KeyboardDisplay
remove <MAC_ADDRESS>
scan on
# wait for device to appear, then:
scan off
pair <MAC_ADDRESS>
# accept on thermostat within a few seconds
```

> If running HA in Docker: mount `/var/run/dbus/system_bus_socket` and run in privileged mode.

---

## Option 2 — ESPHome (ESP32 Proxy)

An ESP32 bridges the Bluetooth connection over WiFi. HA talks to the ESP via the standard ESPHome API — no special configuration needed on the HA side.

### Minimal config

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/dasimon135/daikin_madoka
      ref: v2.2.0
      path: esphome/components
    components: [madoka]

esp32_ble:
  io_capability: display_yes_no

esp32_ble_tracker:
  scan_parameters:
    interval: 320ms
    window: 30ms
    active: true

ble_client:
  - mac_address: "AA:BB:CC:DD:EE:FF"
    id: my_madoka
    on_disconnect:
      then:
        - delay: 10s
        - ble_client.connect: my_madoka

climate:
  - platform: madoka
    name: "Living Room"
    ble_client_id: my_madoka
    update_interval: 15s
```

### Optional entities

Add any of these under your `climate: - platform: madoka` block:

```yaml
    outdoor_temperature:
      name: "Outdoor Temperature"
    clean_filter:
      name: "Filter Alert"
    firmware_version:
      name: "Firmware"
    eye_brightness:
      name: "Display Brightness"
    reset_filter:
      name: "Reset Filter"
```

### Entities exposed

Each thermostat creates:
- `climate.*` — thermostat (mode, setpoint, fan speed, current temperature)
- `sensor.*_outdoor_temperature` — outdoor temperature (optional)
- `binary_sensor.*_clean_filter` — filter alert (optional)
- `text_sensor.*_firmware_version` — firmware version (optional)
- `number.*_eye_brightness` — display LED brightness 0–19 (optional)
- `button.*_reset_filter` — reset filter timer (optional)

### Pinning versions

Always pin to a specific release tag — never track `main` directly (main may contain work-in-progress changes):

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/dasimon135/daikin_madoka
      ref: v2.2.0        # replace with latest tag
      path: esphome/components
    components: [madoka]
```

See [CHANGELOG.md](CHANGELOG.md) for available versions.

---

## Dashboard cards

### Madoka Card (bundled)

A dial-style card that mirrors the physical BRC1H — a glowing halo that follows
the mode, a setpoint arc, fan segments, an eye-brightness slider, a 12 h
temperature sparkline and filter/signal chips. It ships **inside the
integration** (no separate install) and registers itself automatically; pick
**Madoka Card** from the dashboard card picker, or add it in YAML:

```yaml
type: custom:madoka-card
entity: climate.my_madoka
# layout: full         # full | compact | tile  (default: full)
# compact: true        # alias for layout: compact
# name: "Bedroom"      # override the title
```

Three layouts: **full** (the dial with fan/brightness/graph), **compact**
(dial + controls + modes only) and **tile** — an ultra-compact row (a
mode-colored status dot + name + current→target + `−`/`+`) that lines up with
Home Assistant's tile cards in a dense grid.

The related entities (outdoor temperature, eye brightness, filter, signal) are
discovered automatically from the same device — you only need the `climate.*`
entity. It follows your Home Assistant theme and language (mode names use HA's
own climate translations). The signal chip appears once you enable the
disabled-by-default `sensor.*_signal_strength`.

### Thermostat card

```yaml
type: thermostat
entity: climate.my_madoka
```

### Full entity card

```yaml
type: entities
entities:
  - entity: climate.my_madoka
  - entity: sensor.my_madoka_outdoor_temperature
  - entity: binary_sensor.my_madoka_clean_filter
  - entity: button.my_madoka_reset_filter
```

---

## Credits

Based on the original work by [@mduran80](https://github.com/mduran80/daikin_madoka).  
ESPHome madoka component adapted from [Petapton/esphome](https://github.com/Petapton/esphome).
