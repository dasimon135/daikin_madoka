"""Platform for the Daikin BRC1H (Madoka) thermostat."""
import logging

from pymadoka import Controller

import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_DEVICES, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_BONDED_SOURCES,
    CONF_FRIENDLY_NAME,
    CONF_MAC,
    CONF_PREFERRED_SOURCE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import (
    MadokaConfigEntry,
    MadokaCoordinator,
    async_forget_pairing_state,
    async_pairing_state,
    async_restore_pairing_state,
)
from .frontend import async_register_card
from .util import build_candidates, entry_macs, normalize_mac

COMPONENT_TYPES = ["climate", "sensor", "binary_sensor", "button", "number"]

_LOGGER = logging.getLogger(__name__)

# YAML configuration was removed long ago; setup is config-entry only.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _async_purge_orphan_devices(hass: HomeAssistant) -> None:
    """Remove registry devices left behind by deleted config entries.

    A delete/recreate cycle can leave devices pointing at a config entry id
    that no longer exists ("Can't link device to unknown config entry ..."
    at startup). Purge only devices that are ours AND whose every linked
    entry id is dangling — a device still linked to any live entry (ours or
    another integration's) is left alone.

    Note: now that the config flow supports reconfigure (MAC/name changes
    without delete + re-add), fresh orphans should become rare; this sweep
    can be reassessed for removal once field reports confirm that.
    """
    dev_reg = dr.async_get(hass)
    for device in list(dev_reg.devices.values()):
        # Identifier tuples are not fixed at two elements — rfxtrx registers
        # four — so match on the domain slot instead of destructuring, which
        # raised ValueError and aborted setup for anyone running such an
        # integration alongside this one.
        if not any(identifier[:1] == (DOMAIN,) for identifier in device.identifiers):
            continue
        if any(
            hass.config_entries.async_get_entry(entry_id) is not None
            for entry_id in device.config_entries
        ):
            continue
        dev_reg.async_remove_device(device.id)


async def async_setup_entry(hass: HomeAssistant, entry: MadokaConfigEntry) -> bool:
    """Set up Madoka thermostat(s) from a config entry.

    New-style entries carry a single MAC (``CONF_MAC``); legacy entries created
    before the per-device config flow carry a list of MACs (``CONF_DEVICES``).
    Both shapes are supported so existing installs keep working without a forced
    re-add, and legacy MACs (typed with dashes or lowercase) are normalized to
    the canonical form HA's BLE registry uses.

    Connections go through Home Assistant's Bluetooth stack (local adapter or
    ESPHome Bluetooth proxies) and are owned by the coordinator: its first
    refresh connects, and every later poll doubles as a reconnect attempt. The
    legacy ``adapter``/``force_update`` entry options are ignored.
    """
    # Idempotent and cheap (one pass over the registry), so running it on
    # every entry setup is fine.
    _async_purge_orphan_devices(hass)

    await async_register_card(hass)

    # Before any coordinator exists: what the integration concluded about this
    # device's bond before the last restart drives the very first connect
    # attempt (candidate restriction, poll cadence, quarantine).
    async_restore_pairing_state(hass, entry)

    if CONF_MAC in entry.data:
        devices = [(entry.data[CONF_MAC], entry.data.get(CONF_FRIENDLY_NAME) or None)]
        single_device = True
    else:
        devices = [(mac, None) for mac in entry.data.get(CONF_DEVICES, [])]
        single_device = False

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    coordinators: dict[str, MadokaCoordinator] = {}
    for raw_mac, friendly_name in devices:
        mac = normalize_mac(raw_mac) or raw_mac

        # Reads entry.data live so the preferred_source the coordinator
        # persists after a successful poll primes the very next reconnect,
        # without an entry reload. Legacy multi-MAC entries share one entry,
        # so a single preferred_source cannot be right for all of them: they
        # get plain RSSI ordering instead.
        def _candidates(mac=mac):
            # TOTAL BY CONTRACT: this callback must never raise. pymadoka
            # catches an exception here and silently falls back to
            # _connect_via_ha_single(), which lets habluetooth's scorer choose
            # any path, retries it three times AND calls pair() unconditionally
            # — the unattended auto-pairing this whole policy exists to forbid,
            # reachable through a single stray exception. The library's fallback
            # cannot be changed from here, so the callback is made incapable of
            # triggering it.
            try:
                preferred = (
                    entry.data.get(CONF_PREFERRED_SOURCE) if single_device else None
                )
                # Automatic reconnects only reach proxies known to hold a bond:
                # touching an unbonded one starts a real numeric-comparison
                # pairing that no unattended retry can complete, and repeating it
                # jams the thermostat. A user-opened pairing window lifts the
                # restriction, and so does having no bond on record yet (fresh
                # install), where an unrestricted first connect is the only way in.
                allowed = None
                if single_device and not async_pairing_state(hass, mac).pairing_window:
                    allowed = entry.data.get(CONF_BONDED_SOURCES) or (
                        [preferred] if preferred else None
                    )
                return build_candidates(hass, mac, preferred, allowed_sources=allowed)
            except Exception:  # see the contract above
                # Fail CLOSED, with an empty list. The library reports that as
                # DeviceUnreachableError, which surfaces as an ordinary failed
                # poll and feeds the device_unreachable repair: visible, bounded,
                # and it touches no radio. The alternative — returning a
                # best-effort list built from partial state — risks handing back
                # exactly the unbonded proxies the restriction was computed to
                # remove, which is the failure mode that jams a BRC1H. A wrong
                # "unreachable" costs one poll interval; a wrong pairing salvo
                # costs a trip to the thermostat.
                _LOGGER.exception(
                    "Could not build the candidate list for %s; reporting no "
                    "path rather than letting the library pick one",
                    mac,
                )
                return []

        # reconnect=False: the coordinator is the single reconnect owner; a
        # library-side background reconnect task would race it.
        controller = Controller(
            mac,
            hass=hass,
            name=friendly_name,
            reconnect=False,
            candidates_callback=_candidates,
        )
        coordinator = MadokaCoordinator(
            hass, controller, scan_interval, friendly_name=friendly_name
        )

        try:
            await coordinator.async_config_entry_first_refresh()
        except ConfigEntryNotReady as connection_error:
            # NEVER setup_retry a device that is already configured. The config
            # flow performs a full authenticated connect before creating the
            # entry, so a failure here is a RUNTIME failure — which Home
            # Assistant's own guidance says must surface as unavailable
            # entities, not as a refused setup.
            #
            # Refusing setup instead produced the catch-22 this integration
            # spent two field incidents in: no entities at all, hence no
            # Reconnect button — the very button the pairing notification tells
            # the user to press — and a fresh coordinator on every retry, each
            # performing exactly one poll, so no failure streak, no verdict and
            # no repair could ever accumulate. Loading degraded keeps the
            # coordinator alive: its update_interval drives the retries, the
            # P0.3 timeout backoff can slow them down, and every recovery
            # affordance stays on screen.
            _LOGGER.warning(
                "Setting up %s without a connection; its entities stay "
                "unavailable until it answers: %s",
                mac,
                connection_error,
            )

        try:
            await controller.read_info()
        except Exception:
            _LOGGER.debug("Could not read device info for %s", mac, exc_info=True)

        coordinators[mac] = coordinator

    if not coordinators:
        # Unreachable devices no longer land here (they load degraded); only a
        # malformed entry that names no MAC at all can.
        raise ConfigEntryError("This entry does not name any thermostat")

    entry.runtime_data = coordinators

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, COMPONENT_TYPES)

    return True


async def _safe_stop(controller: Controller) -> None:
    """Stop a controller, ignoring shutdown errors."""
    try:
        await controller.stop()
    except Exception:
        _LOGGER.debug("Error stopping controller", exc_info=True)


async def _async_update_listener(
    hass: HomeAssistant, entry: MadokaConfigEntry
) -> None:
    """Apply a new poll interval without tearing down the BLE connection.

    Delegated rather than assigned: this listener fires on EVERY
    async_update_entry, including the ones the coordinator itself performs to
    persist a preferred source or a pairing verdict, and assigning
    update_interval here used to silently disarm an active timeout backoff.
    """
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    for coordinator in entry.runtime_data.values():
        coordinator.async_apply_scan_interval(scan_interval)


async def async_unload_entry(
    hass: HomeAssistant, config_entry: MadokaConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, COMPONENT_TYPES
    )

    if unload_ok:
        # HA discards runtime_data once the entry is unloaded; only the BLE
        # side needs an explicit teardown here.
        for coordinator in config_entry.runtime_data.values():
            coordinator.async_shutdown_extras()
            await _safe_stop(coordinator.controller)
        # Drop the in-memory verdicts. The entry keeps its persisted copy, so
        # a reload or a restart picks the diagnosis straight back up; but a
        # DELETED entry takes that copy with it, so deleting and re-adding a
        # thermostat now really does start from a clean slate — the escape
        # hatch of last resort out of a quarantine the user disagrees with.
        for mac in entry_macs(config_entry):
            async_forget_pairing_state(hass, config_entry, mac, persisted=False)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: MadokaConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting a device that the entry no longer serves.

    HA calls this when the user hits "Delete device" in the UI. A device
    whose MAC is still backed by a live coordinator is in active use and
    must not be removable; anything else (stale MAC after an entry rewrite,
    leftover from a legacy multi-MAC entry) may go.
    """
    # runtime_data is unset when the entry never finished setting up.
    coordinators = getattr(config_entry, "runtime_data", None) or {}
    macs = {mac for domain, mac in device_entry.identifiers if domain == DOMAIN}
    if macs & set(coordinators):
        return False
    # The device is going: so must every verdict recorded against it, in
    # memory and in the entry. A stale "suspended" left behind here would
    # quarantine the replacement thermostat the moment it is added.
    for mac in macs:
        async_forget_pairing_state(hass, config_entry, mac)
    return True
