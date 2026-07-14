"""Platform for the Daikin BRC1H (Madoka) thermostat."""
import asyncio
import logging

from pymadoka import Controller
from pymadoka.connection import ConnectionStatus
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DEVICE,
    CONF_DEVICES,
    CONF_FORCE_UPDATE,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_FRIENDLY_NAME,
    CONF_MAC,
    CONTROLLERS,
    COORDINATORS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import MadokaCoordinator

CONNECT_TIMEOUT = 15

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

    Connections go through Home Assistant's Bluetooth stack (local adapter or
    ESPHome Bluetooth proxies); the legacy ``adapter``/``force_update`` entry
    options are ignored.
    """
    if CONF_MAC in entry.data:
        devices = [(entry.data[CONF_MAC], entry.data.get(CONF_FRIENDLY_NAME) or None)]
    else:
        devices = [(mac, None) for mac in entry.data.get(CONF_DEVICES, [])]

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    controllers = {}
    coordinators = {}
    for mac, friendly_name in devices:
        controller = Controller(mac, hass=hass, name=friendly_name)

        try:
            _LOGGER.debug("Connecting to Madoka device: %s", mac)
            await asyncio.wait_for(controller.start(), timeout=CONNECT_TIMEOUT)
        except Exception as connection_error:  # noqa: BLE001
            await _safe_stop(controller)
            raise ConfigEntryNotReady(
                f"Could not connect to device {mac}: {connection_error}"
            ) from connection_error

        if controller.connection.connection_status is not ConnectionStatus.CONNECTED:
            await _safe_stop(controller)
            raise ConfigEntryNotReady(f"Device {mac} is not reachable")

        try:
            await controller.read_info()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not read device info for %s", mac, exc_info=True)

        coordinator = MadokaCoordinator(hass, controller, scan_interval)
        try:
            await coordinator.async_config_entry_first_refresh()
        except ConfigEntryNotReady:
            await _safe_stop(controller)
            for started in controllers.values():
                await _safe_stop(started)
            raise

        controllers[mac] = controller
        coordinators[mac] = coordinator

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        CONTROLLERS: controllers,
        COORDINATORS: coordinators,
    }

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
    """Reload the entry when options (e.g. scan interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, config_entry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, COMPONENT_TYPES
    )

    if unload_ok:
        data = hass.data[DOMAIN].pop(config_entry.entry_id, None)
        if data:
            for controller in data[CONTROLLERS].values():
                await _safe_stop(controller)

    return unload_ok
