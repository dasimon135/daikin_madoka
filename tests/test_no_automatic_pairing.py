"""An automatic reconnect must never start a new pairing.

Field incident 2026-07-20. A newly added proxy had the best RSSI to two
thermostats, so it was tried first on every reconnect. It had no bond with
them, so each attempt started a real numeric-comparison pairing: a code
appeared on the thermostat screen and a notification reached the user long
after the 8s attempt had already timed out. Nobody could ever confirm, and the
repeated half-finished SMP exchanges jammed the thermostats' Bluetooth stacks —
after which even correctly bonded proxies could no longer re-encrypt, so the
devices went fully unreachable until their Bluetooth was toggled by hand.

The rule that follows: pairing is a deliberate, user-initiated act. Automatic
reconnects only use proxies already known to be bonded. When the user presses
reconnect they are standing at the thermostat, so that opens a pairing window —
every path is allowed and the pairing budget is widened enough for a human to
compare codes and accept.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka import PairingRequiredError
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.daikin_madoka.const import (
    CONF_BONDED_SOURCES,
    CONF_MAC,
    CONF_PREFERRED_SOURCE,
    DOMAIN,
    PAIRING_WINDOW_TIMEOUT,
)
from custom_components.daikin_madoka.coordinator import (
    MadokaCoordinator,
    async_pairing_state,
)
from custom_components.daikin_madoka.util import build_candidates

MAC = "D0:CF:13:0F:11:F6"
BONDED = "AA:BB:CC:11:22:33"
UNBONDED = "DD:EE:FF:44:55:66"

BLUETOOTH = "homeassistant.components.bluetooth"
SCAN_TARGET = f"{BLUETOOTH}.async_scanner_devices_by_address"


def _scanner_device(source: str, rssi: int) -> SimpleNamespace:
    return SimpleNamespace(
        ble_device=SimpleNamespace(details={"source": source}),
        advertisement=SimpleNamespace(rssi=rssi),
    )


# --------------------------------------------------------------------------
# Candidate filtering
# --------------------------------------------------------------------------


def test_unbonded_proxy_is_excluded_even_with_the_best_signal() -> None:
    """The exact field scenario: a new, closer, unbonded proxy must be skipped."""
    unbonded_but_closest = _scanner_device(UNBONDED, -65)
    bonded_but_weaker = _scanner_device(BONDED, -91)

    with patch(SCAN_TARGET, return_value=[unbonded_but_closest, bonded_but_weaker]):
        result = build_candidates(
            object(), MAC, BONDED, allowed_sources=[BONDED]
        )

    assert result == [bonded_but_weaker.ble_device]


def test_no_allowed_list_keeps_every_path() -> None:
    """A pairing window (and a fresh install) must reach unbonded proxies."""
    unbonded = _scanner_device(UNBONDED, -65)
    bonded = _scanner_device(BONDED, -91)

    with patch(SCAN_TARGET, return_value=[unbonded, bonded]):
        result = build_candidates(object(), MAC, BONDED, allowed_sources=None)

    # Preferred still leads; the point is that nothing is dropped.
    assert result == [bonded.ble_device, unbonded.ble_device]


def test_allowed_list_still_honours_preferred_ordering() -> None:
    other_bonded = _scanner_device(UNBONDED, -40)
    preferred = _scanner_device(BONDED, -90)

    with patch(SCAN_TARGET, return_value=[other_bonded, preferred]):
        result = build_candidates(
            object(), MAC, BONDED, allowed_sources=[BONDED, UNBONDED]
        )

    assert result == [preferred.ble_device, other_bonded.ble_device]


# --------------------------------------------------------------------------
# Pairing state that outlives a config-entry retry
# --------------------------------------------------------------------------


def _controller(status: ConnectionStatus, source: str | None = BONDED) -> MagicMock:
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = status
    controller.connection.connected_source = source
    controller.connection.pair_timeout = 8.0
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


# The retry-survival guarantee itself (a rebuilt coordinator must not touch
# the device) lives in test_dead_bond_quarantine.py, where the suspension
# that provides it is specified.


async def test_reconnect_opens_a_pairing_window(hass: HomeAssistant) -> None:
    """Pressing reconnect means the user is at the thermostat, ready to accept."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    controller = _controller(ConnectionStatus.DISCONNECTED)
    controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [BONDED]))
    controller.stop = AsyncMock()
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        with patch(
            "custom_components.daikin_madoka.coordinator.asyncio.sleep", AsyncMock()
        ):
            await coordinator.async_reconnect()

    state = async_pairing_state(hass, MAC)
    assert state.pairing_window is True
    assert controller.connection.pair_timeout == PAIRING_WINDOW_TIMEOUT

    await coordinator.async_shutdown()


async def test_successful_connect_records_the_bonded_source(
    hass: HomeAssistant,
) -> None:
    """Only a proxy that actually completed a session counts as bonded."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass, entry, _controller(ConnectionStatus.CONNECTED))

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert entry.data[CONF_BONDED_SOURCES] == [BONDED]
    assert entry.data[CONF_PREFERRED_SOURCE] == BONDED


async def test_bonded_sources_accumulate_without_duplicates(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_MAC: MAC, CONF_BONDED_SOURCES: [UNBONDED]}
    )
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass, entry, _controller(ConnectionStatus.CONNECTED))

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    assert entry.data[CONF_BONDED_SOURCES] == [UNBONDED, BONDED]


async def test_successful_connect_closes_the_pairing_window(
    hass: HomeAssistant, freezer
) -> None:
    """The window is for one deliberate attempt, not a standing permission."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    state = async_pairing_state(hass, MAC)
    state.pairing_window = True
    coordinator = _coordinator(hass, entry, _controller(ConnectionStatus.CONNECTED))

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert state.pairing_window is False
