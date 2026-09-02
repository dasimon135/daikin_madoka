# VAM (ventilation) support plan

**Status:** implemented 2026-07-17. Basic ventilation control on both platforms;
VAM-specific features to follow after reverse engineering.

## Context

The repository ships two independent integrations for the Daikin Madoka BRC1H
controller:

- **HA integration** `custom_components/daikin_madoka/` (uses external `pymadoka-ng`).
- **ESPHome component** `esphome/components/madoka/`.

A **VAM** (Ventilation Air Management / HRV) is an indoor unit driven by the same
BRC1H over the same BLE protocol. It is *ventilation only*: operation mode `5`
(VENTILATION), no heating/cooling setpoint.

Reference (ESPHome-only, older base): `Frank802/esphome@madoka-vam` → component
`madoka_vam` (OFF + FAN_ONLY → mode 5, fan LOW/HIGH, indoor temp).

Key protocol facts (blafois reverse engineering):

- Same service UUID `2141e110-…`, NOTIFY `…e111`, WWR `…e112`.
- Fn `0x0030` GetOperationMode: arg `0x20` current mode; `5` = VENTILATION.
- Fn `0x0110` GetSensorInformation: `0x40` indoor °C, `0x41` outdoor °C.
- `pymadoka` `OperationModeEnum` already has `VENTILATION = 5`.
- HA `climate.py` maps did not handle VENTILATION, and `FAN_ONLY` already maps to
  `FAN (0)` → a device-type distinction is required to send `VENTILATION (5)`.

## What shipped

### ESPHome `madoka_vam` component

New `esphome/components/madoka_vam/{__init__.py,climate.py,madoka_vam.h,madoka_vam.cpp}`.

- Built on this repo's modern `madoka` transport (chunk queue drained in `loop()`,
  send retries, `ESPBTUUID::from_raw`), not the older reference chunk handling.
- Climate traits: OFF + FAN_ONLY (→ mode 5); fan LOW/MEDIUM/HIGH/AUTO; current
  temperature.
- Reads setting status, operation mode (5 → FAN_ONLY), fan speed (from the `0x20`
  slot regardless of mode — the reference only read it in cool/heat branches and
  therefore never read it for a VAM), sensor info (indoor), optional
  `outdoor_temperature` and `firmware_version`.
- Reverse-engineering aids: `dump_raw` flag (hex-logs every in/out frame and any
  unhandled function ID), public `send_raw_command(cmd, args)` callable from a
  lambda to probe undocumented functions.

### HA integration

- `const.py`: `CONF_DEVICE_TYPE`, `DEVICE_TYPE_THERMOSTAT` (default) /
  `DEVICE_TYPE_VENTILATION`.
- `config_flow.py`: appliance-type selector in the user and bluetooth-confirm
  steps, stored in `entry.data`.
- `climate.py`: the entity adapts to the device type. For ventilation:
  `hvac_modes = [OFF, FAN_ONLY]`, no target-temperature feature, `FAN_ONLY ↔
  VENTILATION (5)` via per-instance mode maps, `hvac_action` FAN/OFF.
- `strings.json` + `translations/{en,es,fr}.json`: appliance-type labels.

### Reverse-engineering tooling

- `docs/reverse-engineering-vam.md`: capture methods (Android HCI snoop log,
  nRF Sniffer, macOS PacketLogger), known-protocol table, ESP32 probe
  (`send_raw_command` / `dump_raw`) usage, VAM unknowns checklist, recording
  template.

## Follow-up (after reverse engineering)

Map VAM-specific features once captured: ventilation sub-modes (auto / bypass /
heat-exchange), airflow presets, filter life + reset, CO₂/humidity sensors,
independent supply/exhaust speeds. See the unknowns checklist in
`docs/reverse-engineering-vam.md`.
