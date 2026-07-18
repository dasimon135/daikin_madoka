# Multi-proxy robustness — design

**Target:** daikin_madoka v3.2.0 + pymadoka-ng 0.3.6
**Date:** 2026-07-18
**Status:** validated (brainstorming session)

## Problem

Home Assistant routes BLE connections through whichever proxy has the best
RSSI, but BLE bonds with the BRC1H are **per proxy**. When those two facts
disagree — a nearby proxy wins the RSSI pick but holds no bond — the
connection is rejected with `Insufficient authentication` and the thermostat
goes `unavailable` with no explanation. Field incident (2026-07-17): enabling
active connections on the closest proxy (no bond) took the Salon thermostat
down; recovery required reverting the proxy to passive.

Two adjacent failure modes surfaced in the same session:

- **Ghost discovery**: a foreign BRC1H (neighbour, −95 dBm) produced a
  discovery card indistinguishable from a real device. Adding it created a
  config entry stuck in `setup_retry` forever.
- **Registry orphans**: a deleted entry left devices/entities pointing at a
  nonexistent config entry (`Can't link device to unknown config entry`
  errors at startup).

An integration cannot prevent HA from using other proxies, so it must embrace
the multi-proxy reality instead of assuming one bonded path.

## Design — three pillars

### Pillar A — Sticky proxy (root cause)

Remember, per config entry, the proxy that last served a successful
**authenticated** connection, and ask for that path explicitly on reconnect
instead of letting RSSI decide.

- Storage: `entry.data["preferred_source"]` = proxy source MAC. Persists
  across restarts; invisible to the user.
- Connection sequence (integration → pymadoka):
  1. Collect **all paths** to the thermostat via
     `bluetooth.async_scanner_devices_by_address()` — one `BLEDevice` per
     proxy that sees it.
  2. Order them: `preferred_source` first, then the rest by descending RSSI.
  3. pymadoka-ng gains a **candidate-list API**: try each path in order,
     report which one succeeded.
  4. On authenticated success, write the winning `source` back to
     `preferred_source`. The system converges on its own: the first success
     sticks.
- Edge cases:
  - Preferred proxy gone → simply absent from the candidate list, fall
    through to the others.
  - Auth fails on **all** paths → `PairingRequiredError` carrying the list of
    attempted proxies → repair (Pillar B).
  - Local adapter: just another path in the list, same logic.
  - Single proxy in range: behaviour identical to today; zero regression.
- Implementation watch-item: make sure bleak-retry-connector's
  `establish_connection` does not override the chosen path with its own
  "best path" logic — use its `ble_device_callback` parameter to keep
  control if needed.

### Pillar B — Failures speak (never die silently)

**pymadoka-ng error taxonomy** (today everything collapses into a generic
error):

- `PairingRequiredError` — GATT error 5 "Insufficient authentication",
  pairing error 97, pairing timeout (PR #3 plugs in here). Carries the
  thermostat MAC **and the proxy `source`** that failed.
- `DeviceUnreachableError` — not found / out of range / connection timeout.
- Everything else stays generic `MadokaError` with silent retry, as today.

**Integration coordinator routing:**

- `PairingRequiredError` (all paths failed) → repair **`pairing_required`**
  raised immediately (an auth refusal never heals on its own). Text names
  the proxy human-readably (via `bluetooth.async_scanner_by_source()`):
  "*Salon* refuses the connection through proxy *atomebuanderie*: this proxy
  is not paired with it. Confirm the pairing prompt on the thermostat
  screen, or set this proxy to passive mode." Links to the pairing docs.
- `DeviceUnreachableError` → existing v3.0.0 "device unreachable" repair
  after N consecutive failures, unchanged.
- Both repairs self-clear on the first successful poll (existing mechanic).
- **No spam rule**: thanks to the sticky proxy, an auth failure on one proxy
  is not fatal (a bonded path succeeds). `pairing_required` is only raised
  when auth fails on **every** path — the one case where the user must act.
  A failed unbonded path with a successful fallback is logged at INFO.
- Bonus: sensors keep their last value for a few cycles on transient errors
  (no graph holes on micro-drops); `climate` availability stays truthful.

### Pillar C — Sane discovery & onboarding

- **RSSI floor**: in `async_step_bluetooth`, abort silently when the
  advertisement is below **−90 dBm** — no discovery card for out-of-home
  devices. Constant threshold (no option, YAGNI). Manual add by MAC does
  NOT filter, keeping a door open for edge cases.
- **Connection test before entry creation** (discovery-confirm AND manual
  flows): attempt a full authenticated connection (connect + pair + one
  read) before `async_create_entry`.
  - Success → create the entry and immediately store the winning
    `preferred_source` (sticky proxy starts primed).
  - Failure → re-show the form with `cannot_connect`, or `pairing_failed`
    for `PairingRequiredError` ("confirm the prompt on the thermostat
    screen"). **Nothing is created** — a `setup_retry` ghost entry becomes
    impossible.
  - Generous timeout (~30 s) because pairing may need the physical
    confirmation; the form says "stay near the thermostat".
- **Registry hygiene**:
  1. Implement `async_remove_config_entry_device` for clean per-device
     removal from the UI.
  2. At setup, purge registry devices whose config entry no longer exists
     (cleans existing orphans and the startup errors they cause).

## ESPHome reference doc

`docs/esphome-proxy.md` (English): recommended proxy config —
`io_capability: display_yes_no`, SMP sdkconfig options, one `ble_client`
pairing responder per thermostat **with the pairing-code HA notification**
(persistent_notification.create with the 6-digit passkey, so multi-thermostat
households know which unit is pairing through which proxy). Golden rule
stated up front: *every active proxy in range must be paired with the
thermostat — otherwise keep it passive.* README pairing section links here.

## Tests

Extend the v3.1.1 pytest suite:

- Flow: discovery below −90 dBm ignored; `cannot_connect` / `pairing_failed`
  create no entry; success stores `preferred_source`.
- Coordinator: `PairingRequiredError` on all paths → `pairing_required`
  repair; one bonded path OK → no repair; self-clear on recovery.
- Sticky proxy: candidate ordering honours `preferred_source`; updated after
  a failover.
- pymadoka-ng (its own repo): GATT error → typed exception mapping.

## Release plan (order matters)

1. **pymadoka-ng 0.3.6** → PyPI: typed exceptions + candidate-list API +
   PR #3 (pairing-timeout message).
2. **daikin_madoka v3.2.0**: manifest `pymadoka-ng==0.3.6`, pillars A/B/C,
   ESPHome doc, CHANGELOG.

Field validation on the maintainer install (4 thermostats, 4 proxies) before
tagging. Success criterion = the 2026-07-17 incident replayed: *an active,
unbonded proxy close to the Salon thermostat → Salon stays reachable and a
clear repair appears.*
