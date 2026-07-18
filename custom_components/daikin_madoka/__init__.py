"""Platform for the Daikin BRC1H (Madoka) thermostat."""
from datetime import timedelta
import logging

from pymadoka import Controller

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICES, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_FRIENDLY_NAME,
    CONF_MAC,
    CONF_PREFERRED_SOURCE,
    COORDINATORS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import MadokaCoordinator
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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
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
            return build_candidates(hass, mac, preferred)

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

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {COORDINATORS: coordinators}

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, COMPONENT_TYPES)

    return True


async def _safe_stop(controller: Controller) -> None:
    """Stop a controller, ignoring shutdown errors."""
    try:
        await controller.stop()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Error stopping controller", exc_info=True)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply a new poll interval without tearing down the BLE connection."""
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    for coordinator in hass.data[DOMAIN][entry.entry_id][COORDINATORS].values():
        coordinator.update_interval = timedelta(seconds=scan_interval)


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, COMPONENT_TYPES
    )

    if unload_ok:
        data = hass.data[DOMAIN].pop(config_entry.entry_id, None)
        if data:
            for coordinator in data[COORDINATORS].values():
                coordinator.async_shutdown_extras()
                await _safe_stop(coordinator.controller)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting a device that the entry no longer serves.

    HA calls this when the user hits "Delete device" in the UI. A device
    whose MAC is still backed by a live coordinator is in active use and
    must not be removable; anything else (stale MAC after an entry rewrite,
    leftover from a legacy multi-MAC entry) may go.
    """
    coordinators = (
        hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {}).get(COORDINATORS, {})
    )
    macs = {mac for domain, mac in device_entry.identifiers if domain == DOMAIN}
    return not (macs & set(coordinators))
