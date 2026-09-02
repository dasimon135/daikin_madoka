# Reverse-engineering the Daikin VAM (ventilation) over BLE

The VAM (Ventilation Air Management / heat-recovery ventilation unit) is driven
by the **same BRC1H remote controller** as a regular Madoka thermostat, over the
**same Bluetooth service and protocol**. The difference is functional: a VAM only
ventilates — its operation mode is `5` (VENTILATION) and it has no
heating/cooling setpoint.

Basic support (on/off, fan speed, indoor temperature) is already
implemented in both integrations. This guide is for discovering the
**VAM-specific features** that are not yet mapped — automatic/bypass ventilation
modes, airflow presets, filter status, CO₂/humidity sensors, etc.

> Base protocol reference:
> [blafois/Daikin-Madoka-BRC1H-BLE-Reverse](https://github.com/blafois/Daikin-Madoka-BRC1H-BLE-Reverse).

## 1. BLE transport (shared with the thermostat)

| Role | UUID |
|---|---|
| Service | `2141e110-213a-11e6-b67b-9e71128cae77` |
| Notify (RX, controller → client) | `2141e111-213a-11e6-b67b-9e71128cae77` |
| Write-without-response (TX, client → controller) | `2141e112-213a-11e6-b67b-9e71128cae77` |

Every exchange is a small frame, fragmented into ≤20-byte chunks. Each chunk is
prefixed with a chunk index (`0x00`, `0x01`, …); chunk `0x00` also carries a
total-length byte. A reassembled message looks like:

```
[len][0x00][cmd_hi][cmd_lo][arg_id][arg_len][arg_bytes...] ...
```

- **Function ID** = `cmd_hi << 8 | cmd_lo`. `0x00xx` = GET (query), `0x40xx` = SET.
- The body is a list of TLV arguments: `arg_id`, `arg_len`, then `arg_len` bytes.

## 2. Known functions

| Function ID | Direction | Meaning | Key arguments |
|---|---|---|---|
| `0x0020` / `0x4020` | get/set | Power (setting status) | `0x20` → on/off (0/1) |
| `0x0030` / `0x4030` | get/set | Operation mode | `0x20` → mode (**5 = VENTILATION**) |
| `0x0031` / `0x4031` | get/set | **Ventilation** | `0x20` → ventilation mode, `0x21` → fan speed |
| `0x0050` / `0x4050` | get/set | Fan speed (thermostat only) | `0x20` cooling slot, `0x21` heating slot (0=AUTO, 1=LOW, 3=MID, 5=HIGH) |
| `0x0110` | get | Sensor information | `0x40` indoor °C (`0x41`, outdoor °C on a thermostat, is not read: a VAM is indoor-only) |
| `0x0130` | get | Version | `0x45` RC version, `0x46` BLE version, `0x40` ASCII model name |

### The VAM does not use `0x0050`

This was confirmed on a VAM350J8VEB (RC 1.10.3 / BLE 5.17) by polling the unit
while it was driven from its own wall controller. `0x0050` *answers*, but every
argument comes back with length 0 and **none of them ever change** — the same is
true of `0x0030`. Airflow lives on `0x0031` instead:

| Argument | Meaning | Values |
|---|---|---|
| `0x12` | Supported ventilation modes | Bitmask; `0x07` = modes 0, 1 and 2 supported |
| `0x20` | **Ventilation mode** | `0` = automatic, `1` = heat exchange, `2` = bypass |
| `0x21` | **Fan speed** | `1` = LOW, `3` = MID, `5` = HIGH (same encoding as `0x0050`) |

Two useful behaviours of this firmware:

- An **unsupported function ID answers `06:00:<hi>:<lo>:FF:00`** — a 6-byte frame
  whose only argument is `0xFF` with length 0. Sweeping GET IDs and discarding
  that sentinel is a quick way to enumerate what a unit really supports.
- An **out-of-range argument value is ignored silently**, with no error frame.
  Writing fan speed `0x03` to a two-speed VAM leaves it exactly where it was, so
  never assume a write succeeded — read the value back.

Both integrations drive `0x0031` directly. The ESPHome `madoka_vam` component
maps argument `0x21` to the climate fan mode and argument `0x20` to a custom
preset; the Home Assistant integration ships its own `Ventilation` feature in
`custom_components/daikin_madoka/ventilation.py`, because pymadoka-ng has no
class for this function and models fan speed as `0x0050` only. Every write
carries **one** argument: the unit applies whatever it is sent and never reports
a rejection, so a stale companion argument would quietly overwrite the value it
was not meant to touch.

## 3. Capturing VAM traffic

You need to see what the **official Daikin app** sends/receives while you drive
the VAM through its full feature set. Pick one method:

### A. Android HCI snoop log (no extra hardware)

1. On the phone: **Settings → Developer options → Enable Bluetooth HCI snoop log**
   (set to *Enabled* / *Filtered*).
2. Toggle Bluetooth off/on so logging starts fresh.
3. Open the Daikin app and exercise **one feature at a time**, pausing a few
   seconds between actions (note the wall-clock time of each action — it makes
   the capture far easier to read).
4. Pull the log and open it in [Wireshark](https://www.wireshark.org/):
   - `btsnoop_hci.log` location varies; on many devices a **bug report**
     (`Developer options → Bug report`) contains it under
     `FS/data/misc/bluetooth/logs/`.
5. In Wireshark, filter with `btatt` and look for writes/notifications on the
   handles of the `2141e11x` characteristics.

### B. nRF Sniffer for Bluetooth LE (dedicated hardware, most reliable)

1. Flash an **nRF52840 dongle** with the
   [nRF Sniffer for BLE](https://www.nordicsemi.com/Products/Development-tools/nRF-Sniffer-for-Bluetooth-LE)
   firmware and install the Wireshark plugin.
2. Start the capture, select the VAM's controller as the target device, then run
   the Daikin app. Pairing/bonding must be captured from the start (the link is
   encrypted), or use the app on a fresh bond.

### C. macOS PacketLogger

If you pair the controller with a Mac, Apple's **PacketLogger** (part of
*Additional Tools for Xcode*) records all HCI traffic without extra hardware.

## 4. Reading a capture

For each app action, isolate the ATT **Write Command** to the WWR characteristic
(TX) and the following **Handle Value Notification** (RX). Decode with the frame
layout above:

- New function IDs (outside the table in §2) are VAM-specific — record them.
- For a *known* function, look for **new argument IDs** or new value ranges
  (e.g. an operation-mode sub-argument selecting *auto* vs *bypass* ventilation,
  or a new function for airflow volume).

## 5. Probing from the ESP32 (`madoka_vam`)

The ESPHome `madoka_vam` component includes reverse-engineering aids:

- **`dump_raw: true`** — hex-logs every BLE frame (TX and RX), and logs any
  unhandled function ID it receives. Enable it and watch the ESPHome logs while
  the unit runs to see what the controller reports unprompted.
- **`send_raw_command(cmd, args)`** — a public method callable from a lambda to
  send an arbitrary function ID + TLV arguments and observe the reply in the log.

Example: query an unknown function `0x0140` and dump the response.

```yaml
climate:
  - platform: madoka_vam
    id: vam
    name: "VAM"
    ble_client_id: vam_client
    dump_raw: true        # journalise chaque trame BLE en hexadécimal

# Bouton de sonde : envoie une commande brute et affiche la réponse dans les logs
button:
  - platform: template
    name: "VAM probe 0x0140"
    on_press:
      - lambda: |-
          // function id 0x0140, arguments TLV vides
          id(vam).send_raw_command(0x0140, std::vector<uint8_t>{0x00, 0x00});
```

Sweeping GET function IDs (`0x00xx`) is safe (read-only). Be cautious with SET
functions (`0x40xx`) — they change the unit's state.

## 6. VAM unknowns — checklist to capture

Record which of these your VAM supports and the corresponding frames:

- [x] Ventilation sub-modes — `0x0031` arg `0x20`: automatic / heat exchange / bypass.
      Night purge was not offered by the tested unit.
- [x] Airflow volume — `0x0031` arg `0x21`, LOW/MID/HIGH. No numeric m³/h steps were
      found; the tested VAM350J8VEB is two-speed and ignores MID.
- [ ] Filter status / filter-life counter and its reset command
- [ ] CO₂, humidity or air-quality sensor readings
- [ ] Independent supply vs exhaust fan speeds
- [ ] Timers / scheduling
- [ ] Error / fault codes

## 7. Recording template

Copy this block per discovered feature into a capture notes file or a PR:

```
Feature:            <e.g. "bypass ventilation ON">
App action:         <what you did in the Daikin app>
TX (write):         <hex frame(s) sent>
RX (notify):        <hex frame(s) received>
Function ID:        0x____
Argument(s):        id=0x__ len=__ value=__ meaning=____
Notes:              <encoding, units, value range, side effects>
```

Once a feature is decoded, map it in:

- ESPHome: `esphome/components/madoka_vam/madoka_vam.{h,cpp}` (add the function ID
  constant, parse it in `parse_cb_`, expose a trait/entity).
- Home Assistant: extend `custom_components/daikin_madoka/` (the mapping lives in
  `climate.py`; new sensors go through the coordinator).
