"""Small inconsistencies in how the verdict is kept, each with its own symptom.

- A successful authenticated connect proved the bond, yet our copy of the
  timeout streak survived it whenever the GATT poll then failed, and was fed
  back to the next Connection (max(ours, library)).
- A proven refusal after a pairing_slow verdict left the pairing_slow repair
  on screen next to pairing_required (two opposite stories) and the 900s
  cadence in place with backoff already False, so nothing would lift it.
- The suspended fast failure touches no radio, yet counted as a radio failure
  and could engage the brake of a device that is not being contacted at all.
- A legacy multi-MAC entry has no single device to re-pair: its reauth flow
  can only abort, so starting it just flashes a notification.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka import ConnectionException, PairingRequiredError
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.daikin_madoka.const import (
    CONF_MAC,
    DOMAIN,
    TIMEOUT_BACKOFF_INTERVAL_S,
)
from custom_components.daikin_madoka.coordinator import (
    BACKOFF_PAIRING,
    UNREACHABLE_THRESHOLD,
    MadokaCoordinator,
    async_pairing_state,
)

MAC = "D0:CF:13:0F:11:F6"
SOURCE = "AA:BB:CC:11:22:33"
BLUETOOTH = "homeassistant.components.bluetooth"
NORMAL = timedelta(seconds=60)
BACKOFF = timedelta(seconds=TIMEOUT_BACKOFF_INTERVAL_S)


def _controller() -> MagicMock:
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.connection.connected_source = SOURCE
    controller.connection.pairing_timeout_rounds = 0
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


def _entry(hass: HomeAssistant, **data) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=data or {CONF_MAC: MAC})
    entry.add_to_hass(hass)
    return entry


async def _refresh(coordinator: MadokaCoordinator) -> None:
    with (
        patch(f"{BLUETOOTH}.async_address_present", return_value=True),
        patch(
            f"{BLUETOOTH}.async_scanner_by_source",
            return_value=SimpleNamespace(name="Proxy"),
        ),
    ):
        await coordinator.async_refresh()


async def test_an_authenticated_connect_ends_our_timeout_streak(
    hass: HomeAssistant,
) -> None:
    """The library zeroes its own counter on connect; ours must follow."""
    entry = _entry(hass)
    controller = _controller()

    async def _connect() -> None:
        controller.connection.connection_status = ConnectionStatus.CONNECTED

    controller.start = AsyncMock(side_effect=_connect)
    controller.update = AsyncMock(side_effect=ConnectionException("gatt lost"))
    coordinator = _coordinator(hass, entry, controller)
    async_pairing_state(hass, MAC).timeout_rounds = 2

    await _refresh(coordinator)

    assert async_pairing_state(hass, MAC).timeout_rounds == 0


async def test_a_proven_refusal_replaces_the_pairing_slow_story(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    controller = _controller()
    controller.start = AsyncMock(
        side_effect=PairingRequiredError(
            MAC, [SOURCE], reason="timeout_streak", timeout_rounds=3
        )
    )
    coordinator = _coordinator(hass, entry, controller)
    await _refresh(coordinator)
    assert coordinator.pairing_slow_issue_active is True
    assert coordinator.backoff_reason == BACKOFF_PAIRING
    assert coordinator.update_interval == BACKOFF

    controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE]))
    await _refresh(coordinator)

    state = async_pairing_state(hass, MAC)
    registry = ir.async_get(hass)
    assert state.suspended is True
    assert registry.async_get_issue(DOMAIN, f"pairing_required_{MAC}") is not None
    # One story on screen, not two opposite ones...
    assert registry.async_get_issue(DOMAIN, f"pairing_slow_{MAC}") is None
    assert coordinator.pairing_slow_issue_active is False
    assert coordinator.pairing_slow_suspected is False
    # ...and no brake half-lifted: the flag, its reason and the cadence agree.
    assert state.backoff is False
    assert state.backoff_reason is None
    assert coordinator.update_interval == NORMAL


async def test_a_suspended_poll_is_not_a_radio_failure(hass: HomeAssistant) -> None:
    """Nothing is contacted while suspended, so there is nothing to brake."""
    entry = _entry(hass)
    controller = _controller()
    controller.start = AsyncMock()
    coordinator = _coordinator(hass, entry, controller)
    state = async_pairing_state(hass, MAC)
    state.suspended = True
    state.last_error = PairingRequiredError(MAC, [SOURCE])

    for _ in range(UNREACHABLE_THRESHOLD + 1):
        await _refresh(coordinator)

    controller.start.assert_not_awaited()
    assert state.radio_fail_count == 0
    assert state.backoff is False
    assert coordinator.update_interval == NORMAL


async def test_a_legacy_entry_gets_no_reauth_flow(hass: HomeAssistant) -> None:
    entry = _entry(hass, **{CONF_DEVICES: [MAC]})
    entry.mock_state(hass, config_entries.ConfigEntryState.LOADED)
    controller = _controller()
    controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE]))
    coordinator = _coordinator(hass, entry, controller)

    with patch.object(entry, "async_start_reauth") as start_reauth:
        await _refresh(coordinator)

    assert async_pairing_state(hass, MAC).suspended is True
    start_reauth.assert_not_called()
