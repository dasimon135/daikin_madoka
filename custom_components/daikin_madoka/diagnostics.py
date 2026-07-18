"""Diagnostics support for Daikin Madoka."""

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant

from .const import CONF_MAC
from .coordinator import MadokaConfigEntry

TO_REDACT = {CONF_MAC, CONF_DEVICES, "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MadokaConfigEntry
) -> dict:
    """Return diagnostics for a config entry."""
    coordinators = entry.runtime_data

    devices = {}
    for index, coordinator in enumerate(coordinators.values()):
        controller = coordinator.controller
        devices[f"device_{index}"] = {
            "connection_status": controller.connection.connection_status.name,
            "info": controller.info,
            "status": {
                feature: {key: str(value) for key, value in feature_status.items()}
                for feature, feature_status in (coordinator.data or {}).items()
            },
            "last_update_success": coordinator.last_update_success,
        }

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "devices": devices,
    }
