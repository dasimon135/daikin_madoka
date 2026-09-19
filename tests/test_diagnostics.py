"""Diagnostics must survive the entries you most want to diagnose.

entry.runtime_data only exists between a successful setup and the unload that
follows it: HA deletes it on unload, and it is never set for an entry that
never finished setting up. Reading it unguarded raised AttributeError, and the
download button failed with an unhelpful backend error precisely for the
broken entry the user was trying to report.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.daikin_madoka.const import CONF_MAC, DOMAIN
from custom_components.daikin_madoka.coordinator import (
    MadokaCoordinator,
    async_pairing_state,
)
from custom_components.daikin_madoka.diagnostics import (
    async_get_config_entry_diagnostics,
)

MAC = "D0:CF:13:0F:11:F6"
SOURCE = "AA:BB:CC:11:22:33"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    return entry


def _controller() -> MagicMock:
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.CONNECTED
    controller.connection.connected_source = SOURCE
    controller.connection.pairing_timeout_rounds = 0
    controller.update = AsyncMock()
    controller.refresh_status.return_value = {"set_point": {"cooling_set_point": 25}}
    return controller


async def test_never_loaded_entry_still_returns_a_payload(
    hass: HomeAssistant,
) -> None:
    """A setup_retry entry is exactly the one worth downloading."""
    entry = _entry(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["devices"] == {}
    assert result["entry"][CONF_MAC] == "**REDACTED**"
    assert result["loaded"] is False


async def test_unloaded_entry_still_returns_a_payload(hass: HomeAssistant) -> None:
    """HA deletes runtime_data on unload; diagnostics must not crash after."""
    entry = _entry(hass)
    entry.runtime_data = {MAC: MagicMock()}
    del entry.runtime_data

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["devices"] == {}
    assert result["loaded"] is False


async def test_pairing_verdict_is_reported_without_a_coordinator(
    hass: HomeAssistant,
) -> None:
    """The verdict is the single most useful field for a dead entry."""
    entry = _entry(hass)
    state = async_pairing_state(hass, MAC)
    state.suspended = True

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["pairing_state"]["suspended"] is True
    assert result["pairing_state"]["pairing_window"] is False


async def test_loaded_entry_reports_its_devices(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    token = config_entries.current_entry.set(entry)
    try:
        coordinator = MadokaCoordinator(hass, _controller(), scan_interval=60)
    finally:
        config_entries.current_entry.reset(token)
    await coordinator.async_refresh()
    entry.runtime_data = {MAC: coordinator}

    with patch(
        "homeassistant.components.bluetooth.async_scanner_by_source",
        return_value=SimpleNamespace(name="Proxy"),
    ):
        result = await async_get_config_entry_diagnostics(hass, entry)

    device = result["devices"]["device_0"]
    assert result["loaded"] is True
    assert device["connection_status"] == "CONNECTED"
    assert device["connected_source"] == "Proxy"
    assert device["issues"]["pairing_slow"] is False
    # Every repair the coordinator can raise is reported, this one included.
    assert device["issues"]["unbonded_path"] is False


async def test_the_thermostat_mac_appears_nowhere_in_the_payload(
    hass: HomeAssistant,
) -> None:
    """Redaction is by key name, so a MAC used AS a key, or inside a message, leaks.

    The persisted pairing state is keyed by MAC, and PairingRequiredError's text
    starts with the address. Diagnostics get pasted into public issues.
    """
    from pymadoka import PairingRequiredError

    from custom_components.daikin_madoka.const import CONF_PAIRING_STATE

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: MAC, CONF_PAIRING_STATE: {MAC: {"suspended": True}}},
    )
    entry.add_to_hass(hass)
    state = async_pairing_state(hass, MAC)
    state.suspended = True
    state.last_error = PairingRequiredError(MAC, [SOURCE])

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert MAC not in repr(result)
    # Still useful: the verdict and the error survive, minus the address.
    assert result["pairing_state"]["suspended"] is True
    assert result["pairing_state"]["last_error"]
