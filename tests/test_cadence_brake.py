"""The poll cadence must slow down without waiting for a verdict.

Review finding C1, the second half. Slowing a failing device down was wired
exclusively to _async_enter_timeout_backoff, which only ever runs after pymadoka
raises PairingRequiredError - which needs a full candidate round to complete,
which needs the per-round budget arithmetic to be right. It was not: with two or
more candidate paths no round ever completed, so no verdict ever formed, so the
ONLY brake in the integration was unreachable and a dead bond polled at 60s
forever. Measured against the release it replaced: ~40 connect attempts/h and
~80 SMP initiations/h, versus ~6 and ~18.

So the brake no longer depends on the library concluding anything. Five failed
polls in a row is enough, whatever the reason. The verdict still matters - it is
what decides WHICH story the user is told - but it is no longer load-bearing for
throttling.

Second finding here (I1): an active brake used to be silently lost. Persisting a
pairing verdict calls async_update_entry, which fires the entry update listener,
which reassigned update_interval unconditionally. A braked device whose next
poll failed without a pairing verdict dropped straight back to fast cadence with
backoff still True and the repair still on screen.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka import ConnectionException, PairingRequiredError
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.daikin_madoka import _async_update_listener
from custom_components.daikin_madoka.const import (
    CONF_MAC,
    DOMAIN,
    TIMEOUT_BACKOFF_INTERVAL_S,
)
from custom_components.daikin_madoka.coordinator import (
    BACKOFF_PAIRING,
    BACKOFF_UNREACHABLE,
    UNREACHABLE_THRESHOLD,
    MadokaCoordinator,
    async_pairing_state,
)

MAC = "D0:CF:13:0F:11:F6"
SOURCE = "AA:BB:CC:11:22:33"
BLUETOOTH = "homeassistant.components.bluetooth"
BACKOFF = timedelta(seconds=TIMEOUT_BACKOFF_INTERVAL_S)
NORMAL = timedelta(seconds=60)


def _controller() -> MagicMock:
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.connection.connected_source = SOURCE
    controller.connection.pair_timeout = 8.0
    controller.connection.pairing_timeout_rounds = 0
    controller.update = AsyncMock()
    controller.refresh_status.return_value = {"set_point": {"cooling_set_point": 25}}
    controller.start = AsyncMock(side_effect=ConnectionException("no answer"))
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


def _patched_bluetooth():
    return (
        patch(f"{BLUETOOTH}.async_address_present", return_value=True),
        patch(
            f"{BLUETOOTH}.async_scanner_by_source",
            return_value=SimpleNamespace(name="Proxy"),
        ),
    )


async def _refresh(coordinator: MadokaCoordinator, times: int = 1) -> None:
    for _ in range(times):
        present, scanner = _patched_bluetooth()
        with present, scanner:
            await coordinator.async_refresh()


# --------------------------------------------------------------------------
# C1(a): the brake does not need a verdict
# --------------------------------------------------------------------------


async def test_the_brake_engages_without_any_pairing_verdict(
    hass: HomeAssistant,
) -> None:
    """Five failed polls are enough. No PairingRequiredError is ever raised."""
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)
    assert coordinator.update_interval == NORMAL

    await _refresh(coordinator, UNREACHABLE_THRESHOLD)

    state = async_pairing_state(hass, MAC)
    assert state.fail_count == UNREACHABLE_THRESHOLD
    # Nothing was ever concluded about pairing...
    assert state.timeout_rounds == 0
    assert state.suspended is False
    assert coordinator.pairing_slow_issue_active is False
    # ...and the cadence slowed anyway.
    assert coordinator.update_interval == BACKOFF
    assert coordinator.backoff_reason == BACKOFF_UNREACHABLE


async def test_the_brake_does_not_engage_before_the_threshold(
    hass: HomeAssistant,
) -> None:
    """A device that drops one poll is not a device to stop polling."""
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)

    await _refresh(coordinator, UNREACHABLE_THRESHOLD - 1)

    assert coordinator.update_interval == NORMAL
    assert coordinator.pairing_backoff is False


async def test_an_unreachable_brake_does_not_claim_pairing_is_slow(
    hass: HomeAssistant,
) -> None:
    """The WARNING repair still needs the library's actual timeout verdict.

    A generic unreachable device has its own device_unreachable repair; telling
    its owner that pairing keeps timing out would send them to the thermostat to
    re-pair something that only lost power.
    """
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)

    await _refresh(coordinator, UNREACHABLE_THRESHOLD)

    assert coordinator.pairing_slow_issue_active is False
    assert coordinator.pairing_slow_suspected is False
    assert coordinator.unreachable_issue_active is True


async def test_a_success_restores_the_configured_cadence(
    hass: HomeAssistant,
) -> None:
    """The brake is a brake, not a sentence."""
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)

    await _refresh(coordinator, UNREACHABLE_THRESHOLD)
    assert coordinator.update_interval == BACKOFF

    async def _start() -> None:
        controller.connection.connection_status = ConnectionStatus.CONNECTED

    controller.start = AsyncMock(side_effect=_start)
    await _refresh(coordinator)

    assert coordinator.last_update_success is True
    assert coordinator.update_interval == NORMAL
    assert coordinator.pairing_backoff is False
    assert coordinator.backoff_reason is None


async def test_a_pairing_verdict_still_wins_the_reason(
    hass: HomeAssistant,
) -> None:
    """A failure streak must not overwrite the more specific diagnosis."""
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)
    state = async_pairing_state(hass, MAC)
    state.backoff = True
    state.backoff_reason = BACKOFF_PAIRING

    await _refresh(coordinator, UNREACHABLE_THRESHOLD)

    assert coordinator.backoff_reason == BACKOFF_PAIRING
    assert coordinator.pairing_slow_suspected is True


# --------------------------------------------------------------------------
# I1: an active brake must survive everything that rewrites the interval
# --------------------------------------------------------------------------


async def test_an_entry_update_does_not_disarm_an_active_brake(
    hass: HomeAssistant,
) -> None:
    """async_update_entry fires the update listener on every persisted verdict."""
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)
    entry.runtime_data = {MAC: coordinator}

    await _refresh(coordinator, UNREACHABLE_THRESHOLD)
    assert coordinator.update_interval == BACKOFF

    await _async_update_listener(hass, entry)

    assert coordinator.update_interval == BACKOFF
    # The user's cadence is what we go back to, not what we poll at.
    assert coordinator._normal_interval == NORMAL


async def test_a_new_scan_interval_is_adopted_when_the_brake_lifts(
    hass: HomeAssistant,
) -> None:
    """Deferring the option must not mean losing it."""
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)
    entry.runtime_data = {MAC: coordinator}

    await _refresh(coordinator, UNREACHABLE_THRESHOLD)
    hass.config_entries.async_update_entry(entry, options={CONF_SCAN_INTERVAL: 120})
    await _async_update_listener(hass, entry)
    assert coordinator.update_interval == BACKOFF

    async def _start() -> None:
        controller.connection.connection_status = ConnectionStatus.CONNECTED

    controller.start = AsyncMock(side_effect=_start)
    await _refresh(coordinator)

    assert coordinator.update_interval == timedelta(seconds=120)


async def test_a_verdictless_failure_re_arms_a_dropped_brake(
    hass: HomeAssistant,
) -> None:
    """The exact I1 sequence: braked, interval reset from outside, poll fails.

    DeviceUnreachableError, "not advertising" and a skipped poll all reach the
    next cycle without any pairing verdict, so before this the brake was only
    ever re-applied by the one path that could no longer run.
    """
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)

    await _refresh(coordinator, UNREACHABLE_THRESHOLD)
    assert coordinator.update_interval == BACKOFF

    # Whatever reset it - the update listener of an older release, a reload, a
    # service call - the next poll must not run at that cadence.
    coordinator.update_interval = NORMAL
    await _refresh(coordinator)

    assert coordinator.pairing_backoff is True
    assert coordinator.update_interval == BACKOFF


# --------------------------------------------------------------------------
# A deliberate user action outranks the brake
# --------------------------------------------------------------------------


async def test_reconnect_lifts_the_brake_and_attempts_immediately(
    hass: HomeAssistant,
) -> None:
    """Field report 2026-07-26: a re-pairing that seemed to do nothing.

    A thermostat was in pairing_slow with a 900s brake. The user saw the
    repair, walked over and re-paired - and nothing happened for a quarter of
    an hour, because the brake was still running. A brake is an inference
    about a device nobody has touched; a human who just touched it is better
    information than any inference, so pressing Reconnect must lift it and try
    now.
    """
    entry = _entry(hass)
    controller = _controller()
    controller.stop = AsyncMock()
    coordinator = _coordinator(hass, entry, controller)

    await _refresh(coordinator, UNREACHABLE_THRESHOLD)
    assert coordinator.update_interval == BACKOFF
    attempts = controller.start.await_count

    present, scanner = _patched_bluetooth()
    with (
        present,
        scanner,
        patch("custom_components.daikin_madoka.coordinator.asyncio.sleep", AsyncMock()),
    ):
        await coordinator.async_reconnect()

    # The attempt happened during the button press, not 900s later...
    assert controller.start.await_count == attempts + 1
    # ...and it failed, yet the cadence is the configured one: the streak that
    # armed the brake was evidence gathered before the user intervened.
    assert coordinator.update_interval == NORMAL
    assert coordinator.pairing_backoff is False
    assert coordinator.backoff_reason is None

    # async_request_refresh leaves the debouncer's cooldown timer armed.
    await coordinator.async_shutdown()


async def test_reconnect_lifts_a_pairing_brake_and_clears_the_repair_on_success(
    hass: HomeAssistant,
) -> None:
    """The pairing_slow warning must not outlive the situation it describes."""
    entry = _entry(hass)
    controller = _controller()
    controller.stop = AsyncMock()
    controller.start = AsyncMock(
        side_effect=PairingRequiredError(
            MAC, [SOURCE], reason="timeout_streak", timeout_rounds=3
        )
    )
    coordinator = _coordinator(hass, entry, controller)
    await _refresh(coordinator)
    assert coordinator.update_interval == BACKOFF
    assert coordinator.backoff_reason == BACKOFF_PAIRING
    assert coordinator.pairing_slow_issue_active is True

    async def _start() -> None:
        controller.connection.connection_status = ConnectionStatus.CONNECTED

    controller.start = AsyncMock(side_effect=_start)
    present, scanner = _patched_bluetooth()
    with (
        present,
        scanner,
        patch("custom_components.daikin_madoka.coordinator.asyncio.sleep", AsyncMock()),
    ):
        await coordinator.async_reconnect()

    assert coordinator.last_update_success is True
    assert coordinator.update_interval == NORMAL
    assert coordinator.pairing_slow_suspected is False
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"pairing_slow_{MAC}") is None

    await coordinator.async_shutdown()


async def test_a_rebuilt_coordinator_keeps_the_brake(hass: HomeAssistant) -> None:
    """The verdict is per MAC precisely so it outlives the coordinator."""
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)
    await _refresh(coordinator, UNREACHABLE_THRESHOLD)

    rebuilt = _coordinator(hass, entry, _controller())

    assert rebuilt.update_interval == BACKOFF
    assert rebuilt.backoff_reason == BACKOFF_UNREACHABLE


# --------------------------------------------------------------------------
# An absent device is not a stalling one
#
# The brake exists because every connect attempt takes a proxy slot and
# re-initiates SMP against a thermostat that answers none of it. A poll that
# fails fast on "not advertising" does neither: it never touches a radio. Braking
# on those made a thermostat that lost power for six minutes wait up to fifteen
# more after it came back, for an attempt that cost nothing to make.
# --------------------------------------------------------------------------


async def _refresh_absent(coordinator: MadokaCoordinator, times: int = 1) -> None:
    for _ in range(times):
        with patch(f"{BLUETOOTH}.async_address_present", return_value=False):
            await coordinator.async_refresh()


async def test_an_absent_device_is_not_braked(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)

    await _refresh_absent(coordinator, UNREACHABLE_THRESHOLD + 2)

    controller.start.assert_not_awaited()
    assert coordinator.update_interval == NORMAL
    assert coordinator.pairing_backoff is False


async def test_an_absent_device_still_gets_its_unreachable_repair(
    hass: HomeAssistant,
) -> None:
    """Not braking must not mean not telling: the repair is about the user."""
    entry = _entry(hass)
    coordinator = _coordinator(hass, entry, _controller())

    await _refresh_absent(coordinator, UNREACHABLE_THRESHOLD)

    assert coordinator.unreachable_issue_active is True


async def test_absence_does_not_lift_a_brake_that_real_failures_earned(
    hass: HomeAssistant,
) -> None:
    """Going quiet is not a recovery; only a success or a human lifts the brake."""
    entry = _entry(hass)
    coordinator = _coordinator(hass, entry, _controller())
    await _refresh(coordinator, UNREACHABLE_THRESHOLD)
    assert coordinator.update_interval == BACKOFF

    await _refresh_absent(coordinator, 2)

    assert coordinator.update_interval == BACKOFF
    assert coordinator.backoff_reason == BACKOFF_UNREACHABLE


async def test_one_real_failure_after_an_absence_does_not_brake(
    hass: HomeAssistant,
) -> None:
    """The first connect after a power cut often fails; it is ONE radio failure.

    The absent polls before it filled the failure counter, and the brake used to
    read that counter: the thermostat came back, stumbled once, and was put on
    the 15-minute cadence on the spot.
    """
    entry = _entry(hass)
    coordinator = _coordinator(hass, entry, _controller())
    await _refresh_absent(coordinator, UNREACHABLE_THRESHOLD + 1)

    await _refresh(coordinator, 1)

    assert coordinator.update_interval == NORMAL
    assert coordinator.pairing_backoff is False


async def test_radio_failures_still_brake_across_an_absence(
    hass: HomeAssistant,
) -> None:
    """Absence in between does not reset what the radio failures established."""
    entry = _entry(hass)
    coordinator = _coordinator(hass, entry, _controller())
    await _refresh(coordinator, UNREACHABLE_THRESHOLD - 1)
    await _refresh_absent(coordinator, 1)

    await _refresh(coordinator, 1)

    assert coordinator.update_interval == BACKOFF
