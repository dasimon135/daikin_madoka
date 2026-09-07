# Deployment guide — Madoka ESPHome components

## Requirements

- ESPHome **2025.10+** (CI builds against 2026.8.2)
- An ESP32 or ESP32-S3 (recommended: M5Stack Atom Lite or Atom S3 Lite)
- One or more Daikin Madoka BRC1H thermostats

## Installation steps

### 1. Get the components

#### Option A — from GitHub, pinned to a tag (recommended)

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/dasimon135/daikin_madoka
      ref: v3.12.1        # replace with the latest tag
      path: esphome/components
    components: [madoka, madoka_base]
```

Both `path: esphome/components` and `madoka_base` are required. The components
live in a subfolder of the repository, and ESPHome's `AUTO_LOAD` cannot reach a
component that is not named in `components:`. Tags before **v3.11.0** do not
carry `madoka_base`.

Do not track `main` on a deployed node: it may hold changes not yet validated
on hardware.

#### Option B — local copy

Copy this repository's `esphome/components` folder into your ESPHome
configuration directory, then:

```yaml
external_components:
  - source:
      type: local
      path: components     # relative to your ESPHome config directory
    components: [madoka, madoka_base]
```

### 2. Write your configuration

```yaml
esphome:
  name: madoka-proxy

esp32:
  board: m5stack-atom  # adjust to your hardware

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

api:
  encryption:
    key: !secret api_key

ota:
  - platform: esphome

external_components:
  - source:
      type: git
      url: https://github.com/dasimon135/daikin_madoka
      ref: v3.12.1        # replace with the latest tag
      path: esphome/components
    components: [madoka, madoka_base]

# Authenticated (MITM) pairing is mandatory for the BRC1H. This block and the
# on_numeric_comparison_request responder below are both required — without
# them the thermostat connects and then ignores every command.
esp32_ble:
  io_capability: display_yes_no

esp32_ble_tracker:
  id: ble_tracker
  max_connections: 2

bluetooth_proxy:
  active: false

ble_client:
  - mac_address: "XX:XX:XX:XX:XX:XX"
    id: madoka_1
    on_numeric_comparison_request:
      then:
        - ble_client.numeric_comparison_reply:
            id: madoka_1
            accept: true
    on_disconnect:
      then:
        - ble_client.connect: madoka_1

climate:
  - platform: madoka
    name: "Madoka thermostat"
    ble_client_id: madoka_1
    update_interval: 15s
    # dual_setpoint: true   # only if range mode is enabled on the BRC1H
    outdoor_temperature:
      name: "Madoka outdoor temperature"
    clean_filter:
      name: "Madoka filter"
    firmware_version:
      name: "Madoka firmware"
    eye_brightness:
      name: "Madoka LED brightness"
    reset_filter:
      name: "Madoka filter reset"
```

### 3. Compile and flash

From the ESPHome dashboard: click **INSTALL**.

From the command line:

```bash
esphome compile madoka-proxy.yaml
esphome upload madoka-proxy.yaml
```

## Troubleshooting

### The thermostat does not connect

1. Check the MAC address with `bluetoothctl scan on`
2. Make sure the thermostat is not already connected to the mobile app
3. Move the ESP32 closer to the thermostat
4. Turn on debug logs: `logger: level: DEBUG`

### It connects but no command has any effect

The link is unauthenticated. Both `esp32_ble: io_capability: display_yes_no`
and the `on_numeric_comparison_request` responder are required — see
[README.md](README.md#pairing).

### Re-pairing with the phone

See the dedicated section in [README.md](README.md#re-pairing-with-the-phone).
The BLE switch is included in [`example-config.yaml`](example-config.yaml).
