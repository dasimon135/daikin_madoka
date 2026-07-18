"""Tests for the coordinator's sticky-proxy persistence."""

from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant

from custom_components.daikin_madoka.const import (
    CONF_FRIENDLY_NAME,
    CONF_MAC,
    CONF_PREFERRED_SOURCE,
    DOMAIN,
)
from custom_components.daikin_madoka.coordinator import MadokaCoordinator

MAC = "D0:CF:13:0F:11:F6"
OTHER_MAC = "D0:CF:13:0F:11:F7"
SOURCE = "AA:BB:CC:11:22:33"


def _mock_controller(source: str | None = SOURCE) -> MagicMock:
    """Controller stub whose poll succeeds without touching BLE."""
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.CONNECTED
    controller.connection.connected_source = source
    controller.update = AsyncMock()
    controller.refresh_status.return_value = {"set_point": {"cooling_set_point": 25}}
    return controller


def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, controller: MagicMock
) -> MadokaCoordinator:
    """Build a coordinator bound to the entry the way HA does during setup.

    Production code relies on DataUpdateCoordinator picking the entry up from
    the current_entry ContextVar that HA sets around async_setup_entry.
    """
    token = config_entries.current_entry.set(entry)
    try:
        return MadokaCoordinator(hass, controller, scan_interval=60)
    finally:
        config_entries.current_entry.reset(token)


async def test_successful_poll_persists_preferred_source(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_MAC: MAC, CONF_FRIENDLY_NAME: "Salon"}
    )
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass, entry, _mock_controller())

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert entry.data[CONF_PREFERRED_SOURCE] == SOURCE


async def test_unchanged_source_does_not_rewrite_entry(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass, entry, _mock_controller())

    with patch.object(
        hass.config_entries,
        "async_update_entry",
        wraps=hass.config_entries.async_update_entry,
    ) as mock_update:
        await coordinator.async_refresh()
        assert mock_update.call_count == 1

        await coordinator.async_refresh()
        # Same winning proxy: rewriting the entry would only churn storage
        # and re-fire the update listener for nothing.
        assert mock_update.call_count == 1

    assert entry.data[CONF_PREFERRED_SOURCE] == SOURCE


async def test_legacy_multi_device_entry_is_not_persisted(
    hass: HomeAssistant,
) -> None:
    # Legacy entries share one entry between several thermostats; a single
    # preferred_source cannot be right for all of them, so none is stored.
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICES: [MAC, OTHER_MAC]})
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass, entry, _mock_controller())

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert CONF_PREFERRED_SOURCE not in entry.data


async def test_unknown_source_is_not_persisted(hass: HomeAssistant) -> None:
    # connected_source is None for the local adapter / unknown backends;
    # storing it would clobber a valid sticky proxy with nothing.
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_MAC: MAC, CONF_PREFERRED_SOURCE: SOURCE}
    )
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass, entry, _mock_controller(source=None))

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert entry.data[CONF_PREFERRED_SOURCE] == SOURCE
