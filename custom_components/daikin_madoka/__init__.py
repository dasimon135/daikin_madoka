"""Platform for the Daikin BRC1H (Madoka) thermostat."""
import asyncio
from datetime import timedelta
import logging

from pymadoka import Controller
from pymadoka.connection import ConnectionStatus

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICES, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_FRIENDLY_NAME,
    CONF_MAC,
    CONNECT_TIMEOUT,
    COORDINATORS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import MadokaCoordinator

COMPONENT_TYPES = ["climate", "sensor", "binary_sensor", "button", "number"]

_LOGGER = logging.getLogger(__name__)

# YAML configuration was removed long ago; setup is config-entry only.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass, config):
    """Set up the component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Madoka thermostat(s) from a config entry.

    New-style entries carry a single MAC (``CONF_MAC``); legacy entries created
    before the per-device config flow carry a list of MACs (``CONF_DEVICES``).
    Both shapes are supported so existing installs keep working without a forced
    re-add.

    Connections go through Home Assistant's Bluetooth stack (local adapter or
    ESPHome Bluetooth proxies); the legacy ``adapter``/``force_update`` entry
    options are ignored.
    """
    if CONF_MAC in entry.data:
        devices = [(entry.data[CONF_MAC], entry.data.get(CONF_FRIENDLY_NAME) or None)]
        single_device = True
    else:
        devices = [(mac, None) for mac in entry.data.get(CONF_DEVICES, [])]
        single_device = False

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    coordinators: dict[str, MadokaCoordinator] = {}
    for mac, friendly_name in devices:
        controller = Controller(mac, hass=hass, name=friendly_name)

        try:
            _LOGGER.debug("Connecting to Madoka device: %s", mac)
            await asyncio.wait_for(controller.start(), timeout=CONNECT_TIMEOUT)
            if (
                controller.connection.connection_status
                is not ConnectionStatus.CONNECTED
            ):
                raise ConfigEntryNotReady(f"Device {mac} is not reachable")

            try:
                await controller.read_info()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not read device info for %s", mac, exc_info=True)

            coordinator = MadokaCoordinator(hass, controller, scan_interval)
            await coordinator.async_config_entry_first_refresh()
        except Exception as connection_error:  # noqa: BLE001
            await _safe_stop(controller)
            if single_device:
                raise ConfigEntryNotReady(
                    f"Could not connect to device {mac}: {connection_error}"
                ) from connection_error
            # Legacy multi-device entries keep the reachable thermostats
            # working instead of failing the whole entry.
            _LOGGER.warning(
                "Skipping unreachable device %s: %s", mac, connection_error
            )
            continue

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


async def async_unload_entry(hass, config_entry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, COMPONENT_TYPES
    )

    if unload_ok:
        data = hass.data[DOMAIN].pop(config_entry.entry_id, None)
        if data:
            for coordinator in data[COORDINATORS].values():
                await _safe_stop(coordinator.controller)

    return unload_ok
