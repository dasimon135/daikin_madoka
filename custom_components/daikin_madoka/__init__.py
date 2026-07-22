"""Platform for the Daikin BRC1H (Madoka) thermostat."""
from datetime import timedelta
import logging

from pymadoka import Controller

from homeassistant.const import CONF_DEVICES, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
import homeassistant.helpers.config_validation as cv

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
    async_pairing_state,
)
from .frontend import async_register_card
from .util import build_candidates, normalize_mac

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
        if not any(domain == DOMAIN for domain, _ in device.identifiers):
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
            if single_device and async_pairing_state(hass, mac).suspended:
                # A concluded pairing refusal never heals by retrying, so a
                # not-ready loop would poll into the void forever — and a
                # never-ready entry has no entities, hiding the reconnect
                # button that opens the pairing window: the only remedy.
                # Load degraded instead: entities unavailable, the
                # pairing_required repair on screen, the button reachable.
                _LOGGER.warning(
                    "Setting up %s without a connection; it needs pairing: %s",
                    mac,
                    connection_error,
                )
            else:
                await _safe_stop(controller)
                if single_device:
                    raise
                # Legacy multi-device entries keep the reachable thermostats
                # working instead of failing the whole entry.
                _LOGGER.warning(
                    "Skipping unreachable device %s: %s", mac, connection_error
                )
                continue

        try:
            await controller.read_info()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not read device info for %s", mac, exc_info=True)

        coordinators[mac] = coordinator

    if not coordinators:
        raise ConfigEntryNotReady("No Madoka device is reachable")

    entry.runtime_data = coordinators

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, COMPONENT_TYPES)

    return True


async def _safe_stop(controller: Controller) -> None:
    """Stop a controller, ignoring shutdown errors."""
    try:
        await controller.stop()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Error stopping controller", exc_info=True)


async def _async_update_listener(
    hass: HomeAssistant, entry: MadokaConfigEntry
) -> None:
    """Apply a new poll interval without tearing down the BLE connection."""
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    for coordinator in entry.runtime_data.values():
        coordinator.update_interval = timedelta(seconds=scan_interval)


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
    return not (macs & set(coordinators))
