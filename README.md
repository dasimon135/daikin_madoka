# Home Assistant Daikin Madoka

Integration for Daikin Madoka BRC1H Bluetooth thermostats. This repository provides **two independent approaches** — choose one based on your setup.

![](images/madoka.png)

---

## Which approach should I use?

| | Option 1: HA Integration | Option 2: ESPHome |
|---|---|---|
| **Hardware needed** | None (BLE from HA host) | ESP32 (e.g. M5Stack Atom) |
| **HA server location** | Must be within BLE range | Anywhere on your network |
| **Docker/VM** | Requires DBUS config | Works out of the box |
| **Install via** | HACS | ESPHome dashboard |

**If you have an ESP32 device, use Option 2.** It's simpler, more reliable, and works regardless of how HA is hosted.

---

## Option 1 — Home Assistant Integration (Direct Bluetooth)

> ✅ **Fixed in v2.3.0**: earlier versions failed on recent Home Assistant with `cannot import name 'discover' from 'bleak'` (bleak ≥ 0.20 removed `discover`). v2.3.0 ships a patched [pymadoka](https://github.com/dasimon135/pymadoka) fork and connects through HA's own Bluetooth stack, so Option 1 now works on current HA. If you hit this error, update to **v2.3.0** or later.

The integration connects to the Madoka thermostat directly from the HA host via Bluetooth, using the [pymadoka](https://github.com/dasimon135/pymadoka) library.

### Installation

**Via HACS (recommended):**
1. Add this repository as a custom HACS integration repository.
2. Install **Daikin Madoka** from HACS.
3. Restart Home Assistant.

**Manual:**
Copy `custom_components/daikin_madoka/` into your HA `custom_components/` directory, then restart.

### Entities exposed

Each thermostat creates:
- `climate.*` — thermostat (mode, setpoint, fan speed, current temperature)
- `sensor.*_indoor_temperature` — indoor temperature
- `sensor.*_outdoor_temperature` — outdoor temperature
- `binary_sensor.*_clean_filter` — filter alert (device_class: problem)
- `button.*_reset_filter` — reset filter timer
- `number.*_eye_brightness` — display LED brightness 0–19

### Requirements

The Madoka uses Bluetooth pairing. You must pair the device once from the HA host:

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
