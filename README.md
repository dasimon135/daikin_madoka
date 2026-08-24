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
- `sensor.*_connection_source` — which BLE path serves the thermostat: active proxy while connected, preferred (bonded) proxy otherwise (diagnostic)
- `sensor.*_connection_status` — `connected` / `retrying` / `pairing_slow` / `needs_pairing` / `not_advertising` (diagnostic). Tells the failures apart at a glance: `not_advertising` means no proxy can see the thermostat (range, power), `needs_pairing` means a proxy was explicitly refused and you must re-pair, `pairing_slow` means the handshake keeps timing out (often just a busy proxy). Like `signal_strength` and `connection_source`, it stays available while the thermostat does not — those three are what you read when everything else is `unavailable`.
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

##### When a thermostat stops connecting

A thermostat that has been added always loads, even when it cannot be reached: its entities go `unavailable` and Home Assistant keeps retrying in the background, rather than the device disappearing from the UI. Read `sensor.*_connection_status` first — it stays available and says which problem you have. Then, in order of effort:

1. **`not_advertising`** — nothing to re-pair: the thermostat is off, out of range, or its proxy is down.
2. **`pairing_slow`** — the handshake keeps timing out. Often a congested proxy; Home Assistant slows its attempts to one every 15 minutes and recovers on its own. Reloading the proxy's config entry frees stale connection slots and frequently fixes it.
3. **`needs_pairing`** — a proxy explicitly refused the bond, which only a human at the thermostat can fix. Home Assistant raises a repair with a **Fix** button that walks you through it (stand at the thermostat, submit, accept the prompt on its screen). The device's **Reconnect** button does the same thing from the dashboard; the repair also works when the device has no entities at all.

Remember the bond is per proxy: re-pairing restores one path, and another proxy may still need its own prompt.

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
      ref: v3.8.0
      path: esphome/components
    components: [madoka]

# The BRC1H only accepts an authenticated (MITM) link, established through a
# numeric comparison. Both lines below are required — see the note after this
# config for what happens if either is missing.
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
    # Answers the thermostat's numeric-comparison request. Without this the
    # pairing starts, nothing confirms it, and the link fails.
    on_numeric_comparison_request:
      then:
        - ble_client.numeric_comparison_reply:
            id: my_madoka
            accept: true
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

> ### Pairing: why both lines matter
>
> The BRC1H pairs by **numeric comparison** — both sides show a 6-digit code
> and each confirms it matches. It silently ignores every command, and even
> notification subscriptions, on an unauthenticated link.
>
> - **`io_capability: display_yes_no`** lets the ESP32 take part in that
>   exchange at all. The default (`none`) cannot, and `keyboard` offers
>   passkey *entry* — a different pairing model the thermostat never asks for.
> - **`on_numeric_comparison_request`** answers it. Without the responder the
>   pairing starts, the confirmation is never given, and the connection ends
>   as `AuthenticationCanceled` — often without any prompt appearing on the
>   thermostat screen.
>
> On the first connection the thermostat shows the pairing prompt: accept it
> within a few seconds. The bond is stored on the ESP32 and survives reboots.
>
> **If pairing already failed several times**, the BRC1H's Bluetooth stack
> stays jammed and will refuse even a correct configuration. On the
> thermostat: Bluetooth menu → forget the pairing, then toggle Bluetooth off
> and on again before retrying.

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

### Options

| Option | Default | Description |
|---|---|---|
| `dual_setpoint` | `false` | Advertise two setpoints (heating/cooling range) instead of one. |

The BRC1H can work with a single setpoint or with a heating/cooling **range**.
The HA integration (Option 1) switches between the two automatically, because
Home Assistant lets an entity change its supported features at runtime. ESPHome
cannot: climate traits are sent once, when the ESP32 lists its entities, so the
choice has to be made at build time.

Leave `dual_setpoint` unset (single setpoint) unless range mode is enabled on
the thermostat itself:

```yaml
climate:
  - platform: madoka
    name: "Living Room"
    ble_client_id: my_madoka
    dual_setpoint: true    # only if range mode is enabled on the BRC1H
```

In single mode the component writes the setpoint register matching the active
mode (heating in `heat`, cooling otherwise) and leaves the other one as the
thermostat last reported it — the same rule the HA integration follows.

> **Behaviour change**: the ESP32 entity used to advertise two setpoints
> unconditionally. After updating the external component, it exposes a single
> setpoint unless you add `dual_setpoint: true`.

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

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/madoka-card-dark.png">
  <img src="images/madoka-card.png" width="820"
       alt="Madoka Card: the full dial layout next to a stack of tile rows, the last one offline showing its Reconnect button">
</picture>

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
# reconnect: auto      # auto | always | never  (default: auto)
```

Three layouts: **full** (the dial with fan/brightness/graph), **compact**
(dial + controls + modes only) and **tile** — an ultra-compact row (a
mode-colored status dot + name + current→target + `−`/`+`) that lines up with
Home Assistant's tile cards in a dense grid.

**Reconnect on the spot**: when a thermostat drops off the air, the card
surfaces its **Reconnect** button right where you noticed the problem — as a
banner in the full/compact layouts, and in the tile layout in place of the
`−`/`+` pair (which is inert while the device is unreachable). It disappears
as soon as the link is back. Set `reconnect: always` to keep it visible at all
times, or `reconnect: never` to hide it.

The related entities (outdoor temperature, eye brightness, filter, signal,
reconnect button) are discovered automatically from the same device — you only
need the `climate.*` entity. It follows your Home Assistant theme and language (mode names use HA's
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

## Known limitations

Things that are expected behaviour rather than defects, and open gaps worth
knowing about before you open an issue. Connection and pairing problems have
their own walkthrough above — see [When a thermostat stops connecting](#when-a-thermostat-stops-connecting).

### Only the European BRC1H is validated

Everything here is confirmed against a BRC1H. Other regional variants are **not
verified**, and one of them cannot be supported over Bluetooth at all.

On a **BRC1H71 (North America)** the Bluetooth link connects and pairs normally,
state reads come back wrong and commands have no effect — and that is not a gap
in this integration. The BLE protocol that controller exposes is the one used by
Daikin's *Quick Set* commissioning app, and it contains no runtime HVAC commands:
power, mode, setpoints and fan speed are exchanged with the indoor unit over the
wired **P1P2** bus instead. This was established from the controller's own
firmware, command whitelist included, in
[#62](https://github.com/dasimon135/daikin_madoka/issues/62).

There is therefore nothing this integration can add for a BRC1H71 over
Bluetooth. Driving one from Home Assistant means being on the P1P2 bus, with
hardware acting as an auxiliary controller — a different project from this one.

If you have a variant that is not a plain BRC1H, say so in your report and
include the exact model marking. Do not assume a fix that worked on a BRC1H
transfers.

### An unattended reconnect can pick a proxy that holds no bond

The bond is stored **per proxy**. Home Assistant's Bluetooth stack
(`habluetooth`) chooses the connection path by signal strength, and that choice
overrides the integration's preference for a proxy that is actually bonded. A
reconnect happening while nobody is around can therefore land on an unbonded
proxy, start a real pairing exchange, and wait for a confirmation on the
thermostat's screen that never comes.

The practical answer is the one in [Requirements](#requirements): give **every**
proxy that can reach the thermostat the pairing YAML, and accept its one-time
prompt. `sensor.*_connection_status` shows `needs_pairing` when this is what
happened.

Tracked in [#58](https://github.com/dasimon135/daikin_madoka/issues/58). The
bookkeeping half of this was fixed in v3.9.0; the routing policy is still open.

### State is polled, not pushed

The integration reads the thermostat on a timer (default **60 s**, configurable
in the **Configure** dialog). A change made at the thermostat itself, or by
another remote, appears in Home Assistant at the next poll — not immediately.
Up to one poll interval of staleness is normal and is not a bug.

Shortening the interval means more Bluetooth traffic and more chances to collide
with another connection; 60 s is a deliberate default.

### Protocol-level behaviour lives upstream

This repository handles the Home Assistant side: entities, discovery, options,
connection management. The Bluetooth protocol itself — GATT characteristics,
command and response encoding, the pairing handshake — is implemented in
[pymadoka](https://github.com/dasimon135/pymadoka), shipped as `pymadoka-ng` and
pinned in `manifest.json`.

A bug in how a command is encoded, or in how the thermostat's answer is parsed,
belongs there rather than here. Report it here anyway if you are unsure — it
gets routed — but include a debug log with **both** loggers enabled:

```yaml
logger:
  default: warning
  logs:
    custom_components.daikin_madoka: debug
    pymadoka: debug
```

Without `pymadoka: debug` the log says almost nothing about the Bluetooth
exchange itself.

---

## Credits

Based on the original work by [@mduran80](https://github.com/mduran80/daikin_madoka).  
ESPHome madoka component adapted from [Petapton/esphome](https://github.com/Petapton/esphome).
