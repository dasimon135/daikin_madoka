"""The allowed-sources callback must actually reach pymadoka.

The library gained the ability to refuse pairing on a path Home Assistant
chose for itself, but it can only enforce a policy it is handed. This is the
handover, plus the two cases where the answer must be "unrestricted": a user
deliberately pairing a new proxy, and anything going wrong while working it
out.

Kept apart from test_unbonded_path_verdict.py because these set an entry up
for real (and so need HA's bluetooth stack), exactly as
test_candidates_contract.py does for the sibling callback.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.daikin_madoka.const import (
    CONF_BONDED_SOURCES,
    CONF_MAC,
    DOMAIN,
)
from custom_components.daikin_madoka.coordinator import async_pairing_state

MAC = "D0:CF:13:0F:11:F6"
BONDED = "AA:BB:CC:11:22:33"
BLUETOOTH = "homeassistant.components.bluetooth"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Setting the entry up pulls in HA's bluetooth integration, whose scanner
    schedules a device-expiry timer that outlives the test (same waiver as
    test_candidates_contract)."""
    return True


def _controller() -> MagicMock:
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.connection.connected_source = None
    controller.connection.pairing_timeout_rounds = 0
    controller.info = {}
    controller.start = AsyncMock()
    controller.stop = AsyncMock()
    controller.read_info = AsyncMock()
    controller.update = AsyncMock()
    controller.refresh_status.return_value = {}
    return controller


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_MAC: MAC, CONF_BONDED_SOURCES: [BONDED]}
    )
    entry.add_to_hass(hass)
    return entry


async def _captured_kwargs(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    """Set the entry up and return the kwargs handed to pymadoka."""
    controller = MagicMock(return_value=_controller())
    with (
        patch("custom_components.daikin_madoka.Controller", controller),
        patch("custom_components.daikin_madoka.async_register_card", AsyncMock()),
        patch("custom_components.daikin_madoka.COMPONENT_TYPES", []),
        patch(f"{BLUETOOTH}.async_address_present", return_value=False),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return controller.call_args.kwargs


async def _captured_kwargs(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    controller = MagicMock(return_value=_controller())
    with (
        patch("custom_components.daikin_madoka.Controller", controller),
        patch("custom_components.daikin_madoka.async_register_card", AsyncMock()),
        patch("custom_components.daikin_madoka.COMPONENT_TYPES", []),
        patch(f"{BLUETOOTH}.async_address_present", return_value=False),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return controller.call_args.kwargs


async def test_the_allowed_sources_callback_reports_the_bonded_proxies(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    entry = _entry(hass)
    kwargs = await _captured_kwargs(hass, entry)

    assert kwargs["allowed_sources_callback"]() == [BONDED]

    await hass.config_entries.async_unload(entry.entry_id)


async def test_an_open_pairing_window_lifts_the_restriction(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """A user standing at the thermostat is allowed to pair a NEW proxy —
    that is the only way one ever enters bonded_sources."""
    entry = _entry(hass)
    kwargs = await _captured_kwargs(hass, entry)

    async_pairing_state(hass, MAC).pairing_window = True
    assert kwargs["allowed_sources_callback"]() is None

    await hass.config_entries.async_unload(entry.entry_id)


async def test_the_allowed_sources_callback_never_raises(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """Fail open, like the candidates callback: a broken policy must never be
    the reason a thermostat cannot connect."""
    entry = _entry(hass)
    kwargs = await _captured_kwargs(hass, entry)

    with patch(
        "custom_components.daikin_madoka.async_pairing_state",
        side_effect=RuntimeError("state store went away"),
    ):
        assert kwargs["allowed_sources_callback"]() is None

    await hass.config_entries.async_unload(entry.entry_id)
