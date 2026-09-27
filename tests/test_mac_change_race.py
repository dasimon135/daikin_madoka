"""A coordinator must only ever write to an entry that still describes it.

Reconfigure can rewrite an entry for a different thermostat (new MAC) while
the coordinator of the old one is still running: the reload that replaces it
comes after the rewrite, and a poll in flight finishes whenever it finishes.
Every writer used to check only that the entry EXISTS, so the old device's
verdict, preferred proxy and bond list could land in the entry of the new
one: a replacement thermostat quarantined, or restricted to proxies that
never paired with it, before it had been polled once.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka import ConnectionException
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.daikin_madoka.const import (
    CONF_BONDED_SOURCES,
    CONF_MAC,
    CONF_PAIRING_STATE,
    CONF_PREFERRED_SOURCE,
    DOMAIN,
)
from custom_components.daikin_madoka.coordinator import (
    PAIRING_STATE_KEY,
    MadokaCoordinator,
    async_restore_pairing_state,
)

OLD_MAC = "D0:CF:13:0F:11:F6"
NEW_MAC = "D0:CF:13:0F:22:A1"
PROXY_A = "AA:BB:CC:11:22:33"
PROXY_B = "AA:BB:CC:44:55:66"
BLUETOOTH = "homeassistant.components.bluetooth"


def _controller() -> MagicMock:
    controller = MagicMock()
    controller.connection.address = OLD_MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.connection.connected_source = PROXY_A
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


def _rewrite_for_new_thermostat(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """What reconfigure leaves behind before the reload replaces the coordinator."""
    hass.config_entries.async_update_entry(entry, data={CONF_MAC: NEW_MAC})


async def test_the_old_devices_verdict_does_not_land_in_the_new_entry(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: OLD_MAC})
    entry.add_to_hass(hass)
    controller = _controller()
    controller.start = AsyncMock(side_effect=ConnectionException("gone"))
    coordinator = _coordinator(hass, entry, controller)

    _rewrite_for_new_thermostat(hass, entry)
    with patch(f"{BLUETOOTH}.async_address_present", return_value=True):
        await coordinator.async_refresh()

    assert coordinator.fail_count == 1
    assert CONF_PAIRING_STATE not in entry.data


async def test_the_old_devices_proxy_is_not_recorded_as_a_bond_of_the_new_one(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: OLD_MAC})
    entry.add_to_hass(hass)
    controller = _controller()

    async def _connect() -> None:
        controller.connection.connection_status = ConnectionStatus.CONNECTED

    controller.start = AsyncMock(side_effect=_connect)
    coordinator = _coordinator(hass, entry, controller)

    _rewrite_for_new_thermostat(hass, entry)
    with patch(f"{BLUETOOTH}.async_address_present", return_value=True):
        await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert CONF_BONDED_SOURCES not in entry.data
    assert CONF_PREFERRED_SOURCE not in entry.data


async def test_the_old_device_cannot_evict_a_bond_of_the_new_one(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: OLD_MAC})
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass, entry, _controller())

    hass.config_entries.async_update_entry(
        entry, data={CONF_MAC: NEW_MAC, CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]}
    )

    with patch(
        f"{BLUETOOTH}.async_scanner_by_source",
        return_value=SimpleNamespace(name="Proxy"),
    ):
        assert coordinator._async_drop_bonded_source(PROXY_A, "test") is False
    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_A, PROXY_B]


async def test_restore_only_rehydrates_the_entrys_own_devices(
    hass: HomeAssistant,
) -> None:
    """A verdict left under the old MAC must not come back as a live state."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MAC: NEW_MAC,
            CONF_PAIRING_STATE: {
                OLD_MAC: {"suspended": True},
                NEW_MAC: {"fail_count": 2},
            },
        },
    )
    entry.add_to_hass(hass)

    async_restore_pairing_state(hass, entry)

    store = hass.data[PAIRING_STATE_KEY]
    assert OLD_MAC not in store
    assert store[NEW_MAC].fail_count == 2
