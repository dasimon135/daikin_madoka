"""Pressing Reconnect must produce the attempt the user asked for.

Three ways the deliberate attempt used to be lost or spoiled:

1. async_reconnect ended with async_request_refresh, which goes through HA's
   Debouncer. While a poll holds the refresh lock the call is deferred by the
   cooldown and then DROPPED if the lock is still held ("any call is good").
   A connect attempt lasts 30-90s, so the press usually landed in exactly that
   situation and the user's attempt only happened at the next scheduled poll,
   possibly after the pairing window had expired.
2. The library's own all-paths-timed-out streak survives cleanup(), so rounds
   gathered before the press let the brake and the pairing_slow verdict come
   straight back during the user's own attempt.
3. The window's TTL could fire in the middle of the attempt it was opened for
   and hand the automatic pairing budget back mid-connect.
"""

import asyncio
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
from homeassistant.util import dt as dt_util

from custom_components.daikin_madoka.const import (
    AUTOMATIC_PAIR_TIMEOUT,
    CONF_MAC,
    DOMAIN,
    PAIRING_WINDOW_TIMEOUT,
)
from custom_components.daikin_madoka.coordinator import (
    MadokaCoordinator,
    async_pairing_state,
)

MAC = "D0:CF:13:0F:11:F6"
SOURCE = "AA:BB:CC:11:22:33"
BLUETOOTH = "homeassistant.components.bluetooth"
COORDINATOR = "custom_components.daikin_madoka.coordinator"
SHORT_TTL = 5.0

_real_sleep = asyncio.sleep


async def _instant_sleep(_delay: float, *args, **kwargs) -> None:
    """Skip the post-disconnect settle delay without starving the loop."""
    await _real_sleep(0)


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


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    return entry


def _patches():
    return (
        patch(f"{BLUETOOTH}.async_address_present", return_value=True),
        patch(
            f"{BLUETOOTH}.async_scanner_by_source",
            return_value=SimpleNamespace(name="Proxy"),
        ),
        patch(
            "custom_components.daikin_madoka.coordinator.asyncio.sleep",
            _instant_sleep,
        ),
        # Shorter than every connect budget, so firing the TTL does not also
        # fire the wait_for around controller.start().
        patch(f"{COORDINATOR}.PAIRING_WINDOW_TTL_S", SHORT_TTL),
    )


async def _until(predicate, rounds: int = 50) -> None:
    for _ in range(rounds):
        if predicate():
            return
        await _real_sleep(0)
    raise AssertionError("condition never became true")


def _advance(hass: HomeAssistant, seconds: float) -> None:
    """Run every loop timer due within `seconds` (debouncer, window TTL)."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))


async def test_reconnect_during_a_poll_still_makes_the_users_attempt(
    hass: HomeAssistant,
) -> None:
    """The press lands while an automatic attempt holds the refresh lock.

    That attempt outlives the debouncer cooldown AND the window TTL. The user's
    attempt must still run afterwards, and still under the window.
    """
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)
    state = async_pairing_state(hass, MAC)
    gate = asyncio.Event()
    windows: list[bool] = []

    async def _start() -> None:
        windows.append(state.pairing_window)
        if len(windows) == 1:
            await gate.wait()
        raise ConnectionException("proxy busy")

    controller.start = AsyncMock(side_effect=_start)

    present, scanner, sleep, ttl = _patches()
    with present, scanner, sleep, ttl:
        in_flight = hass.async_create_task(coordinator.async_refresh())
        await _until(lambda: len(windows) == 1)

        press = hass.async_create_task(coordinator.async_reconnect())
        await _until(lambda: state.pairing_window)
        # The debouncer cooldown ends while the lock is still held (a queued
        # request_refresh is dropped here), then the window TTL runs out.
        _advance(hass, 11)
        await _real_sleep(0)

        gate.set()
        await in_flight
        await asyncio.wait_for(press, 5)

    # The deliberate attempt happened, under the window it was granted...
    assert windows == [False, True]
    # ...and, having failed, it spent the window.
    assert state.pairing_window is False
    assert controller.connection.pair_timeout == AUTOMATIC_PAIR_TIMEOUT

    await coordinator.async_shutdown()


async def test_reconnect_forgets_the_librarys_timeout_streak(
    hass: HomeAssistant,
) -> None:
    """Rounds gathered before the press must not count against the user's try.

    cleanup() does not reset the library's streak, so a device two rounds into
    it would be convicted of pairing_slow by the very attempt the user made.
    """
    entry = _entry(hass)
    controller = _controller()
    controller.connection.pairing_timeout_rounds = 2

    def _reset() -> None:
        controller.connection.pairing_timeout_rounds = 0

    controller.connection.reset_pairing_timeout_rounds = MagicMock(
        side_effect=_reset
    )
    seen_rounds: list[int] = []

    async def _start() -> None:
        seen_rounds.append(controller.connection.pairing_timeout_rounds)
        raise ConnectionException("proxy busy")

    controller.start = AsyncMock(side_effect=_start)
    coordinator = _coordinator(hass, entry, controller)

    present, scanner, sleep, ttl = _patches()
    with present, scanner, sleep, ttl:
        await coordinator.async_reconnect()

    assert seen_rounds == [0]
    assert async_pairing_state(hass, MAC).timeout_rounds == 0

    await coordinator.async_shutdown()


async def test_clearing_the_brake_forgets_the_librarys_timeout_streak(
    hass: HomeAssistant,
) -> None:
    """The reauth flow lifts the brake through async_clear_backoff; same rule."""
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)
    async_pairing_state(hass, MAC).timeout_rounds = 2

    coordinator.async_clear_backoff()

    controller.connection.reset_pairing_timeout_rounds.assert_called_once_with()
    assert async_pairing_state(hass, MAC).timeout_rounds == 0


async def test_the_window_does_not_expire_under_the_attempt_it_was_opened_for(
    hass: HomeAssistant,
) -> None:
    """A TTL firing mid-connect used to reset pair_timeout under the handshake."""
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)
    state = async_pairing_state(hass, MAC)
    gate = asyncio.Event()
    started = asyncio.Event()

    async def _start() -> None:
        started.set()
        await gate.wait()
        raise ConnectionException("no answer")

    controller.start = AsyncMock(side_effect=_start)

    present, scanner, _sleep, ttl = _patches()
    with present, scanner, ttl:
        coordinator._async_open_pairing_window()
        attempt = hass.async_create_task(coordinator.async_refresh())
        await started.wait()

        _advance(hass, SHORT_TTL + 1)
        await _real_sleep(0)

        # Still open, still the human-sized budget, while the attempt runs.
        assert state.pairing_window is True
        assert controller.connection.pair_timeout == PAIRING_WINDOW_TIMEOUT

        gate.set()
        await attempt

    assert state.pairing_window is False
    assert controller.connection.pair_timeout == AUTOMATIC_PAIR_TIMEOUT

    await coordinator.async_shutdown()


async def test_an_expiry_deferred_by_a_connected_attempt_still_closes(
    hass: HomeAssistant,
) -> None:
    """Connect succeeded, the GATT poll failed: neither close path runs.

    The deferred expiry has to close the window itself at the end of the
    attempt, or it would stay open with no timer left to close it.
    """
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)
    state = async_pairing_state(hass, MAC)
    gate = asyncio.Event()
    started = asyncio.Event()

    async def _start() -> None:
        started.set()
        await gate.wait()
        controller.connection.connection_status = ConnectionStatus.CONNECTED

    controller.start = AsyncMock(side_effect=_start)
    controller.update = AsyncMock(side_effect=ConnectionException("gatt lost"))

    present, scanner, _sleep, ttl = _patches()
    with present, scanner, ttl:
        coordinator._async_open_pairing_window()
        attempt = hass.async_create_task(coordinator.async_refresh())
        await started.wait()
        _advance(hass, SHORT_TTL + 1)
        await _real_sleep(0)
        assert state.pairing_window is True

        gate.set()
        await attempt

    assert state.pairing_window is False
    assert controller.connection.pair_timeout == AUTOMATIC_PAIR_TIMEOUT

    await coordinator.async_shutdown()
