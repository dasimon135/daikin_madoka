"""Diagnostics support for Daikin Madoka."""

from homeassistant.components import bluetooth
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant

from .const import CONF_MAC, CONF_PREFERRED_SOURCE
from .coordinator import MadokaConfigEntry

TO_REDACT = {CONF_MAC, CONF_DEVICES, "title", "unique_id"}


def _resolve_source(hass: HomeAssistant, source: str | None) -> str | None:
    """Resolve a proxy source MAC to its scanner name when known."""
    if not source:
        return None
    scanner = bluetooth.async_scanner_by_source(hass, source)
    return getattr(scanner, "name", None) or source


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
            "connected_source": _resolve_source(
                hass, controller.connection.connected_source
            ),
            "info": controller.info,
            "status": {
                feature: {key: str(value) for key, value in feature_status.items()}
                for feature, feature_status in (coordinator.data or {}).items()
            },
            "last_update_success": coordinator.last_update_success,
            "fail_count": coordinator.fail_count,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval is not None
                else None
            ),
            "issues": {
                "device_unreachable": coordinator.unreachable_issue_active,
                "pairing_required": coordinator.pairing_issue_active,
            },
        }

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        # The proxy MAC itself is already part of entry data; the resolved
        # scanner name is what makes multi-proxy reports readable.
        "preferred_source_resolved": _resolve_source(
            hass, entry.data.get(CONF_PREFERRED_SOURCE)
        ),
        "devices": devices,
    }
