# Madoka ESPHome components

This folder holds the custom ESPHome components that drive Daikin Madoka BRC1H
thermostats from an ESP32 over Bluetooth Low Energy.

## Compatibility

| ESPHome | Support |
|---|---|
| < 2025.10 | Not supported |
| 2025.10 – 2026.8.1 | Expected to work, not built here |
| 2026.8.2 | Built and compiled on every change by CI |

CI installs the version pinned in
[`.github/workflows/esphome.yml`](../.github/workflows/esphome.yml) and runs
`esphome config` then `esphome compile` on every file in
[`tests/`](tests/), targeting `esp32dev` with the ESP-IDF framework. That is
what proves the C++ still builds; nothing here is validated against other
ESPHome releases.

## Components in this folder

- **`madoka`** — climate platform for Madoka thermostats
- **`madoka_vam`** — climate platform for Daikin VAM ventilation units (heat
  recovery ventilation), which are driven by the same BRC1H controller
- **`madoka_base`** — the shared BLE transport both platforms sit on

> **`madoka_base` must be listed explicitly.** ESPHome's `AUTO_LOAD` cannot
> reach a component that is not named in `external_components: components:`.
> Leave it out and the build stops with `Component not found: madoka_base`.

## Installation

### Option 1 — from GitHub, pinned to a tag (recommended)

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/dasimon135/daikin_madoka
      ref: v3.12.1        # replace with the latest tag
      path: esphome/components
    components: [madoka, madoka_base]
```

`path: esphome/components` is required: the components live in a subfolder of
this repository, not at its root.

Never point an ESP32 at `main` in production. Pin a released tag, so that work
in progress on the Home Assistant side or on a component cannot reach a node
you already flashed. Tags before **v3.11.0** do not carry `madoka_base` and
will fail to build with the config above.

### Option 2 — local copy (for development)

Copy this repository's `esphome/components` folder into your ESPHome
configuration directory, then point `path` at wherever you put it:

```yaml
external_components:
  - source:
      type: local
      path: components     # relative to your ESPHome config directory
    components: [madoka, madoka_base]
```

## Version policy

- `main` — continuous integration, may carry changes not yet validated on hardware
- `vX.Y.Z` tags — the stable versions ESPHome users should pin
- HACS follows repository releases, so Home Assistant users are never forced onto `main`

The convention this repository follows:

1. Tag a version only after it has been validated on real hardware.
2. Record every YAML or behavioural change on the ESPHome side in the changelog.
3. Ask ESPHome users to bump their `ref` explicitly when they want a new version.

## Example configuration

```yaml
substitutions:
  name: m5stack-atom-lite-a03448
  friendly_name: LaundryAtom

esphome:
  name: ${name}
  friendly_name: ${friendly_name}

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

# Authenticated (MITM) pairing is MANDATORY for the BRC1H. Both this block and
# the on_numeric_comparison_request responder below are required — see
# "Pairing" further down for what breaks when either is missing.
esp32_ble:
  io_capability: display_yes_no

esp32_ble_tracker:
  id: ble_tracker
  max_connections: 2

bluetooth_proxy:
  active: false

ble_client:
  - mac_address: "F0:B3:1E:87:AF:FE"
    id: madoka_salon
    on_numeric_comparison_request:
      then:
        - ble_client.numeric_comparison_reply:
            id: madoka_salon
            accept: true
    on_disconnect:
      then:
        - ble_client.connect: madoka_salon
  - mac_address: "1C:54:9E:90:E3:0E"
    id: madoka_parents
    on_numeric_comparison_request:
      then:
        - ble_client.numeric_comparison_reply:
            id: madoka_parents
            accept: true
    on_disconnect:
      then:
        - ble_client.connect: madoka_parents

climate:
  - platform: madoka
    name: "Madoka living room"
    ble_client_id: madoka_salon
    update_interval: 15s
    # dual_setpoint: true   # only if range mode is enabled on the BRC1H
    outdoor_temperature:
      name: "Madoka living room outdoor temperature"
    clean_filter:
      name: "Madoka living room filter"
    firmware_version:
      name: "Madoka living room firmware"
    eye_brightness:
      name: "Madoka living room LED brightness"
    reset_filter:
      name: "Madoka living room filter reset"
  - platform: madoka
    name: "Madoka parents"
    ble_client_id: madoka_parents
    update_interval: 15s
    outdoor_temperature:
      name: "Madoka parents outdoor temperature"
    clean_filter:
      name: "Madoka parents filter"
    firmware_version:
      name: "Madoka parents firmware"
    eye_brightness:
      name: "Madoka parents LED brightness"
    reset_filter:
      name: "Madoka parents filter reset"
```

A complete, commented version of this configuration is in
[`example-config.yaml`](example-config.yaml).

## Pairing

The BRC1H only accepts an authenticated link. It silently ignores every command
sent over an unauthenticated one, which looks like a node that connects happily
and then does nothing.

It pairs by **numeric comparison**: both sides display a six-digit code and each
confirms it matches. Two pieces of configuration are needed, and neither works
without the other.

- `esp32_ble: io_capability: display_yes_no` — the default (`none`) cannot take
  part in a numeric comparison at all, and `keyboard` offers passkey entry, a
  pairing model the thermostat never asks for.
- `on_numeric_comparison_request` on each `ble_client`, replying with
  `accept: true` — without it, pairing starts, nothing confirms it, and the
  connection fails with `AuthenticationCanceled`, often with no prompt appearing
  on the thermostat screen at all.

## Single or dual setpoint (`dual_setpoint`)

The BRC1H runs either with one setpoint or with a heating/cooling **range**
(range mode enabled on the thermostat itself).

| Option | Default | Description |
|---|---|---|
| `dual_setpoint` | `false` | Exposes two setpoints (heat/cool range) instead of one. |

```yaml
climate:
  - platform: madoka
    name: "Madoka living room"
    ble_client_id: madoka_salon
    dual_setpoint: true   # only if range mode is enabled on the BRC1H
```

**Why a YAML option rather than automatic detection?** The Home Assistant
integration (option 1 in the main README) switches between single setpoint and
range on its own, because Home Assistant lets an entity change its
`supported_features` at runtime. ESPHome does not: climate *traits* are
announced once, when the ESP32 declares its entities to the API. The choice has
to be made at compile time. Parity with the native integration is therefore at
the configuration level, not dynamic.

In single-setpoint mode the component writes the setpoint register matching the
active mode (heating in `heat`, cooling otherwise) and returns the other
register to the last value it read from the thermostat — the same rule the
native integration follows.

> **Behaviour change**: before this version the ESP32 entity always announced
> two setpoints. After updating the external component it exposes only one,
> unless you add `dual_setpoint: true`.

## Extra entities on the madoka platform

Every `climate: - platform: madoka` block can expose auxiliary entities:

- `outdoor_temperature` — outdoor temperature sensor
- `clean_filter` — binary sensor, on when the filter needs cleaning
- `firmware_version` — diagnostic text sensor for the version read from the controller
- `eye_brightness` — number (0-19) setting the front LED brightness
- `reset_filter` — button acknowledging the filter alert and resetting its timer

## The madoka_vam component (VAM ventilation)

For a Daikin **VAM ventilation unit** (heat recovery ventilation), use the
dedicated **`madoka_vam`** platform instead of `madoka`. A VAM is driven by the
same BRC1H controller over the same BLE service, but it only ventilates:
operation mode `5` (VENTILATION), with no temperature setpoint.

Entities exposed: **Off** / **Fan only** modes, fan speed (LOW/HIGH),
ventilation mode preset (*Auto*, *Heat exchange*, *Bypass*) and current
temperature. A VAM is an indoor unit, so it exposes no outdoor temperature
sensor. Options: `firmware_version` (text sensor) and `dump_raw` (boolean).

Fan speed and ventilation mode travel on BLE function `0x0031` (arguments
`0x21` and `0x20`), not on the thermostat's `0x0050`.

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/dasimon135/daikin_madoka
      ref: v3.12.1        # replace with the latest tag
      path: esphome/components
    components: [madoka_vam, madoka_base]

esp32_ble:
  io_capability: display_yes_no

esp32_ble_tracker:
  max_connections: 2

bluetooth_proxy:
  active: false

ble_client:
  - mac_address: "AA:BB:CC:DD:EE:FF"  # your VAM's MAC address
    id: vam_client
    on_numeric_comparison_request:
      then:
        - ble_client.numeric_comparison_reply:
            id: vam_client
            accept: true
    on_disconnect:
      then:
        - ble_client.connect: vam_client

climate:
  - platform: madoka_vam
    name: "VAM ventilation"
    ble_client_id: vam_client
    update_interval: 15s
    firmware_version:
      name: "VAM firmware"
    dump_raw: false  # set true to hex-log BLE frames (reverse engineering)
```

`dump_raw: true` hex-logs every BLE frame and any unknown function received —
useful for mapping VAM-specific functions. See
[../docs/reverse-engineering-vam.md](../docs/reverse-engineering-vam.md).

## File layout

```
esphome/
├── components/
│   ├── madoka/
│   ├── madoka_base/
│   └── madoka_vam/
├── tests/                 # compile-check configs, built by CI
├── example-config.yaml
├── DEPLOYMENT.md
└── README.md
```

## Re-pairing with the phone

While the ESP32 is running it reconnects to the Madoka in a loop. If you clear
every Bluetooth pairing on the thermostat to take control back with the phone
app, the ESP32 grabs the connection again and the phone can never pair.

**Fix**: add a switch that turns BLE off from Home Assistant for the duration.

**Step 1** — give the `esp32_ble_tracker` block an `id`:

```yaml
esp32_ble_tracker:
  id: ble_tracker
  max_connections: 2
```

**Step 2** — add the switch and the scripts:

```yaml
switch:
  - platform: template
    name: "Madoka proxy enabled"
    id: proxy_enabled
    optimistic: true
    restore_mode: RESTORE_DEFAULT_ON
    turn_on_action:
      - script.execute: start_ble
    turn_off_action:
      - script.execute: stop_ble

script:
  - id: stop_ble
    then:
      - logger.log: "BLE off - stopping the scan and disconnecting the thermostats"
      - lambda: |-
          id(ble_tracker).stop_scan();
      - ble_client.disconnect: madoka_salon
      - ble_client.disconnect: madoka_parents

  - id: start_ble
    then:
      - logger.log: "BLE on - resuming the scan and reconnecting"
      - lambda: |-
          id(ble_tracker).start_scan();
      - ble_client.connect: madoka_salon
      - ble_client.connect: madoka_parents
```

**Re-pairing procedure:**

1. In Home Assistant, switch **"Madoka proxy enabled"** to **OFF**
2. Pair your phone with the Madoka and make your changes
3. Switch it back **ON** — the ESP32 reconnects on its own

> `stop_scan()` alone is not always enough. What actually releases the
> thermostat for the phone app is the `ble_client.disconnect` actions.

> The switch is already part of [`example-config.yaml`](example-config.yaml).

## Troubleshooting

### The component does not load

Check that `external_components.source.path` points at the components folder.
For the git source that value is `esphome/components`; for a local copy it is
wherever you put the folder, relative to your ESPHome configuration directory.

### The build fails with `Component not found: madoka_base`

Either `madoka_base` is missing from `components:`, or the tag in `ref:`
predates v3.11.0, which is when the shared transport was introduced.

### The node connects but nothing responds

The link is almost certainly unauthenticated. See [Pairing](#pairing) — both
`io_capability: display_yes_no` and the `on_numeric_comparison_request`
responder are required.

## Credits

- Original madoka component: [Petapton/esphome](https://github.com/Petapton/esphome)
