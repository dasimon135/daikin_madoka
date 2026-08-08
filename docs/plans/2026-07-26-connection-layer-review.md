# daikin_madoka — Complete review of the connection / pairing / recovery layer

**Date:** 2026-07-26
**Scope:** four independent read-only reviews (connection state machine, pymadoka-ng
boundary, entity/UX recoverability, bonding & proxy affinity), driven by a live
field incident on a 4-thermostat / 4-proxy installation.
**Status:** diagnosis complete, no code written.

---

## 0. The field incident that triggered this

After an HA restart on a 4-thermostat system behind 4 ESPHome BT proxies:

| Device | Symptom | Real cause (established) |
|---|---|---|
| Bedroom A | recovered | — |
| Parents | reconnected, then **falsely quarantined** ("refused the authenticated bond") | timeout streak on a *bonded* path; the bond was intact — a manual Reconnect restored it instantly with **no on-screen confirmation** |
| Bedroom B | never recovered | genuine auth rejection (error 81) on one path, mixed with other failures |
| Salon | never recovered; user unpaired it to retry, then **could not re-pair by any means** | nested-timeout incoherence made every pairing path structurally unable to complete |

Independently: one proxy's slot pool was saturated by **stale allocations**
(HA core issue #176516), which converted a multi-path device into a de-facto
single-path device. Reloading that proxy's config entry freed the slots.

---

## 1. The single root cause

**The code never states the one invariant that governs this whole subsystem:**

> *A bond is per-proxy. On a path that already holds a bond, no human is
> required — so a pairing **timeout** there can only mean congestion, never a
> missing bond. Only an explicit **rejection** proves a human is needed. A human
> is required exclusively on a path with no bond, and that case is always
> user-initiated.*

Because that invariant was never written down, the subsystem accreted **four
overlapping partial representations** of the same question — `_boot_window`,
`pairing_window`, `timeout_rounds`, `suspended` — each added after a field
incident. Every one of them is a time/state window guarding a different slice of
"is a human needed?", and none of them is the invariant itself.

Two structural consequences follow, and together they explain every symptom above.

### 1a. The library cannot express the distinction, so the integration cannot act on it

`PairingRequiredError` is raised for **two epistemically different events** with
the same type, the same attributes and the same message:

- `pymadoka/connection.py:408-413` — every path failed **and at least one
  explicitly rejected** → proven dead bond.
- `pymadoka/connection.py:415-425` — every path merely **timed out**, three
  rounds → *inferred* dead bond.

`PairingRequiredError.__init__` (`pymadoka/errors.py:24-32`) exposes only
`address` and `tried_sources`. A consumer cannot tell them apart, so the
integration's only possible reaction is the harshest one: indefinite quarantine
plus an ERROR repair (`coordinator.py:237-240`, `:398-405`).

Aggravating factors that make the *inferred* branch fire on healthy hardware:

- **The streak is counted per-Connection, not per-path**
  (`connection.py:405-407`): `every_path_failed_auth = (auth_rejections +
  pair_timeouts == len(candidates))`. With one candidate this is trivially true
  every round; with three, any single non-auth failure resets it to false.
  **Single-path devices are structurally far more likely to be falsely
  quarantined** — and the integration *manufactures* single-path devices by
  restricting automatic reconnects to bonded proxies (`__init__.py:104-115`).
- **No per-candidate connect budget** (`connection.py:324`, `max_attempts=1` in
  the candidates path): a slot-saturated proxy burns its full internal timeout
  (~20 s — the timeout lives in aioesphomeapi, not pymadoka; field-confirmed by
  tonight's `after 20.0s` log lines), so the bonded second candidate is never
  tried within `CONNECT_TIMEOUT`.
- **Rejection evidence is discarded on mixed rounds** (`connection.py:405-407`):
  2 proven rejections + 1 transient failure in a 3-candidate round takes the
  transient branch — rejections carry no state across rounds (unlike timeouts).
  A flapping third proxy can postpone a legitimate pairing conclusion forever.
- **A success via the fallback single path does NOT reset the streak**
  (`connection.py:495-498`): a device that recovers through the fallback keeps
  its accumulated timeout streak armed for later conviction.
- **The classification is unreachable anyway** during the boot window (see 1b).

### 1b. Every timeout budget is nested inside a smaller one, so the inner budgets are fiction

`coordinator.py:234-236` wraps the entire `controller.start()` in
`wait_for(..., CONNECT_TIMEOUT=30)` (`const.py:52`). Inside that:

| Budget | Value | Reality (adversarially re-verified 2026-07-26) |
|---|---|---|
| `PAIRING_WINDOW_TIMEOUT` (human) | 60 s (`const.py:21`) | **dead config** — the inner `wait_for(pair(), 60)` can never fire under the outer 30 s wrap. Per attempt the user gets ~25-27 s of real confirmation time. Because the window stays open on failure, each subsequent poll grants a fresh ~25 s slice — the UX is "25 s slices separated by up-to-a-poll-interval of dead time, repeating", not "can never pair". Still broken, differently. |
| `BOOT_PAIR_TIMEOUT` (v3.7.1) | 30 s (`const.py:33`) | **NOT inert** (earlier claim refuted by numeric walk): `pair()` runs ~27 s of real time before the outer cancel, so the slow-valid-bond boot scenario IS saved (8 s would have killed it). **But** `pair_timeout ≥ outer budget` makes the library's timeout classifier (`connection.py:338-345`) unreachable → **zero evidence is ever counted during the boot window** — see §8 for the regression this causes. |
| config-flow pairing | 8 s (`config_flow.py:124-129`, default) | the one moment a human is *guaranteed* present has the *smallest* budget (several 8 s slices inside `VALIDATE_TIMEOUT=30`) |
| library internal backoff | `sleep(5→10→20→40→60)` + fixed `sleep(2.0)` **inside** the 30 s (`connection.py:433-435`, `:232-233`) | ~15 s+ of the 30 s budget is dead sleep; 1-2 classified rounds per poll at the 8 s budget, zero at ≥~27 s |

**Why the user could not recover Salon by any documented path**: per attempt, the
Reconnect flow grants only ~25 s (not the promised 60), and delete-and-re-add
pairs at 8 s. Recovery was possible in principle only for a user who confirms
within one slice — in practice, with prompts appearing and dying across poll
intervals, it never converged.

---

## 2. The catch-22: the remedy is unreachable exactly when needed

`async_setup_entry` loads degraded (entities present, Reconnect button usable)
**only** when `pairing_state.suspended` is already true (`__init__.py:133`).
Every other failure — not advertising, connect timeout, mixed failures — re-raises
`ConfigEntryNotReady` (`:148`) → `setup_retry` → `async_forward_entry_setups`
(`:170`) is never reached → **no entities at all**, including the Reconnect
button the integration's own notification tells the user to press.

Compounding it:

- **No services whatsoever** (no `services.yaml`, no `async_register*` anywhere),
  so there is no entity-independent escape hatch.
- **Both repairs are `is_fixable=False`** (`coordinator.py:391`, `:440`) — text
  panels with no Fix button.
- **No `async_step_reauth`** — HA's canonical, entity-independent mechanism for
  "our credentials/pairing are no longer accepted" is not implemented.
- **The `device_unreachable` repair can never fire**: `_fail_count`
  (`coordinator.py:110`) is an instance attribute, and HA builds a **fresh
  coordinator on every retry** (`__init__.py:126`) which performs exactly one
  refresh — so the counter never reaches `UNREACHABLE_THRESHOLD = 5`. Two dead
  thermostats produced **zero** notifications. This falsifies the v3.2.0 claim
  that "a config entry stuck forever in `setup_retry` is no longer possible".
- **The card fakes success**: `frontend/madoka-card.js:177-182` sets the
  "reconnecting…" state *before* firing `callService(...)` with **no `.catch`**,
  so a press on a non-existent entity shows a spinner forever.
- **The pairing state is in-memory only** (`hass.data`), so an HA restart wipes
  `suspended` and the streak — the degraded-load escape hatch does not survive a
  restart, while `preferred_source`/`bonded_sources` persistence already exists
  next to it.

---

## 3. Live hazard: a failed Reconnect leaves the system in its most dangerous mode

`coordinator.py:327-328` sets `pairing_window = True` and `pair_timeout = 60`.
The **only** close path is `_clear_issues()` (`:472`), reached from a **successful**
poll (`:181`) or unload. So a *failed* Reconnect leaves the window open
indefinitely, which simultaneously:

1. sets `allowed = None` (`__init__.py:111`) → **the bonded-proxy restriction is
   lifted**, so every automatic poll initiates real SMP on unbonded proxies —
   precisely the pairing storm v3.6.0 exists to prevent;
2. makes `suspended and not pairing_window` false (`:209`) → **the quarantine is
   silently disarmed**;
3. leaves a 60 s SMP budget on every automatic reconnect, monopolising the
   BRC1H's single central slot.

A related leak: after a *successful* user pairing in steady state,
`_close_boot_window()` returns early (`:415-416`) so `pair_timeout` **stays at
60 s forever**.

*Re-verification caveats:* "indefinitely" means until the next successful poll,
an entry reload, or an HA restart (the state is memory-only) — but on a
persistently failing device none of those happen on their own. And per poll the
hazard is throttled: with a 60 s pair budget under the 30 s wrap, candidate 1
usually eats the whole attempt, so unbonded proxies are reached only when
earlier candidates fail fast. The storm is slower than first stated; the
mechanism is exactly as stated.

---

## 4. Bond bookkeeping is wrong in both directions

- **Append-only** (`coordinator.py:358-360`): nothing is ever removed. A
  reflashed/replaced proxy stays "bonded" forever; every reconnect retries that
  dead path, feeding the storm the design exists to prevent.
- **Under-recorded** (`:191`): a source is persisted only after a *fully
  successful poll*. A connect that authenticates but whose poll fails **proves**
  the bond yet records nothing; `on_disconnect` also clears `connected_source`.
- **Silently wiped**: `config_flow.py:269-279` rebuilds `entry.data` from
  scratch on **reconfigure**, dropping `CONF_BONDED_SOURCES` (HA's `data=`
  replaces, it does not merge). *Re-verified nuance:* on a pure rename
  `preferred_source` survives, so the restriction degrades to that single proxy
  — bad (one path instead of four) but not unrestricted; only a MAC change or a
  preferred-less entry disables the anti-storm policy outright.
- **Policy is bypassable**: if `candidates_callback` raises,
  `connection.py:276-284` falls back to `_connect_via_ha_single()`, which uses
  habluetooth's scorer, `max_attempts=3`, and calls `pair()` unconditionally —
  the auto-pairing the policy forbids, reachable via a single exception.
- **"No bonded path" is misreported** as `DeviceUnreachableError` ("device not
  seen by any adapter/proxy") when the device is in fact visible — the user is
  told to check power and range for a device sitting in front of them.
- **No slot awareness**: candidate ordering is `(preferred, RSSI)` only
  (`util.py:59-73`), so a proxy with 0 free slots is offered first, every time.

---

## 5. Cross-device amplification

The global connect lock (`coordinator.py:41-51`, `:233`) is held across the
library's internal `sleep()` backoff. One device with a dead bond parks the
**shared** lock for the full 30 s per poll while doing nothing, delaying healthy
devices' reconnects, increasing proxy congestion, producing more pair timeouts on
healthy devices — the cascade that turns one bad bond into several quarantines.
This is the most plausible mechanism by which Parents (healthy) was dragged down.

*Re-verification nuances:* a device that stopped **advertising** fails before the
lock (`coordinator.py:205-208`) and never takes it — the hazard is confined to
advertising-but-unconnectable devices (which both Salon and Bedroom B were tonight).
Worse than first stated: lock **acquisition has no timeout** and the 30 s timer
starts only after acquisition, so N stuck devices stack N×30 s serially.

---

## 6. Target design

**State the invariant, then delete the windows that approximate it.**

Replace `_boot_window` / `pairing_window` / `timeout_rounds` / `suspended` with
**two explicit connection profiles**, selected by *who initiated the attempt*:

| | `AUTOMATIC` | `USER_INITIATED` |
|---|---|---|
| Candidates | bonded paths only | unrestricted |
| Pair budget | generous (no human needed on a bonded path) | human-sized |
| May conclude "needs pairing" | **never on timeouts** — only on explicit rejection | yes |
| Outer connect budget | must exceed the inner pair budget | must exceed `pair × candidates` |

Consequences: `BOOT_PAIR_TIMEOUT` and `_boot_window` disappear;
`PAIRING_TIMEOUT_ROUNDS`, `MadokaPairingState.timeout_rounds` and the
re-injection of the private `conn._pairing_timeout_rounds` disappear. **The
correct fix is a code deletion, not an addition.**

Plus:

- **Quarantine bounded, not indefinite** — capped exponential backoff with one
  probe per step, instead of a full stop whose only exit is a sometimes-absent
  button.

  *Tension surfaced by the re-verification, must be resolved in the design:* a
  genuinely dead BRC1H bond usually fails by **silent timeout**, not by explicit
  rejection (the thermostat ignores the encryption request). So "only a rejection
  convicts" cannot mean "timeouts are ignored" — that would let SMP-initiating
  `pair()` attempts continue at poll cadence forever, i.e. the v3.6.0 storm.
  The correct policy is three-tiered:
  - **explicit rejection** → hard quarantine + reauth prompt (human required,
    proven);
  - **persistent timeout streak on bonded paths** → NO conviction, but
    **aggressive capped backoff** (e.g. up to 15-30 min between attempts, and
    prefer SMP-free liveness probes — `establish_connection` without `pair()` —
    between full attempts) + a WARNING-level repair suggesting a re-pair;
  - **mixed/transient** → normal retry cadence.
  This keeps both guarantees at once: no false-positive lockout (Parents) and no
  prompt-salvo on a dead bond (the original v3.6.0 incident).
- **Degraded load unconditional** — never `setup_retry` for a known BLE device;
  load the entry, mark entities unavailable, keep Reconnect actionable.
- **`async_step_reauth`**, started via `entry.async_start_reauth()` *before*
  raising — the only recovery affordance that works with zero entities and
  survives restarts. Must carry the fixed budgets, or it fails exactly like the
  button does today.
- **Evidence-based bond bookkeeping** — promote on successful `pair()` (not on a
  full poll), demote after N consecutive auth failures *on that path*; requires a
  per-path verdict from upstream.
- **Slot-aware candidate ordering** — `(bonded+preferred, free slots, RSSI)`; the
  scanner objects are already reachable. BlueSight is an optional *read-only*
  signal source for stale-allocation detection, never a dependency.

---

## 7. Ordered plan

### P0 — stop the bleeding (integration only, small, high value)
1. **Close the pairing window on failure/expiry** and restore `pair_timeout`
   unconditionally (single owner, context manager). Kills the live hazard in §3.
2. **Restore the budget invariant: every inner pair budget strictly below the
   outer connect budget.** This is not only "make pairing possible" — it is
   "restore evidence collection": with `pair_timeout ≥ CONNECT_TIMEOUT` the
   library's timeout classifier is unreachable and no verdict can ever form
   (§8). Concretely: automatic/boot pair budget ≈ 20-24 s under
   `CONNECT_TIMEOUT=30`; for user-initiated pairing, raise the outer budget
   (e.g. 90 s) so `PAIRING_WINDOW_TIMEOUT=60` stops being dead config; config
   flow uses the human budget.
3. **Re-tier the quarantine policy** (see §6 tension): hard quarantine ONLY on
   explicit rejection; timeout streaks → capped aggressive backoff + WARNING
   repair, never a conviction and never ignored. Deletes `_boot_window` /
   `BOOT_PAIR_TIMEOUT` as a side effect (replaced by the AUTOMATIC profile's
   wide-but-below-outer budget).
4. **Format empty exceptions** (`coordinator.py:257`) so the operator stops
   seeing `Could not reconnect to …: `.
5. **Guard `diagnostics.py`** for both never-loaded AND unloaded entries (HA
   deletes `runtime_data` on unload).

### P1 — make recovery always reachable
5. **Unconditional degraded load** (remove the `suspended` gate at
   `__init__.py:133`).
6. **`async_step_reauth`** + `entry.async_start_reauth()` before the raise.
7. **Fix `_fail_count`** so `device_unreachable` can actually fire (move it to
   the persisted per-MAC state).
8. **Card: `.catch` the service call** and only show "reconnecting…" on success.
9. **Persist the pairing verdict** per MAC (survive restarts) and **purge it** on
   entry removal.

### P2 — correctness of the bond model & scheduling
10. Bond bookkeeping: record on `pair()` success; demote/evict dead paths;
    stop wiping `bonded_sources` on reconfigure.
11. Don't hold the global lock across library sleeps; per-attempt acquisition.
12. Slot-aware candidate ordering; distinct `no_bonded_path` repair.
13. Make the `candidates_callback` contract strict (`allow_unbonded` flag; a
    raising callback must be a hard error, never a fallback to auto-pairing).

### Upstream (pymadoka-ng) — the real fix for the root cause
- `PairingRequiredError.reason: "rejected" | "timeout_streak"` (+ per-path
  `evidence`, `last_path_error`). **This is the single most valuable change.**
  (Re-verified: the two raise sites share one constructor, so today's messages
  are byte-identical; the only side channel is the counter value — 0 after a
  rejection raise vs ≥3 after a streak raise — fragile and undocumented.)
- Per-path streak counting + a public reset; **retain rejection evidence across
  rounds** (today a mixed round discards proven rejections) and **reset the
  streak on fallback-path success too** (`connection.py:495-498` doesn't).
- Per-candidate connect budget (`wait_for` around `establish_connection`).
- `connect_once()` — one sweep, no internal `while`/`sleep`; retry cadence
  belongs to the caller. Removes the integration's need to poke privates
  (`_closing`, `_paired`, `_pairing_timeout_rounds`).
- Distinguish "callback returned nothing" from "nothing visible".
- Inspect the SMP reason code in `is_pairing_error` instead of matching the
  bare "pairing failed" substring: today ANY `Pairing failed due to error: N`
  convicts instantly with zero grace. Whether error 81 (plausibly SMP
  0x51 = passkey-entry-fail, i.e. "prompt not confirmed in time") is a true
  rejection **cannot be determined from the code** — needs a log-corpus check
  before instant-conviction on it is trusted.

---

## 8. Honest note on v3.7.1 (corrected after adversarial re-verification)

`BOOT_PAIR_TIMEOUT = 30.0` was released on 2026-07-25 as a fix for the
boot-time false quarantine. Three statements about it, in order of revision:

1. *First assessment* (review that approved it): "30 is right; it is
   intentionally a ceiling." **Incomplete** — did not trace the side effects.
2. *4-axis review*: "structurally inert." **REFUTED by numeric walk**: with
   `pair_timeout=30` under the 30 s outer wrap, `pair()` gets ~27 s of real
   runtime before cancellation (vs 8 s before), so the targeted scenario — a
   valid bond encrypting slowly at boot — IS saved. The fix works for what it
   was aimed at.
3. *Final, verified state*: the fix works **and introduces a regression**.
   Because `pair_timeout ≥ CONNECT_TIMEOUT`, the library's timeout classifier
   is unreachable during the boot window: zero rounds are ever counted, so a
   genuinely dead bond (whose BRC1H signature is silent timeout, not rejection)
   can **never** reach `PairingRequiredError` → never gets `suspended` → the
   v3.6.0 anti-storm quarantine is disabled in exactly the post-restart
   scenario, indefinitely (the boot window only closes on a success a dead bond
   never produces). SMP-initiating `pair()` attempts continue at retry cadence
   forever.

**Direct field consequence (tonight):** Salon and Bedroom B sit in `setup_retry`
with a fresh coordinator — hence a fresh boot window — on every retry. Their
failures produce no evidence, `suspended` is never set, so the degraded-load
escape hatch (`__init__.py:133`) never opens and the Reconnect button never
appears. v3.7.1 is part of why they are currently unrecoverable from the UI.

The correct replacement is P0.2 + P0.3: wide-but-below-outer automatic budget
(evidence collection restored) + three-tier verdict policy (rejection → hard
quarantine; timeout streak → capped backoff + warning, no conviction).

## 9. Adversarial re-verification (2026-07-26)

All claims from the 4-axis review were independently re-verified by two
adversarial passes (integration C1-C17, library L1-L12) instructed to refute
them. Outcome: **28 confirmed (some with nuances folded into the text above),
1 refuted** (the "v3.7.1 inert" claim, §8). Material corrections integrated:

- §1b: budgets table rewritten (per-attempt vs across-polls UX; 60 s is dead
  config, not "never pair").
- §3: hazard confirmed; "indefinitely" bounded by restart/reload; per-poll
  reach throttled.
- §4: reconfigure nuance (rename degrades to `[preferred]`, not unrestricted).
- §5: lock hazard confined to advertising-but-unconnectable devices; lock
  acquisition itself unbounded (waits stack N×30 s).
- §6: three-tier verdict policy replaces the too-simple "never on timeouts".
- New findings: mixed rounds discard rejection evidence; fallback-path success
  doesn't reset the streak; `is_pairing_error` convicts on any
  "Pairing failed due to error: N" with zero grace (error-81 semantics
  undetermined — needs log corpus); diagnostics also crashes after unload.

Verified against: integration v3.7.1 working tree, pymadoka-ng repo checkout
v0.3.9 == manifest pin, HA core 2026.7.2.

## 10. Verified parity bug (separate track): dual setpoint hardcoded on the ESPHome path

Reported externally, verified 2026-07-26 against the working tree. **Confirmed,
and deeper than reported** — the ESPHome path is two-point end to end, not just
in the traits:

- `esphome/components/madoka/madoka.h:130-131` —
  `traits.add_feature_flags(... | climate::CLIMATE_REQUIRES_TWO_POINT_TARGET_TEMPERATURE)`
  is unconditional: the ESP32 climate entity ALWAYS advertises dual setpoints.
- `esphome/components/madoka/madoka.cpp:105-111` — `control()` only handles
  `get_target_temperature_low/high`; there is **no single-setpoint branch at
  all**. State publishing (`:415`, `:420`) only fills `target_temperature_high/
  low`; `target_temperature` is explicitly NaN (`:181`).
- The native integration is conditional and correct:
  `custom_components/daikin_madoka/climate.py:104-125` — `_range_active` =
  `hvac_mode == AUTO and set_point.range_enabled`, switching
  `supported_features` between `TARGET_TEMPERATURE_RANGE` and
  `TARGET_TEMPERATURE`.

**Consequence:** disabling range mode on the BRC1H fixes the native-HA entity
display but changes NOTHING on the ESP32 entity — the trait is compiled in.
This is a code-level parity gap, not a user setting.

**Constraint that shapes the fix:** ESPHome climate traits are advertised at
entity-listing time and are effectively static per API connection — the ESPHome
path cannot replicate the native path's *dynamic* (mode-dependent) switching.
Config-level parity is the realistic target.

**Fix outline (independent of the connection work, can ship any time):**
1. Add a YAML option to the component schema
   (`esphome/components/madoka/climate.py`), e.g. `dual_setpoint:
   cv.boolean`, default `false` (single setpoint is the common BRC1H
   configuration).
2. `madoka.h traits()`: add `CLIMATE_REQUIRES_TWO_POINT_TARGET_TEMPERATURE`
   only when enabled; otherwise single target temperature.
3. `madoka.cpp control()`: add the missing `get_target_temperature()` branch
   (single mode → write the setpoint register matching the current HVAC mode,
   as pymadoka's setpoint feature does), keep the low/high branch for dual.
4. State publish: fill `target_temperature` from the mode-relevant register
   when single; keep high/low when dual.
5. Document the option + the static-traits limitation in the component README.
