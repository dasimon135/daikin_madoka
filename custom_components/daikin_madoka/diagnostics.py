"""Diagnostics support for Daikin Madoka."""

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant

from .const import CONF_MAC, CONTROLLERS, COORDINATORS, DOMAIN

TO_REDACT = {CONF_MAC, CONF_DEVICES, "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Return diagnostics for a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]

    devices = {}
    for index, (mac, controller) in enumerate(data[CONTROLLERS].items()):
        coordinator = data[COORDINATORS][mac]
        devices[f"device_{index}"] = {
            "connection_status": controller.connection.connection_status.name,
            "info": controller.info,
            "status": {
                feature: {
                    key: str(value) for key, value in feature_status.items()
                }
                for feature, feature_status in (coordinator.data or {}).items()
            },
            "last_update_success": coordinator.last_update_success,
        }

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "devices": devices,
    }
