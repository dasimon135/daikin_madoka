"""Diagnostics support for Daikin Madoka."""

from homeassistant.components import bluetooth
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant

from .const import CONF_MAC, CONF_PREFERRED_SOURCE
from .coordinator import (
    PAIRING_STATE_KEY,
    MadokaConfigEntry,
    MadokaCoordinator,
    MadokaPairingState,
)
from .util import entry_macs

TO_REDACT = {CONF_MAC, CONF_DEVICES, "title", "unique_id"}


def _resolve_source(hass: HomeAssistant, source: str | None) -> str | None:
    """Resolve a proxy source MAC to its scanner name when known."""
    if not source:
        return None
    scanner = bluetooth.async_scanner_by_source(hass, source)
    return getattr(scanner, "name", None) or source


def _pairing_states(hass: HomeAssistant, entry: MadokaConfigEntry) -> dict | None:
    """Pairing verdict per MAC, readable without a live coordinator.

    This is the field worth having on a broken entry: it says whether the
    integration concluded a pairing refusal, is merely backing off, or has a
    window open — none of which is visible anywhere else once setup fails.
    """
    store: dict[str, MadokaPairingState] = hass.data.get(PAIRING_STATE_KEY, {})
    states = {}
    for mac in entry_macs(entry):
        state = store.get(mac)
        if state is None:
            continue
        states[mac] = {
            "suspended": state.suspended,
            "backoff": state.backoff,
            # Which of the two independent triggers braked the cadence: a
            # pairing verdict, or a plain streak of failed polls.
            "backoff_reason": state.backoff_reason if state.backoff else None,
            "pairing_window": state.pairing_window,
            "timeout_rounds": state.timeout_rounds,
            "fail_count": state.fail_count,
            # Polls that never reached the device because another Madoka
            # coordinator held the shared connect lock. A non-zero value here
            # next to a healthy-looking device is the signature of lock
            # starvation, which is otherwise invisible above DEBUG.
            "skipped_polls": state.skipped_polls,
            # Per-proxy refusal streaks: the only place the bond-eviction
            # bookkeeping is visible, and the first thing to look at when a
            # thermostat reaches fewer proxies than the user expects.
            "auth_failures": {
                _resolve_source(hass, source) or source: count
                for source, count in state.auth_failures.items()
            },
            # Per-proxy pairing TIMEOUT streaks. Distinct from auth_failures
            # (proven refusals) and, in the field, the only one of the two that
            # is ever non-empty. This is the field that answers "which of this
            # device's bonds looks dead, and which proxy is just busy?" without
            # joining raw log lines against bonded_sources by hand.
            "timeout_sources": {
                _resolve_source(hass, source) or source: count
                for source, count in state.timeout_sources.items()
            },
            "last_error": str(state.last_error) if state.last_error else None,
        }
    if not states:
        return None
    # Single-device entries (the normal shape) read better flattened, and the
    # MAC is redacted elsewhere in this payload anyway.
    if len(states) == 1:
        return next(iter(states.values()))
    return states


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MadokaConfigEntry
) -> dict:
    """Return diagnostics for a config entry.

    runtime_data only exists between a successful setup and the matching
    unload: HA deletes it on unload and never sets it for an entry stuck in
    setup_retry. Those are precisely the entries a user downloads diagnostics
    for, so both cases return a payload instead of raising.
    """
    coordinators: dict[str, MadokaCoordinator] = getattr(entry, "runtime_data", None) or {}

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
                "pairing_slow": coordinator.pairing_slow_issue_active,
            },
            "pairing_suspended": coordinator.pairing_suspended,
            "pairing_backoff": coordinator.pairing_backoff,
            "backoff_reason": coordinator.backoff_reason,
            "skipped_polls": coordinator.skipped_polls,
        }

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "state": entry.state.value,
        # False means no coordinator exists: either the entry never finished
        # setting up, or it has been unloaded.
        "loaded": bool(coordinators),
        # The proxy MAC itself is already part of entry data; the resolved
        # scanner name is what makes multi-proxy reports readable.
        "preferred_source_resolved": _resolve_source(
            hass, entry.data.get(CONF_PREFERRED_SOURCE)
        ),
        "pairing_state": _pairing_states(hass, entry),
        "devices": devices,
    }
