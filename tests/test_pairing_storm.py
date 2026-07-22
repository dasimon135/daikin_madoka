"""Don't turn a pairing problem into a pairing storm.

Field incident 2026-07-20. Two behaviours combined to keep one thermostat
permanently unreachable and to jam its Bluetooth stack:

1. Every Madoka entry reconnected at once after a restart, all through the
   same handful of ESPHome proxies. The contention made the SMP encryption of
   already-valid bonds exceed the pairing budget, which the library then read
   as "this device needs pairing".
2. Once a device did report a pairing refusal, every single poll re-attempted
   the connect, and each attempt re-initiated an SMP exchange. Repeated
   prompts jam the BRC1H until its Bluetooth is toggled off and on by hand.

So connects are serialized across devices, and a pairing refusal suspends
automatic reconnects entirely (see test_dead_bond_quarantine.py) — a prompt
only ever appears again when the user asks for one via the reconnect button.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka import PairingRequiredError
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.daikin_madoka.const import CONF_MAC, DOMAIN
from custom_components.daikin_madoka.coordinator import MadokaCoordinator

MAC = "D0:CF:13:0F:11:F6"
OTHER_MAC = "D0:CF:13:0F:11:F7"
SOURCE = "AA:BB:CC:11:22:33"

BLUETOOTH = "homeassistant.components.bluetooth"


def _disconnected_controller(mac: str = MAC) -> MagicMock:
    """Controller stub that is disconnected, so every poll attempts a connect."""
    controller = MagicMock()
    controller.connection.address = mac
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.connection.connected_source = SOURCE
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
            return_value=SimpleNamespace(name="Proxy Salon"),
        ),
    )


def _pairing_coordinator(hass: HomeAssistant) -> MadokaCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    controller = _disconnected_controller()
    controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE]))
    return _coordinator(hass, entry, controller)


async def test_pairing_refusal_does_not_reattempt_on_the_very_next_poll(
    hass: HomeAssistant,
) -> None:
    """Each re-attempt re-initiates SMP; suspend instead of hammering."""
    coordinator = _pairing_coordinator(hass)
    present, scanner = _patched_bluetooth()

    with present, scanner:
        await coordinator.async_refresh()
        assert coordinator.controller.start.await_count == 1

        await coordinator.async_refresh()

    assert coordinator.controller.start.await_count == 1
    assert not coordinator.last_update_success


# The suspension that replaced the retry backoff — indefinite, surviving
# entry retries, lifted by the reconnect button or a successful session — is
# specified in test_dead_bond_quarantine.py.


async def test_connect_attempts_are_serialized_across_devices(
    hass: HomeAssistant,
) -> None:
    """Simultaneous connects through shared proxies are what starts the storm."""
    in_flight = 0
    peak = 0

    async def _slow_connect() -> None:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.02)
        finally:
            in_flight -= 1

    coordinators = []
    for mac in (MAC, OTHER_MAC):
        entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: mac})
        entry.add_to_hass(hass)
        controller = _disconnected_controller(mac)
        controller.start = AsyncMock(side_effect=_slow_connect)
        coordinators.append(_coordinator(hass, entry, controller))

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await asyncio.gather(*(c.async_refresh() for c in coordinators))

    assert peak == 1
