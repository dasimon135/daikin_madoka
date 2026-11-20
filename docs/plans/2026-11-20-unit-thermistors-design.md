# Unit status thermistors (#162) - design

Expose the thermistor readings the Madoka Assistant app shows on its
"Unit status" screen: indoor Th1 to Th3 and outdoor Th1 to Th3.

## Protocol

Found by the reporter of #162 from an Android HCI snoop capture of the app,
and replayed from an ESP32 with `send_raw_command`. The decoded frames
(`madoka_unit_status_frames.txt`, attached to #162) confirm each step.

1. Function `0x4111`, argument `0xFE = 01`: start thermistor monitoring.
2. Function `0x0111`, indoor page: `0x01 = 00` (page), `0x02 = 00` (unit),
   then arguments `0x40` to `0x4F` with length 0.
3. Function `0x0111`, outdoor page: `0x01 = 01`, `0x02 = 00`, then only
   `0x40 0x41 0x43 0x44 0x45 0x46 0x4A 0x4B 0x4C`.
4. Function `0x4111`, argument `0xFE = 00`: stop monitoring.

The first answer to a page has every length set to `0xFF` (acquisition
pending). The app asks again every 0.5 to 0.7 s; the values arrive on the
third or fourth answer. Asking for the outdoor page while the indoor one is
still pending, or for an argument the page does not have, returns an empty
answer.

Values: arguments `0x4A`, `0x4B`, `0x4C` are 2-byte big-endian signed
integers in whole degrees Celsius. `FF FF` means the sensor is not fitted
("--" in the app).

| Argument | Indoor page (`0x01 = 00`) | Outdoor page (`0x01 = 01`) |
|---|---|---|
| `0x4A` | Th1 suction air | Th1 outdoor air |
| `0x4B` | Th2 heat exchanger, liquid | Th2 heat exchanger |
| `0x4C` | Th3 heat exchanger, gas | Th3 compressor discharge |

Other arguments (indoor `0x40` = ASCII "OFF", `0x49`, outdoor `0x45`/`0x46`)
are not understood and not exposed.

## Split of responsibilities

**pymadoka-ng** carries the protocol: a new `UnitThermistors` feature in
`pymadoka/features/unit_thermistors.py` whose `query()` runs the whole
sequence. Each page is asked at most 6 times, 0.6 s apart; the outdoor page
is only asked once the indoor one has values. The stop command is sent in a
`finally`, and a failure of the stop command itself is ignored.

`Controller` does not instantiate it: `Controller.update()` queries every
feature it holds on each poll, and this sequence must not run every 60 s.
The module exports the class; the caller decides when to run it.

**daikin_madoka** decides when:

- The coordinator creates the feature only if at least one thermistor
  entity of that thermostat is enabled in the entity registry. Home
  Assistant reloads the config entry when an entity is enabled, so this is
  read at setup.
- After `controller.update()`, inside the same `POLL_TIMEOUT`, it runs the
  sequence when the last successful read is 300 s old or more.

## Errors

- A page still pending after 6 answers raises `TimeoutError` in
  pymadoka-ng. The integration logs it at debug level; the poll itself
  still succeeds, so climate, filter and defrost are unaffected.
- After a failed read the previous values stay for up to 900 s (three
  intervals), then become `None` so the entities turn unavailable instead
  of showing a frozen value. The next attempt is the next poll, not 300 s
  later.
- A dropped link during the sequence raises `ConnectionException` like any
  other feature query.
- A value that is not 2 bytes long counts as pending. A page that answers
  empty sets its three sensors to `None`.

## Entities

Six temperature sensors per thermostat, entity category diagnostic,
disabled by default: indoor suction air, indoor liquid pipe, indoor gas
pipe, outdoor air, outdoor heat exchanger, compressor discharge.
`None` means unavailable.

## Out of scope

- Indoor Th4 to Th6 (`0x4D` to `0x4F`): only ever seen as `FF FF`.
- The remote sensor read through `0x4114` / `0x0114`: its encoding is not
  confirmed, and function `0x0110` already gives the indoor temperature.
- The ESPHome component: a separate change, as for defrost.

## Testing

- pymadoka-ng: unit tests replaying the real frames from #162 (pending
  then values, indoor then outdoor), `FF FF` to `None`, an empty outdoor
  page, the 6-answer cap, the stop command sent after an error.
- daikin_madoka: the feature exists only with an enabled thermistor entity,
  runs at most once per 300 s, a failure does not fail the poll, values
  expire after 900 s, six entities disabled by default.
- Real hardware: enable one thermistor sensor on one of the maintainer's
  BRC1H and read the debug log of the sequence before any release. Whether
  those units answer both pages is unknown until then.

## Delivery

pymadoka-ng 0.6.0, then daikin_madoka 3.16.0-beta.2 on main (it already
carries the defrost sensors of 3.16.0-beta.1).
