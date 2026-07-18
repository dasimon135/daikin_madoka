# daikin_madoka v3.2.0 — multi-proxy robustness implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> subagent-driven-development) to implement this plan task-by-task.

**Goal:** Implement the integration side of the multi-proxy robustness design:
sticky proxy, typed-error repairs, sane discovery, registry hygiene.

**Architecture:** pymadoka-ng 0.3.6 (already on PyPI) provides
`candidates_callback`, `connected_source`, `PairingRequiredError`,
`DeviceUnreachableError`. The integration supplies candidates ordered
sticky-first, persists the winning proxy in `entry.data["preferred_source"]`,
maps typed errors to repair issues, gates discovery on RSSI + a real
connection test, and purges registry orphans.

**Tech Stack:** HA custom integration (Python 3.13+ in CI), pytest +
pytest-homeassistant-custom-component (CI only — the harness imports `fcntl`
and cannot run on the Windows dev machine; author tests per task, validate on
CI via the PR).

**Design doc:** `docs/plans/2026-07-18-multi-proxy-robustness-design.md`
**Library plan (done):** pymadoka `docs/plans/2026-07-18-typed-errors-and-candidates.md`

**Key library contract notes (from the 0.3.6 reviews):**
- `start()` raises `PairingRequiredError` (all paths refused auth, carries
  `tried_sources`) / `DeviceUnreachableError` (empty candidates). Status is
  ABORTED + `last_error` stamped before the raise.
- After a typed-error abort the library does NOT auto-retry; the coordinator's
  existing "call `start()` when not CONNECTED" per-poll logic is the re-arm
  (calling `start()` from ABORTED works — status resets to CONNECTING).
- `reconnect=False` is already used, so no background starts; `start()`'s
  silent-return-when-already-running guard is not a concern here.
- `connection.connected_source` = source MAC of the winning proxy (None for
  local adapter/unknown). `bluetooth.async_scanner_by_source(hass, source)`
  resolves a human name for repair text.

---

### Task 1: Candidates helper + sticky-proxy wiring

**Files:**
- Modify: `custom_components/daikin_madoka/const.py` (add `CONF_PREFERRED_SOURCE = "preferred_source"`)
- Modify: `custom_components/daikin_madoka/util.py` (add `build_candidates`)
- Modify: `custom_components/daikin_madoka/__init__.py` (pass `candidates_callback`)
- Modify: `custom_components/daikin_madoka/coordinator.py` (persist winner)
- Modify: `custom_components/daikin_madoka/manifest.json` (`pymadoka-ng==0.3.6`)
- Test: `tests/test_util.py` (new), `tests/test_coordinator.py` (new)

**`build_candidates(hass, address, preferred_source)` in util.py:**

```python
def build_candidates(hass, address: str, preferred_source: str | None):
    """Ordered BLEDevice paths to the device: preferred proxy first, then RSSI.

    Feeds pymadoka's candidates_callback so the connection tries the proxy
    that last authenticated successfully before letting signal strength pick.
    """
    from homeassistant.components import bluetooth

    scanner_devices = bluetooth.async_scanner_devices_by_address(
        hass, address, connectable=True
    )
    def sort_key(sd):
        source = sd.ble_device.details.get("source") if isinstance(
            getattr(sd.ble_device, "details", None), dict) else None
        rssi = sd.advertisement.rssi if sd.advertisement else -127
        return (0 if preferred_source and source == preferred_source else 1, -rssi)
    return [sd.ble_device for sd in sorted(scanner_devices, key=sort_key)]
```

**`__init__.py`:** in the per-device loop, build the controller with a
callback reading `entry.data` live (so a persisted preferred_source is picked
up without reload):

```python
def _candidates(mac=mac):
    return build_candidates(hass, mac, entry.data.get(CONF_PREFERRED_SOURCE))

controller = Controller(
    mac, hass=hass, name=friendly_name, reconnect=False,
    candidates_callback=_candidates,
)
```
(Only meaningful for single-device entries; legacy multi-MAC entries share one
entry — store per-MAC dict `preferred_sources` keyed by MAC instead? NO —
YAGNI: legacy entries get candidates ordered by RSSI only (pass None), a
comment explains. New-style entries are one MAC each.)

**coordinator.py:** after a successful `_async_poll` (in
`_async_update_data`, success branch), persist the winner:

```python
source = self.controller.connection.connected_source
if source and self.config_entry and (
    self.config_entry.data.get(CONF_PREFERRED_SOURCE) != source
):
    self.hass.config_entries.async_update_entry(
        self.config_entry,
        data={**self.config_entry.data, CONF_PREFERRED_SOURCE: source},
    )
```
Guard: only for single-device entries (`CONF_MAC in data`). Note
`DataUpdateCoordinator` exposes `self.config_entry` automatically.

**Tests** (CI): `test_util.py` — `build_candidates` ordering with fake
scanner devices (SimpleNamespace with `ble_device.details["source"]` and
`advertisement.rssi`): preferred wins over stronger RSSI; RSSI order when no
preferred; missing details tolerated. `test_coordinator.py` — successful poll
persists `preferred_source` into entry.data (mock controller with
`connected_source="AA:BB..."`); no update call when unchanged.

**Commit:** `feat: sticky-proxy candidates (preferred_source) wired into pymadoka 0.3.6`

---

### Task 2: Typed errors → repairs in the coordinator

**Files:**
- Modify: `custom_components/daikin_madoka/coordinator.py`
- Modify: `custom_components/daikin_madoka/strings.json` + `translations/en.json` + `translations/fr.json` (mirror the other locale files that exist)
- Test: `tests/test_coordinator.py`

**In `_async_poll`,** split the blanket reconnect handler:

```python
try:
    await asyncio.wait_for(self.controller.start(), timeout=CONNECT_TIMEOUT)
except PairingRequiredError as err:
    self._raise_pairing_issue(err)
    raise UpdateFailed(str(err)) from err
except Exception as err:  # noqa: BLE001
    raise UpdateFailed(f"Could not reconnect to {self.address}: {err}") from err
```
(`DeviceUnreachableError` intentionally stays in the generic branch: it feeds
the existing threshold-based `device_unreachable` repair — no new issue type.)

**`_raise_pairing_issue(err)`** — immediate (no threshold: an auth refusal
never heals alone), `ir.IssueSeverity.ERROR`, issue_id
`f"pairing_required_{self.address}"`, translation_key `pairing_required`,
placeholders: `device`, `proxies` (comma-joined human names). Resolve names:

```python
def _proxy_names(self, sources) -> str:
    names = []
    for source in sources:
        if source is None:
            names.append("local adapter")
            continue
        scanner = bluetooth.async_scanner_by_source(self.hass, source)
        names.append(getattr(scanner, "name", None) or source)
    return ", ".join(dict.fromkeys(names)) or "unknown"
```

Clear it wherever `_clear_unreachable_issue` is cleared (success + unload) —
rename the pair to `_clear_issues()` clearing both ids. Keep the
`_issue_active` idempotence pattern (one flag per issue or just always-delete).

**strings.json** additions under `issues`:

```json
"pairing_required": {
  "title": "{device} refuses the Bluetooth connection",
  "description": "Every Bluetooth path to {device} was refused because the proxy is not paired with it (tried via: {proxies}). Confirm the pairing prompt on the thermostat screen — required once per proxy — or set the unpaired proxy to passive mode. See the pairing documentation for details."
}
```
Mirror in `translations/en.json`; French in `translations/fr.json`
(check which locale files exist and keep them all consistent — copy the
English text into locales you can't translate confidently, EXCEPT fr which
gets a proper French translation).

**Stale-value grace (design pillar B bonus):** in `_async_update_data`, on
`UpdateFailed` when `self._fail_count < STALE_GRACE` (new const, 2) AND
`self.data` is truthy AND the failure is NOT a `PairingRequiredError` chain:
log at DEBUG and `return self.data` instead of raising (prevents graph holes
on micro-drops; entities stay available for ≤2 cycles). The fail counter
still increments so the unreachable threshold logic is unchanged in spirit —
recount: threshold now effectively `UNREACHABLE_THRESHOLD` failures of which
the first `STALE_GRACE` are masked. Keep `UNREACHABLE_THRESHOLD = 5`.

**Tests:** PairingRequiredError from start() → issue created with proxy
names + UpdateFailed; issue cleared on next success; transient failure with
existing data → returns stale data (no UpdateFailed) for 2 cycles, raises on
the 3rd; pairing failure is never masked by the grace.

**Commit:** `feat: pairing_required repair (names the refusing proxies) + stale-value grace`

---

### Task 3: Config flow — RSSI floor + connection test before entry creation

**Files:**
- Modify: `custom_components/daikin_madoka/config_flow.py`
- Modify: `custom_components/daikin_madoka/const.py` (`RSSI_DISCOVERY_FLOOR = -90`, `VALIDATE_TIMEOUT = 30`)
- Modify: `custom_components/daikin_madoka/strings.json` + translations (errors `cannot_connect`, `pairing_failed`; abort `weak_signal`; step descriptions mention staying near the thermostat)
- Test: `tests/test_config_flow.py` (extend)

**RSSI floor** — first thing in `async_step_bluetooth`:

```python
if discovery_info.rssi is not None and discovery_info.rssi < RSSI_DISCOVERY_FLOOR:
    return self.async_abort(reason="weak_signal")
```
(Manual `async_step_user` does NOT filter — documented escape hatch.)

**Connection test** — new helper on the flow:

```python
async def _async_validate_device(self, mac: str) -> tuple[str | None, str | None]:
    """Try a full authenticated connect. Returns (error_key, connected_source)."""
    from pymadoka import Controller, DeviceUnreachableError, PairingRequiredError
    from .util import build_candidates

    controller = Controller(
        mac, hass=self.hass, reconnect=False,
        candidates_callback=lambda: build_candidates(self.hass, mac, None),
    )
    try:
        await asyncio.wait_for(controller.start(), timeout=VALIDATE_TIMEOUT)
        source = controller.connection.connected_source
        return None, source
    except PairingRequiredError:
        return "pairing_failed", None
    except (DeviceUnreachableError, TimeoutError, Exception):  # noqa: BLE001
        return "cannot_connect", None
    finally:
        try:
            await controller.stop()
        except Exception:  # noqa: BLE001
            pass
```
Wire into BOTH `async_step_bluetooth_confirm` (on submit) and
`async_step_user` (after MAC validation): on error → re-show the form with
`errors={"base": error_key}`; on success → `_create_entry(...)` now also
stores `CONF_PREFERRED_SOURCE: source` (when not None) so the sticky proxy
starts primed. Note: `_create_entry` gains a `preferred_source` parameter.

CAUTION — flow tests currently create entries without any BLE mocking; every
existing test that reaches `_create_entry` must now mock
`FlowHandler._async_validate_device` (patch to return `(None, "AA:BB:...")`).
Update `tests/test_config_flow.py` accordingly and add: weak discovery
aborted; `pairing_failed` shown and NO entry created; `cannot_connect` shown
and NO entry created; success stores preferred_source in entry data.

**Commit:** `feat: config flow validates the connection before creating an entry + RSSI discovery floor`

---

### Task 4: Registry hygiene

**Files:**
- Modify: `custom_components/daikin_madoka/__init__.py`
- Test: `tests/test_init.py` (new)

1. **`async_remove_config_entry_device`** (module-level function per HA
   convention): allow removal when the device's identifier MAC is not among
   the entry's current coordinators:

```python
async def async_remove_config_entry_device(hass, config_entry, device_entry) -> bool:
    """Allow deleting a device that the entry no longer serves."""
    coordinators = hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {}).get(COORDINATORS, {})
    macs = {mac for domain, mac in device_entry.identifiers if domain == DOMAIN}
    return not (macs & set(coordinators))
```

2. **Orphan purge** at the top of `async_setup_entry` (before building
   coordinators): remove device-registry devices carrying a `(DOMAIN, ...)`
   identifier whose `config_entries` reference no existing entry — the
   leftover class observed in the wild (`Can't link device to unknown config
   entry 01KXKGVR...` startup errors):

```python
def _async_purge_orphan_devices(hass: HomeAssistant) -> None:
    dev_reg = dr.async_get(hass)
    valid_entry_ids = {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}
    for device in list(dev_reg.devices.values()):
        if not any(domain == DOMAIN for domain, _ in device.identifiers):
            continue
        if not (set(device.config_entries) & valid_entry_ids):
            dev_reg.async_remove_device(device.id)
```
Call it once per setup (idempotent, cheap). Guard against removing devices
that ALSO belong to other integrations: only purge when ALL of the device's
config_entries are dangling (the `&` check above already ensures a device
still linked to any valid daikin_madoka entry survives; a device shared with
another domain keeps its other entry ids in `device.config_entries`, which
are not in `valid_entry_ids` — so add: skip when `device.config_entries`
contains ids of OTHER domains' existing entries. Concretely: purge only if
`not any(hass.config_entries.async_get_entry(eid) for eid in device.config_entries)`).

**Tests:** orphan device (identifier `(DOMAIN, mac)`, dangling entry id) is
removed at setup; healthy device untouched; device linked to a foreign live
entry untouched; `async_remove_config_entry_device` True for stale device,
False for an active one.

**Commit:** `feat: purge orphaned registry devices + allow per-device removal from the UI`

---

### Task 5: ESPHome reference doc + README + CHANGELOG

**Files:**
- Create: `docs/esphome-proxy.md`
- Modify: `README.md` (pairing section links to it)
- Modify: `CHANGELOG.md` (v3.2.0 - Unreleased entry)

`docs/esphome-proxy.md` (English): recommended proxy config for BRC1H —
`esp32_ble: io_capability: display_yes_no`, the SMP `sdkconfig_options`
(CONFIG_BT_BLE_SMP_ENABLE / CONFIG_BLE_SM_SC / CONFIG_BLE_SM_LEGACY), one
`ble_client` responder per thermostat (auto_connect: false,
`on_numeric_comparison_request` → HA persistent notification with the
6-digit `passkey` + `ble_client.numeric_comparison_reply accept: true`) —
full YAML example for two thermostats; the golden rule up front (*every
ACTIVE proxy in range must be paired with each thermostat — pair it once via
the on-screen prompt, or keep it passive*); troubleshooting table
(Insufficient authentication → unpaired proxy wins RSSI; prompt timeout →
unanswered screen prompt). Source material: the user's real configs (e.g.
`esphome-configs/atom-salon.yaml`) — genericize names/MACs.

CHANGELOG (v3.2.0 - Unreleased): sticky proxy, pairing repair, stale-value
grace, discovery floor + connection test, registry hygiene, requires
pymadoka-ng 0.3.6.

**Commit:** `docs: ESPHome proxy reference (pairing responders + code notification)`

---

### Task 6: PR + CI green

Push branch, `gh pr create` (targets dasimon135/daikin_madoka main). CI runs
ruff + hassfest + HACS + pytest (ubuntu). Iterate on CI failures — the HA
harness does not run on the Windows dev machine, so this is where the test
suite actually executes. Check `requirements-test.txt` (or the CI workflow's
install step) pins `pymadoka-ng` — bump/add `pymadoka-ng==0.3.6` so the new
API imports resolve in CI.

### Task 7: Release v3.2.0 — GATED ON HARDWARE VALIDATION

After PR merge: user updates their HA install (HACS re-download of main or
release), validates on the 4-thermostat/4-proxy home: Salon stays reachable
with buanderie active-unbonded (sticky proxy holds), pairing repair appears
with proxy names, discovery of the −95 dBm foreign BRC1H no longer shows.
Only then: release PR (manifest version 3.2.0 + changelog dated) → tag →
GitHub release, same mechanics as v3.1.1.
