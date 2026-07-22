# ESPHome Bluetooth Proxy Reference for the Daikin BRC1H (Madoka)

The BRC1H only talks to clients over an **authenticated (MITM) Bluetooth
link** — on an unauthenticated connection it silently ignores every command
and even notification subscriptions. The **stock ESPHome bluetooth-proxy
firmware cannot establish that link**: it runs with `io_capability: none`
(so it cannot take part in a numeric-comparison pairing), and nothing in
the stock firmware answers the pairing confirmation event, so an
HA-initiated pairing shows a prompt on the thermostat screen and then
times out. This document is the reference configuration that fixes both:
it enables MITM-capable pairing on the proxy and adds a small
**pairing responder** per thermostat that confirms the numeric comparison
and tells you, via a Home Assistant notification, which code to check on
which thermostat screen.

> ## The golden rule
>
> **Every ACTIVE proxy in range must be paired with each thermostat it can
> reach.** The Bluetooth bond lives on the proxy, not in Home Assistant —
> so a bond with proxy A does nothing for proxy B. Home Assistant may route
> any connection through any active proxy in range; an active proxy that is
> *not* bonded will accept the connection and then fail with
> "Insufficient authentication", making the thermostat unreachable.
>
> For each active proxy you have two options:
>
> 1. **Pair it once per thermostat**: flash the configuration below, then
>    watch for the pairing prompt on the thermostat screen (and the
>    matching notification in Home Assistant) and confirm it.
> 2. **Keep the proxy passive** (`bluetooth_proxy: active: false`): a
>    passive proxy only relays advertisements and never carries
>    connections, so it never needs a bond.

## Full configuration

The example below configures one proxy (`living-room-proxy`) that serves
two thermostats (`thermostat_living_room` and `thermostat_bedroom`).
Replace the MAC addresses with your thermostats' addresses (shown in the
integration's device page, or in the BRC1H Bluetooth menu).

```yaml
esphome:
  name: living-room-proxy
  friendly_name: Living Room Proxy

# Start from the stock bluetooth-proxy package for your board
# (https://github.com/esphome/bluetooth-proxies), then add everything below.
packages:
  esphome.bluetooth-proxy: github://esphome/bluetooth-proxies/m5stack/m5stack-atom-s3.yaml@main

# --- Pairing support for the Daikin BRC1H (Madoka) ---
# io_capability is the only change the stock proxy package needs: it enables
# the MITM-capable pairing (display + yes/no confirmation) the BRC1H
# requires, overriding the esp32_ble default (io_capability: none), which
# the BRC1H rejects. No sdkconfig_options are needed — the Bluedroid stack
# already ships with SMP enabled and persists bonds to NVS across reboots
# by default (CONFIG_BT_BLE_SMP_ENABLE and CONFIG_BT_BLE_SMP_BOND_NVS_FLASH
# are both default y; the CONFIG_BLE_SM_* options previously listed here
# are NimBLE symbols that Bluedroid never reads).
esp32_ble:
  io_capability: display_yes_no

# Pairing responders — one per thermostat this proxy can reach.
# They never connect on their own (auto_connect: false); they only react
# when Home Assistant initiates a pairing through this proxy:
#   1. push the 6-digit numeric-comparison code to HA as a persistent
#      notification (so you know which thermostat is pairing through
#      which proxy, and can compare the code on the physical screen),
#   2. auto-confirm the comparison on the proxy side.
# You still confirm the prompt once on each thermostat screen, per proxy.
ble_client:
  - mac_address: "AA:BB:CC:DD:EE:01"   # thermostat_living_room
    id: madoka_living_room_pairing
    auto_connect: false
    on_numeric_comparison_request:
      then:
        - homeassistant.action:
            action: persistent_notification.create
            data:
              notification_id: madoka_pairing_living_room_proxy_living_room
              title: "Madoka pairing — Living Room (via living-room-proxy)"
              message: !lambda 'return "Code on the thermostat screen: " + str_sprintf("%06u", passkey);'
        - ble_client.numeric_comparison_reply:
            id: madoka_living_room_pairing
            accept: true

  - mac_address: "AA:BB:CC:DD:EE:02"   # thermostat_bedroom
    id: madoka_bedroom_pairing
    auto_connect: false
    on_numeric_comparison_request:
      then:
        - homeassistant.action:
            action: persistent_notification.create
            data:
              notification_id: madoka_pairing_living_room_proxy_bedroom
              title: "Madoka pairing — Bedroom (via living-room-proxy)"
              message: !lambda 'return "Code on the thermostat screen: " + str_sprintf("%06u", passkey);'
        - ble_client.numeric_comparison_reply:
            id: madoka_bedroom_pairing
            accept: true

api:
  encryption:
    key: !secret living_room_proxy_api_key

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
```

To keep a proxy **passive** instead (relay advertisements only, never
carry connections — no pairing needed on it):

```yaml
bluetooth_proxy:
  active: false
```

## Why the pairing-code notification matters

In a household with several thermostats and several proxies, a bare
pairing prompt on a thermostat screen is ambiguous: you don't know which
proxy triggered it, and with two units pairing close together you can't
tell which prompt belongs to which connection. The notification removes
the guesswork:

- the **title** names the thermostat *and* the proxy
  ("Madoka pairing — Bedroom (via living-room-proxy)"), so you always
  know which bond is being created;
- the **message** carries the same 6-digit code the BRC1H shows on its
  screen, so you can verify you are confirming the right prompt before
  pressing accept on the unit.

> **Required HA setting**: the `homeassistant.action` call only works if
> the ESP device is allowed to perform actions. In Home Assistant go to
> **Settings → Devices & Services → ESPHome →** *(your proxy device)* and
> enable **"Allow the device to perform Home Assistant actions"**. Without
> it the pairing still succeeds — you just won't get the notification.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Entities unavailable; logs show `Insufficient authentication` | An **unpaired ACTIVE proxy** is winning the connection (it has the strongest signal but no bond) | Pair that proxy: flash the config above, trigger a reconnect, and watch for the prompt + notification — or set it to `bluetooth_proxy: active: false` |
| Pairing times out; prompt appears on the thermostat screen and then disappears | The numeric-comparison prompt was not answered on the thermostat | Retry and **confirm the prompt on the thermostat screen within a few seconds** (the proxy side is auto-confirmed by the responder) |
| A discovery card appears for a Madoka you don't recognize | Likely a neighbour's out-of-home BRC1H at the edge of range | Since v3.2.0 the integration ignores discoveries below −90 dBm; on older versions, just ignore/dismiss the card |
| A `pairing_required` repair shows up in Home Assistant | Every connection path refused the link for lack of a bond | Open the repair — it **names the proxies that refused**; pair each of them (or make them passive) following the steps above |

Two more classic pitfalls:

- **Pairing loops** (prompt appears, fails, re-appears): the thermostat
  holds a stale bond. Un-pair on the BRC1H (Bluetooth menu → forget) and
  retry.
- **Reflashing a proxy** normally keeps its bonds (they live in NVS), but
  a full flash-erase or a board swap loses them — the affected proxy must
  be paired again with each thermostat (or set passive until it is).

## See also

- The [README Requirements section](../README.md#requirements) — the
  minimal snippet, plus pairing via the HA host's own Bluetooth adapter.
