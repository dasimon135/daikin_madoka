"""The device information is read again once a poll proves the link works.

Setup asks for it before the BLE link exists, where pymadoka answers with an
empty dict, so without this the device page carries no revision at all.

What the service actually publishes is the BRC1H's Bluetooth radio, not the
Daikin controller: these tests pin that it is reported as the radio's and never
dressed up as a thermostat marking.
"""

from unittest.mock import AsyncMock, MagicMock

from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.daikin_madoka.const import (
    CONF_FRIENDLY_NAME,
    CONF_MAC,
    DEVICE_INFO_MAX_ATTEMPTS,
    DOMAIN,
)
from custom_components.daikin_madoka.coordinator import MadokaCoordinator

MAC = "D0:CF:13:0F:11:F6"

# What a real BRC1H publishes: the service describes its Bluetooth radio, a
# Universal Electronics module, not the Daikin controller around it.
INFO = {
    "Device Name": "UE878 RF MODULE",
    "Manufacturer Name String": "Universal Electronics, Inc.",
    "Model Number String": "0.1",
    "Firmware Revision String": "BL C0",
    "Hardware Revision String": "UEIS-15288",
    "Software Revision String": "7031.05.17",
}


def _controller(info: dict[str, str] | None = None) -> MagicMock:
    """Controller stub whose poll succeeds and whose info starts out empty."""
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.CONNECTED
    controller.connection.connected_source = None
    controller.connection.pairing_timeout_rounds = 0
    controller.update = AsyncMock()
    controller.refresh_status.return_value = {"set_point": {"cooling_set_point": 25}}
    controller.info = dict(info) if info else {}
    return controller


def _answers_after(controller: MagicMock, attempts: int) -> AsyncMock:
    """Make read_info() populate info only on the nth call, like a slow link."""
    state = {"calls": 0}

    async def _read_info() -> dict[str, str]:
        state["calls"] += 1
        if state["calls"] >= attempts:
            controller.info = dict(INFO)
        return controller.info

    return AsyncMock(side_effect=_read_info)


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_MAC: MAC, CONF_FRIENDLY_NAME: "Salon"}
    )
    entry.add_to_hass(hass)
    return entry


def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, controller: MagicMock
) -> MadokaCoordinator:
    token = config_entries.current_entry.set(entry)
    try:
        return MadokaCoordinator(hass, controller, scan_interval=60)
    finally:
        config_entries.current_entry.reset(token)


def _register(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    """Create the device the way the entity platform does during setup."""
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, MAC)},
        manufacturer="DAIKIN",
        model="BRC1H",
    )


async def test_a_successful_poll_fills_in_the_model_and_firmware(
    hass: HomeAssistant,
) -> None:
    """Setup missed it; the first good poll reads it and updates the registry."""
    entry = _entry(hass)
    controller = _controller()
    controller.read_info = _answers_after(controller, 1)
    device = _register(hass, entry)
    coordinator = _coordinator(hass, entry, controller)

    await coordinator.async_refresh()
    assert coordinator.last_update_success

    stored = dr.async_get(hass).async_get(device.id)
    assert stored is not None
    # The radio's model number is not a thermostat marking, so it is not
    # appended to one; its revisions are reported but labelled as the radio's.
    assert stored.model == "BRC1H"
    assert stored.sw_version == "RF module 7031.05.17"
    assert stored.hw_version == "UEIS-15288"


async def test_it_keeps_trying_across_polls_until_the_device_answers(
    hass: HomeAssistant,
) -> None:
    """A link that is up is not a link that has finished discovering services."""
    entry = _entry(hass)
    controller = _controller()
    controller.read_info = _answers_after(controller, 2)
    device = _register(hass, entry)
    coordinator = _coordinator(hass, entry, controller)

    await coordinator.async_refresh()
    assert dr.async_get(hass).async_get(device.id).sw_version is None

    await coordinator.async_refresh()
    assert dr.async_get(hass).async_get(device.id).sw_version == "RF module 7031.05.17"


async def test_a_controller_that_never_answers_is_not_re_enumerated_forever(
    hass: HomeAssistant,
) -> None:
    """read_info() walks every service, so it must not run on every poll."""
    entry = _entry(hass)
    controller = _controller()
    controller.read_info = AsyncMock(return_value={})
    _register(hass, entry)
    coordinator = _coordinator(hass, entry, controller)

    for _ in range(DEVICE_INFO_MAX_ATTEMPTS + 3):
        await coordinator.async_refresh()

    assert controller.read_info.await_count == DEVICE_INFO_MAX_ATTEMPTS


async def test_nothing_is_read_again_when_setup_already_got_it(
    hass: HomeAssistant,
) -> None:
    """The happy path costs no extra GATT traffic at all."""
    entry = _entry(hass)
    controller = _controller(INFO)
    controller.read_info = AsyncMock(return_value=dict(INFO))
    _register(hass, entry)
    coordinator = _coordinator(hass, entry, controller)

    await coordinator.async_refresh()

    controller.read_info.assert_not_awaited()


async def test_a_failing_read_does_not_fail_the_poll(hass: HomeAssistant) -> None:
    """Device information is a nicety; the thermostat still has to work."""
    entry = _entry(hass)
    controller = _controller()
    controller.read_info = AsyncMock(side_effect=RuntimeError("boom"))
    _register(hass, entry)
    coordinator = _coordinator(hass, entry, controller)

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert controller.read_info.await_count == 1
