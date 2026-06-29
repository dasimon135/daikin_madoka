"""Platform for the Daikin AC."""
import asyncio
from datetime import timedelta
import logging

from pymadoka import Controller, force_device_disconnect
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DEVICE,
    CONF_DEVICES,
    CONF_FORCE_UPDATE,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.core import HomeAssistant

from . import config_flow  # noqa: F401
from .const import CONTROLLERS, DOMAIN, CONF_MAC, CONF_FRIENDLY_NAME

PARALLEL_UPDATES = 0
MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=60)

COMPONENT_TYPES = ["climate", "sensor", "binary_sensor", "button", "number"]

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    vol.All(
        cv.deprecated(DOMAIN),
        {
            DOMAIN: vol.Schema(
                {
                    vol.Required(CONF_MAC): cv.string,
                    vol.Optional(CONF_FRIENDLY_NAME, default=""): cv.string,
                    vol.Optional(CONF_FORCE_UPDATE, default=True): bool,
                    vol.Optional(CONF_DEVICE, default="hci0"): cv.string,
                }
            )
        },
    ),
    extra=vol.ALLOW_EXTRA,
)


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
    """
    adapter = entry.data.get(CONF_DEVICE, "hci0")
    force_update = entry.data.get(CONF_FORCE_UPDATE, True)

    if CONF_MAC in entry.data:
        devices = [(entry.data[CONF_MAC], entry.data.get(CONF_FRIENDLY_NAME) or None)]
    else:
        devices = [(mac, None) for mac in entry.data.get(CONF_DEVICES, [])]

    controllers = {}
    for mac, friendly_name in devices:
        if force_update:
            try:
                await force_device_disconnect(mac)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Forced disconnect failed for %s, skipping...", mac)

        controller = Controller(mac, adapter=adapter, hass=hass, name=friendly_name)

        try:
            _LOGGER.info("Connecting to Madoka device: %s", mac)
            await asyncio.wait_for(controller.start(), timeout=15)
        except Exception as connection_error:  # noqa: BLE001
            _LOGGER.error(
                "Could not connect to device %s: %s",
                mac,
                str(connection_error),
            )

        controllers[mac] = controller

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {CONTROLLERS: controllers}

    await hass.config_entries.async_forward_entry_setups(entry, COMPONENT_TYPES)

    return True


async def async_unload_entry(hass, config_entry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, COMPONENT_TYPES
    )

    if unload_ok:
        data = hass.data[DOMAIN].pop(config_entry.entry_id, None)
        if data:
            for controller in data[CONTROLLERS].values():
                try:
                    await controller.stop()
                except Exception:  # noqa: BLE001
                    _LOGGER.debug(
                        "Error stopping controller during unload", exc_info=True
                    )

    return unload_ok
