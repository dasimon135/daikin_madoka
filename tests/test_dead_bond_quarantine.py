"""A dead bond behind a trusted proxy must not become an endless prompt loop.

Field incident 2026-07-21. A thermostat's only bonded proxy lost its bond.
Every poll then re-attempted the connect through that trusted path, and each
attempt re-initiated a real SMP exchange: a pairing prompt on the thermostat
screen with an 8s budget no human can meet. The library's own safeguard — a
3-round streak of all-paths-timed-out before concluding "pairing required" —
never fired, because the round counter lives on the Connection object and
CONNECT_TIMEOUT cuts a setup attempt after ~2 rounds: HA then rebuilds the
Controller for the next ConfigEntryNotReady retry and the counter restarts
from zero. Result: a fresh prompt salvo every 600 seconds, forever, and a
jammed thermostat.

The rules that follow:

1. The all-paths-timed-out streak survives the Controller rebuild, so the
   3-round threshold is actually reachable and PairingRequiredError fires.
2. A concluded pairing refusal SUSPENDS automatic reconnects indefinitely —
   never touching the device again beats prompting a screen nobody watches.
   The suspension outlives config-entry retries and is lifted only by a
   deliberate user pairing action (the reconnect button) or a successful
   session.
3. While suspended, the pairing_required repair is kept alive so the story
   on screen matches the inaction.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka import ConnectionException, PairingRequiredError
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.daikin_madoka.const import CONF_MAC, DOMAIN
from custom_components.daikin_madoka.coordinator import (
    MadokaCoordinator,
    async_pairing_state,
)

MAC = "D0:CF:13:0F:11:F6"
SOURCE = "AA:BB:CC:11:22:33"

BLUETOOTH = "homeassistant.components.bluetooth"


def _disconnected_controller(rounds: int = 0) -> MagicMock:
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.connection.connected_source = SOURCE
    controller.connection.pairing_timeout_rounds = rounds
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


# --------------------------------------------------------------------------
# 1. The timed-out-while-pairing streak survives a config entry retry
# --------------------------------------------------------------------------


async def test_timeout_round_streak_survives_a_config_entry_retry(
    hass: HomeAssistant,
) -> None:
    """The counter that reset on every rebuild is what kept the salvo alive."""
    entry = _entry(hass)
    present, scanner = _patched_bluetooth()

    first = _coordinator(hass, entry, _disconnected_controller())
    # CONNECT_TIMEOUT cuts the attempt after two all-paths-timed-out rounds;
    # the library reports the streak it accumulated before being cancelled.
    first.controller.connection.pairing_timeout_rounds = 2
    first.controller.start = AsyncMock(
        side_effect=ConnectionException("cancelled mid-round")
    )
    with present, scanner:
        await first.async_refresh()

    assert async_pairing_state(hass, MAC).timeout_rounds == 2

    # HA retries the entry: a brand-new Controller and Connection. The fresh
    # Connection must resume the streak, not restart it from zero.
    second = _coordinator(hass, entry, _disconnected_controller())
    assert second.controller.connection._pairing_timeout_rounds == 2


async def test_a_streak_is_never_regressed_by_a_roundless_failure(
    hass: HomeAssistant,
) -> None:
    """A failure that ran no pairing round must not erase the streak."""
    entry = _entry(hass)
    state = async_pairing_state(hass, MAC)
    state.timeout_rounds = 2
    present, scanner = _patched_bluetooth()

    coordinator = _coordinator(hass, entry, _disconnected_controller(rounds=0))
    # Seeding normally keeps the connection counter at the saved streak; a
    # lower value models a future library that lost the seed (renamed private
    # attribute) — the saved streak must win.
    coordinator.controller.connection.pairing_timeout_rounds = 0
    coordinator.controller.start = AsyncMock(
        side_effect=ConnectionException("device switched off")
    )
    with present, scanner:
        await coordinator.async_refresh()

    assert state.timeout_rounds == 2


# --------------------------------------------------------------------------
# 2. A concluded refusal suspends automatic reconnects indefinitely
# --------------------------------------------------------------------------


async def test_pairing_refusal_suspends_all_future_automatic_attempts(
    hass: HomeAssistant, freezer
) -> None:
    """No backoff cap: hours later the device still must not be prompted."""
    entry = _entry(hass)
    controller = _disconnected_controller()
    controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE]))
    coordinator = _coordinator(hass, entry, controller)
    present, scanner = _patched_bluetooth()

    with present, scanner:
        await coordinator.async_refresh()
        assert controller.start.await_count == 1

        freezer.tick(timedelta(hours=6))
        await coordinator.async_refresh()

    assert controller.start.await_count == 1
    assert async_pairing_state(hass, MAC).suspended is True


async def test_suspension_survives_a_config_entry_retry(
    hass: HomeAssistant,
) -> None:
    """The rebuilt coordinator must not touch the device either."""
    entry = _entry(hass)
    present, scanner = _patched_bluetooth()

    first = _coordinator(hass, entry, _disconnected_controller())
    first.controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE]))
    with present, scanner:
        await first.async_refresh()

    second = _coordinator(hass, entry, _disconnected_controller())
    second.controller.start = AsyncMock()
    present2, scanner2 = _patched_bluetooth()
    with present2, scanner2:
        await second.async_refresh()

    second.controller.start.assert_not_awaited()


async def test_reconnect_lifts_the_suspension(hass: HomeAssistant) -> None:
    """The reconnect button is the user saying they are at the thermostat."""
    entry = _entry(hass)
    controller = _disconnected_controller()
    controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE]))
    controller.stop = AsyncMock()
    coordinator = _coordinator(hass, entry, controller)
    present, scanner = _patched_bluetooth()

    with present, scanner:
        await coordinator.async_refresh()
        assert controller.start.await_count == 1

        with patch(
            "custom_components.daikin_madoka.coordinator.asyncio.sleep", AsyncMock()
        ):
            await coordinator.async_reconnect()

    assert controller.start.await_count == 2

    # async_request_refresh leaves the debouncer's cooldown timer armed.
    await coordinator.async_shutdown()


async def test_successful_poll_clears_the_suspension(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    controller = _disconnected_controller()
    controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE]))
    coordinator = _coordinator(hass, entry, controller)
    state = async_pairing_state(hass, MAC)
    present, scanner = _patched_bluetooth()

    with present, scanner:
        await coordinator.async_refresh()
        assert state.suspended is True

        controller.connection.connection_status = ConnectionStatus.CONNECTED
        await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert state.suspended is False
    assert state.timeout_rounds == 0


# --------------------------------------------------------------------------
# 3. The repair stays on screen while suspended
# --------------------------------------------------------------------------


async def test_suspended_poll_keeps_the_pairing_repair_alive(
    hass: HomeAssistant,
) -> None:
    """After a rebuild the new coordinator re-raises the repair it inherited."""
    entry = _entry(hass)
    present, scanner = _patched_bluetooth()

    first = _coordinator(hass, entry, _disconnected_controller())
    first.controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE]))
    with present, scanner:
        await first.async_refresh()
    assert first.pairing_issue_active is True

    second = _coordinator(hass, entry, _disconnected_controller())
    present2, scanner2 = _patched_bluetooth()
    with present2, scanner2:
        await second.async_refresh()

    # The flag both re-creates the registry issue and keeps the redundant
    # device_unreachable repair suppressed once the failure count climbs.
    assert second.pairing_issue_active is True


# --------------------------------------------------------------------------
# 4. The remedy stays reachable: a suspended device must not block setup
# --------------------------------------------------------------------------


def _setup_patches(controller: MagicMock):
    return (
        patch(
            "custom_components.daikin_madoka.Controller",
            MagicMock(return_value=controller),
        ),
        patch(
            "custom_components.daikin_madoka.async_register_card", AsyncMock()
        ),
        patch("custom_components.daikin_madoka.COMPONENT_TYPES", []),
        *_patched_bluetooth(),
    )


async def test_suspended_device_sets_up_in_degraded_mode(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """ConfigEntryNotReady forever would hide the only remedy.

    A never-ready entry never creates entities, so the reconnect button —
    the only way to open a pairing window — would not exist. A suspended
    device therefore loads in a degraded state: entities unavailable, the
    pairing_required repair on screen, and the button reachable.
    """
    entry = _entry(hass)
    state = async_pairing_state(hass, MAC)
    state.suspended = True
    state.last_error = PairingRequiredError(MAC, [SOURCE])

    controller = _disconnected_controller()
    controller.stop = AsyncMock()
    controller.read_info = AsyncMock()
    p1, p2, p3, present, scanner = _setup_patches(controller)
    with p1, p2, p3, present, scanner:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert MAC in entry.runtime_data


async def test_ordinary_failure_still_defers_setup(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """Only a concluded pairing refusal justifies loading a dead entry."""
    entry = _entry(hass)

    controller = _disconnected_controller()
    controller.stop = AsyncMock()
    controller.start = AsyncMock(side_effect=ConnectionException("device off"))
    p1, p2, p3, present, scanner = _setup_patches(controller)
    with p1, p2, p3, present, scanner:
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY
