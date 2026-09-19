"""A pairing window is a loan, not a gift: it always closes.

Live hazard found 2026-07-26 (review section 3). async_reconnect opened the
window and widened the pairing budget, and the ONLY close path was a
successful poll (or an unload). A failed Reconnect therefore left the window
open indefinitely, which at once:

- lifted the bonded-proxy restriction, so every automatic poll started real
  SMP on unbonded proxies - the pairing storm v3.6.0 exists to prevent;
- disarmed the dead-bond quarantine (suspended is only honoured while the
  window is closed);
- left a human-sized pairing budget on automatic reconnects, monopolising the
  BRC1H single central slot.

So the window is now time-bounded (PAIRING_WINDOW_TTL_S) AND failure-closed,
and both close paths restore the automatic pairing budget.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka import ConnectionException
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.daikin_madoka.const import (
    AUTOMATIC_PAIR_TIMEOUT,
    CONF_MAC,
    DOMAIN,
    PAIRING_WINDOW_TIMEOUT,
    PAIRING_WINDOW_TTL_S,
)
from custom_components.daikin_madoka.coordinator import (
    MadokaCoordinator,
    async_pairing_state,
)

MAC = "D0:CF:13:0F:11:F6"
SOURCE = "AA:BB:CC:11:22:33"

BLUETOOTH = "homeassistant.components.bluetooth"


def _controller() -> MagicMock:
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.connection.connected_source = SOURCE
    controller.connection.pair_timeout = 8.0
    controller.connection.pairing_timeout_rounds = 0
    controller.stop = AsyncMock()
    controller.update = AsyncMock()
    controller.refresh_status.return_value = {"set_point": {"cooling_set_point": 25}}
    return controller


def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, controller: MagicMock
) -> MadokaCoordinator:
    token = config_entries.current_entry.set(entry)
    try:
        return MadokaCoordinator(hass, controller, scan_interval=60)
    finally:
        config_entries.current_entry.reset(token)


def _patched_bluetooth():
    return (
        patch(f"{BLUETOOTH}.async_address_present", return_value=True),
        patch(
            f"{BLUETOOTH}.async_scanner_by_source",
            return_value=SimpleNamespace(name="Proxy"),
        ),
    )


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    return entry


def _no_sleep():
    return patch("custom_components.daikin_madoka.coordinator.asyncio.sleep", AsyncMock())


async def test_a_failed_reconnect_closes_the_window_and_restores_the_budget(
    hass: HomeAssistant,
) -> None:
    """One deliberate attempt. It failed; the door does not stay open."""
    entry = _entry(hass)
    controller = _controller()
    budget_during_attempt: list[float] = []

    async def _connect() -> None:
        budget_during_attempt.append(controller.connection.pair_timeout)
        raise ConnectionException("proxy busy")

    controller.start = AsyncMock(side_effect=_connect)
    coordinator = _coordinator(hass, entry, controller)
    state = async_pairing_state(hass, MAC)

    present, scanner = _patched_bluetooth()
    with present, scanner, _no_sleep():
        await coordinator.async_reconnect()

    # The user did get the human-sized budget for their attempt...
    assert budget_during_attempt == [PAIRING_WINDOW_TIMEOUT]
    # ...and the failure closed the window and restored the automatic one.
    assert state.pairing_window is False
    assert controller.connection.pair_timeout == AUTOMATIC_PAIR_TIMEOUT

    await coordinator.async_shutdown()


async def test_the_window_expires_on_its_own(
    hass: HomeAssistant, freezer
) -> None:
    """A window nobody consumed must not stay armed until the next restart."""
    entry = _entry(hass)
    controller = _controller()
    controller.start = AsyncMock()
    coordinator = _coordinator(hass, entry, controller)
    state = async_pairing_state(hass, MAC)

    # No refresh: model the user pressing Reconnect while the device is not
    # even advertising, so no poll ever consumes the window.
    with _no_sleep(), patch.object(coordinator, "async_request_refresh", AsyncMock()):
        await coordinator.async_reconnect()

    assert state.pairing_window is True
    assert controller.connection.pair_timeout == PAIRING_WINDOW_TIMEOUT

    freezer.tick(timedelta(seconds=PAIRING_WINDOW_TTL_S + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert state.pairing_window is False
    assert controller.connection.pair_timeout == AUTOMATIC_PAIR_TIMEOUT


async def test_shutdown_cancels_the_window_timer(hass: HomeAssistant) -> None:
    """An unloaded entry must not leave a callback pointing at a dead object."""
    entry = _entry(hass)
    controller = _controller()
    controller.start = AsyncMock()
    coordinator = _coordinator(hass, entry, controller)

    with _no_sleep(), patch.object(coordinator, "async_request_refresh", AsyncMock()):
        await coordinator.async_reconnect()

    coordinator.async_shutdown_extras()

    assert async_pairing_state(hass, MAC).pairing_window is False
    assert controller.connection.pair_timeout == AUTOMATIC_PAIR_TIMEOUT


async def test_a_successful_poll_still_closes_the_window(
    hass: HomeAssistant,
) -> None:
    """The pre-existing close path keeps working."""
    entry = _entry(hass)
    controller = _controller()
    state = async_pairing_state(hass, MAC)

    async def _connect() -> None:
        controller.connection.connection_status = ConnectionStatus.CONNECTED

    controller.start = AsyncMock(side_effect=_connect)
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner, _no_sleep():
        await coordinator.async_reconnect()

    assert coordinator.last_update_success
    assert state.pairing_window is False
    assert controller.connection.pair_timeout == AUTOMATIC_PAIR_TIMEOUT

    await coordinator.async_shutdown()


async def test_a_poll_already_in_flight_does_not_spend_the_window(
    hass: HomeAssistant,
) -> None:
    """The window belongs to the attempt made FOR it, not to one that predates it.

    Reconnect acts outside the coordinator's refresh lock. A struggling device
    spends half its time inside a connect, so the press often lands while an
    automatic attempt is still failing. That attempt used to close the window on
    its way out, and the attempt the user asked for then ran under the automatic
    profile: bonded proxies only, machine-sized pairing budget.
    """
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)
    state = async_pairing_state(hass, MAC)

    async def _connect_while_the_user_presses() -> None:
        # The press lands mid-attempt: the window opens under this attempt's feet.
        coordinator._async_open_pairing_window()
        raise ConnectionException("proxy busy")

    controller.start = AsyncMock(side_effect=_connect_while_the_user_presses)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    assert state.pairing_window is True
    assert controller.connection.pair_timeout == PAIRING_WINDOW_TIMEOUT

    # The window is still open on purpose, so its TTL timer is still armed.
    coordinator.async_shutdown_extras()
    await coordinator.async_shutdown()
