"""Smoke tests for the sensor, binary_sensor, button and number platforms."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

from custom_components.daikin_madoka import (
    binary_sensor as binary_sensor_platform,
    button as button_platform,
    number as number_platform,
    sensor as sensor_platform,
)
from custom_components.daikin_madoka.const import (
    CONF_MAC,
    CONF_PREFERRED_SOURCE,
    DOMAIN,
)
from custom_components.daikin_madoka.coordinator import MadokaCoordinator

MAC = "D0:CF:13:0F:11:F6"
SOURCE = "AA:BB:CC:11:22:33"

BLUETOOTH = "homeassistant.components.bluetooth"


def _mock_controller() -> MagicMock:
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.CONNECTED
    controller.connection.connected_source = SOURCE
    controller.temperatures.status = SimpleNamespace(indoor=23.0, outdoor=30.0)
    controller.clean_filter_indicator.status = SimpleNamespace(
        clean_filter_indicator=True
    )
    controller.eye_brightness.status = SimpleNamespace(brightness=10)
    controller.eye_brightness.update = AsyncMock()
    controller.reset_clean_filter_timer.update = AsyncMock()
    return controller


def _coordinator(
    hass: HomeAssistant,
    controller: MagicMock,
    entry_data: dict | None = None,
) -> tuple[MadokaCoordinator, MockConfigEntry]:
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data or {CONF_MAC: MAC})
    entry.add_to_hass(hass)
    token = config_entries.current_entry.set(entry)
    try:
        coordinator = MadokaCoordinator(hass, controller, scan_interval=60)
    finally:
        config_entries.current_entry.reset(token)
    coordinator.async_boost = AsyncMock()
    return coordinator, entry


async def _setup_platform(
    platform: object,
    hass: HomeAssistant,
    coordinator: MadokaCoordinator,
    entry: MockConfigEntry,
) -> list[Entity]:
    """Run the platform's async_setup_entry and collect the added entities."""
    entry.runtime_data = {MAC: coordinator}
    added: list[Entity] = []

    def _collect(entities) -> None:
        added.extend(entities)

    await platform.async_setup_entry(hass, entry, _collect)
    for entity in added:
        entity.hass = hass
    return added


# --- sensor ----------------------------------------------------------------


async def test_sensor_setup_creates_all_sensors(hass: HomeAssistant) -> None:
    coordinator, entry = _coordinator(hass, _mock_controller())
    entities = await _setup_platform(sensor_platform, hass, coordinator, entry)

    assert {entity.unique_id for entity in entities} == {
        f"{MAC}_indoor_temperature",
        f"{MAC}_outdoor_temperature",
        f"{MAC}_rssi",
        f"{MAC}_operating_time",
        f"{MAC}_connection_source",
    }


async def test_temperature_sensors_report_controller_values(
    hass: HomeAssistant,
) -> None:
    controller = _mock_controller()
    coordinator, entry = _coordinator(hass, controller)
    entities = await _setup_platform(sensor_platform, hass, coordinator, entry)
    by_id = {entity.unique_id: entity for entity in entities}

    assert by_id[f"{MAC}_indoor_temperature"].native_value == 23.0
    assert by_id[f"{MAC}_outdoor_temperature"].native_value == 30.0

    controller.temperatures.status = None
    assert by_id[f"{MAC}_indoor_temperature"].native_value is None
    assert by_id[f"{MAC}_outdoor_temperature"].native_value is None


async def test_connection_source_shows_live_scanner_name(
    hass: HomeAssistant,
) -> None:
    coordinator, entry = _coordinator(hass, _mock_controller())
    entities = await _setup_platform(sensor_platform, hass, coordinator, entry)
    sensor = next(
        entity
        for entity in entities
        if entity.unique_id == f"{MAC}_connection_source"
    )

    with patch(
        f"{BLUETOOTH}.async_scanner_by_source",
        return_value=SimpleNamespace(name="Proxy Salon"),
    ):
        assert sensor.native_value == "Proxy Salon"


async def test_connection_source_falls_back_to_preferred_proxy(
    hass: HomeAssistant,
) -> None:
    controller = _mock_controller()
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    coordinator, entry = _coordinator(
        hass, controller, {CONF_MAC: MAC, CONF_PREFERRED_SOURCE: SOURCE}
    )
    entities = await _setup_platform(sensor_platform, hass, coordinator, entry)
    sensor = next(
        entity
        for entity in entities
        if entity.unique_id == f"{MAC}_connection_source"
    )

    # No scanner currently registered for the source: the MAC itself shows.
    with patch(f"{BLUETOOTH}.async_scanner_by_source", return_value=None):
        assert sensor.native_value == SOURCE


async def test_connection_source_unknown_when_nothing_stored(
    hass: HomeAssistant,
) -> None:
    controller = _mock_controller()
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    coordinator, entry = _coordinator(hass, controller)
    entities = await _setup_platform(sensor_platform, hass, coordinator, entry)
    sensor = next(
        entity
        for entity in entities
        if entity.unique_id == f"{MAC}_connection_source"
    )

    assert sensor.native_value is None


async def test_connection_source_local_adapter(hass: HomeAssistant) -> None:
    controller = _mock_controller()
    controller.connection.connected_source = None
    coordinator, entry = _coordinator(hass, controller)
    entities = await _setup_platform(sensor_platform, hass, coordinator, entry)
    sensor = next(
        entity
        for entity in entities
        if entity.unique_id == f"{MAC}_connection_source"
    )

    assert sensor.native_value == "Local adapter"


# --- binary_sensor ---------------------------------------------------------


async def test_binary_sensor_setup_and_state(hass: HomeAssistant) -> None:
    controller = _mock_controller()
    coordinator, entry = _coordinator(hass, controller)
    entities = await _setup_platform(
        binary_sensor_platform, hass, coordinator, entry
    )

    assert [entity.unique_id for entity in entities] == [f"{MAC}_clean_filter"]
    sensor = entities[0]
    assert sensor.is_on is True

    controller.clean_filter_indicator.status = SimpleNamespace(
        clean_filter_indicator=False
    )
    assert sensor.is_on is False

    controller.clean_filter_indicator.status = None
    assert sensor.is_on is None


# --- button ----------------------------------------------------------------


async def test_button_setup_and_presses(hass: HomeAssistant) -> None:
    controller = _mock_controller()
    coordinator, entry = _coordinator(hass, controller)
    coordinator.async_reconnect = AsyncMock()
    entities = await _setup_platform(button_platform, hass, coordinator, entry)
    by_id = {entity.unique_id: entity for entity in entities}

    assert set(by_id) == {f"{MAC}_reset_filter", f"{MAC}_reconnect"}

    await by_id[f"{MAC}_reset_filter"].async_press()
    assert controller.reset_clean_filter_timer.update.await_count == 1

    await by_id[f"{MAC}_reconnect"].async_press()
    assert coordinator.async_reconnect.await_count == 1

    # The reconnect button exists to recover a dead link: always available.
    coordinator.last_update_success = False
    assert by_id[f"{MAC}_reconnect"].available is True


# --- number ----------------------------------------------------------------


async def test_number_setup_state_and_write(hass: HomeAssistant) -> None:
    controller = _mock_controller()
    coordinator, entry = _coordinator(hass, controller)
    entities = await _setup_platform(number_platform, hass, coordinator, entry)

    assert [entity.unique_id for entity in entities] == [f"{MAC}_eye_brightness"]
    number = entities[0]
    assert number.native_value == 10

    await number.async_set_native_value(5.0)
    status = controller.eye_brightness.update.call_args[0][0]
    assert status.brightness == 5

    controller.eye_brightness.status = None
    assert number.native_value is None
