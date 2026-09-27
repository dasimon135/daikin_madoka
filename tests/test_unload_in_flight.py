"""Unloading an entry must not let a poll already in flight outlive it.

HA cancels an entry's background tasks only AFTER async_unload_entry returns,
and a refresh started by a button press or the debouncer is not one of them
anyway. A poll parked on the shared connect lock could therefore resume after
the controller was stopped and:

- open a fresh BLE link nobody owns. The BRC1H accepts a single central, so
  the reloaded entry is then locked out of its own thermostat;
- write its stale verdict back into entry.data after the reauth flow cleared
  it;
- re-create an empty pairing state through the pairing callbacks after the
  unload popped it, so the next setup skips the restore and silently loses
  the persisted quarantine.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.daikin_madoka.const import (
    CONF_BONDED_SOURCES,
    CONF_MAC,
    CONF_PAIRING_STATE,
    DOMAIN,
)
from custom_components.daikin_madoka.coordinator import (
    PAIRING_STATE_KEY,
    MadokaCoordinator,
    _async_connect_lock,
    async_forget_pairing_state,
    async_pairing_state,
)

MAC = "D0:CF:13:0F:11:F6"
BONDED = "AA:BB:CC:11:22:33"
BLUETOOTH = "homeassistant.components.bluetooth"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Setting the entry up pulls in HA's bluetooth integration, whose scanner
    schedules a device-expiry timer that outlives the test (same waiver as
    test_allowed_sources_wiring)."""
    return True


def _controller() -> MagicMock:
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.connection.connected_source = BONDED
    controller.connection.pairing_timeout_rounds = 0
    controller.info = {}

    async def _start() -> None:
        controller.connection.connection_status = ConnectionStatus.CONNECTED

    controller.start = AsyncMock(side_effect=_start)
    controller.stop = AsyncMock()
    controller.read_info = AsyncMock()
    controller.update = AsyncMock()
    controller.refresh_status.return_value = {"set_point": {"cooling_set_point": 25}}
    return controller


def _entry(hass: HomeAssistant, **data) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_MAC: MAC, CONF_BONDED_SOURCES: [BONDED], **data}
    )
    entry.add_to_hass(hass)
    return entry


async def _set_up(
    hass: HomeAssistant, entry: MockConfigEntry, controller: MagicMock
) -> MagicMock:
    """Set the entry up for real and return the Controller class mock."""
    controller_class = MagicMock(return_value=controller)
    with (
        patch("custom_components.daikin_madoka.Controller", controller_class),
        patch("custom_components.daikin_madoka.async_register_card", AsyncMock()),
        patch("custom_components.daikin_madoka.COMPONENT_TYPES", []),
        # Load degraded without touching the (mock) radio.
        patch(f"{BLUETOOTH}.async_address_present", return_value=False),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return controller_class


async def test_unload_does_not_leave_a_link_behind(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    entry = _entry(hass)
    controller = _controller()
    await _set_up(hass, entry, controller)
    coordinator: MadokaCoordinator = entry.runtime_data[MAC]

    # Another thermostat is connecting: this poll parks on the shared lock.
    lock = _async_connect_lock(hass)
    await lock.acquire()
    with (
        patch(f"{BLUETOOTH}.async_address_present", return_value=True),
        patch(
            f"{BLUETOOTH}.async_scanner_by_source",
            return_value=SimpleNamespace(name="Proxy"),
        ),
    ):
        poll = hass.async_create_task(coordinator.async_refresh())
        for _ in range(20):
            await asyncio.sleep(0)

        assert await hass.config_entries.async_unload(entry.entry_id)
        assert controller.stop.await_count >= 1

        # The other device lets go. Nothing of this entry may connect now.
        lock.release()
        for _ in range(20):
            await asyncio.sleep(0)
        await asyncio.wait({poll}, timeout=1)

    assert poll.done()
    controller.start.assert_not_awaited()
    assert controller.connection.connection_status is not ConnectionStatus.CONNECTED


async def test_the_pairing_callbacks_do_not_resurrect_an_unloaded_state(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """A late callback must not stand in for the verdict the entry persisted."""
    entry = _entry(hass, **{CONF_PAIRING_STATE: {MAC: {"suspended": True}}})
    controller = _controller()
    controller_class = await _set_up(hass, entry, controller)
    kwargs = controller_class.call_args.kwargs
    assert async_pairing_state(hass, MAC).suspended is True

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert MAC not in hass.data[PAIRING_STATE_KEY]

    # pymadoka may still be walking its candidates when the unload lands.
    assert kwargs["allowed_sources_callback"]() == [BONDED]
    kwargs["candidates_callback"]()

    assert MAC not in hass.data[PAIRING_STATE_KEY]


async def test_a_superseded_coordinator_does_not_write_its_verdict_back(
    hass: HomeAssistant,
) -> None:
    """The reauth flow forgets the verdict; the old coordinator must not undo it."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    controller = _controller()
    token = config_entries.current_entry.set(entry)
    try:
        coordinator = MadokaCoordinator(hass, controller, scan_interval=60)
    finally:
        config_entries.current_entry.reset(token)
    state = async_pairing_state(hass, MAC)
    state.suspended = True
    coordinator._async_persist_pairing_state()
    assert entry.data[CONF_PAIRING_STATE][MAC]["suspended"] is True

    # Reauth succeeded: the verdict is false now, in memory and in the entry.
    async_forget_pairing_state(hass, entry, MAC)
    assert CONF_PAIRING_STATE not in entry.data

    # The old coordinator is still polling (suspended: a fast failure).
    with patch(f"{BLUETOOTH}.async_address_present", return_value=True):
        await coordinator.async_refresh()

    assert CONF_PAIRING_STATE not in entry.data
