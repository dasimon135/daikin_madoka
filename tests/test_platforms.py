"""Smoke tests for the sensor, binary_sensor, button and number platforms."""

from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

from custom_components.daikin_madoka import (
    binary_sensor as binary_sensor_platform,
)
from custom_components.daikin_madoka import (
    button as button_platform,
)
from custom_components.daikin_madoka import (
    number as number_platform,
)
from custom_components.daikin_madoka import (
    sensor as sensor_platform,
)
from custom_components.daikin_madoka.const import (
    CONF_DEVICE_TYPE,
    CONF_ENABLE_ENERGY,
    CONF_MAC,
    CONF_PREFERRED_SOURCE,
    DEVICE_TYPE_VENTILATION,
    DOMAIN,
    THERMISTOR_SENSORS,
)
from custom_components.daikin_madoka.coordinator import MadokaCoordinator

MAC ="D0:CF:13:0F:11:F6"
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
    controller.unit_status.status = SimpleNamespace(
        defrost=False,
        error=False,
        warning=False,
        alarm=False,
        error_code=None,
        last_error_code=None,
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

    assert coordinator.controller.energy_consumption is None
    assert {entity.unique_id for entity in entities} == {
        f"{MAC}_indoor_temperature",
        f"{MAC}_outdoor_temperature",
        f"{MAC}_error_code",
        f"{MAC}_rssi",
        f"{MAC}_operating_time",
        f"{MAC}_connection_source",
        f"{MAC}_connection_status",
    } | THERMISTOR_UNIQUE_IDS


THERMISTOR_UNIQUE_IDS = {f"{MAC}_{key}" for key in THERMISTOR_SENSORS}


async def test_thermistor_sensors_are_disabled_diagnostics_reading_the_coordinator(
    hass: HomeAssistant,
) -> None:
    """Six extra BLE round trips every 5 min are opt-in, per entity (#162)."""
    coordinator, entry = _coordinator(hass, _mock_controller())
    entities = await _setup_platform(sensor_platform, hass, coordinator, entry)
    by_id = {entity.unique_id: entity for entity in entities}
    thermistors = [by_id[unique_id] for unique_id in THERMISTOR_UNIQUE_IDS]

    for entity in thermistors:
        assert entity.entity_registry_enabled_default is False
        assert entity.entity_category is EntityCategory.DIAGNOSTIC

    outdoor_air = by_id[f"{MAC}_thermistor_outdoor_air"]
    assert outdoor_air.native_value is None
    coordinator.thermistors = MagicMock(
        status=SimpleNamespace(
            indoor_suction=14,
            indoor_liquid_pipe=14,
            indoor_gas_pipe=13,
            outdoor_air=10,
            outdoor_heat_exchanger=13,
            outdoor_discharge=None,
        )
    )
    coordinator._thermistors_read_at = monotonic()
    assert outdoor_air.native_value == 10
    assert by_id[f"{MAC}_thermistor_outdoor_discharge"].native_value is None


async def test_the_read_exists_only_with_an_enabled_thermistor_entity(
    hass: HomeAssistant,
) -> None:
    from homeassistant.helpers import entity_registry as er

    coordinator, _ = _coordinator(hass, _mock_controller())
    assert coordinator.thermistors is None

    registry = er.async_get(hass)
    disabled = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{MAC}_thermistor_outdoor_air",
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    coordinator, _ = _coordinator(hass, _mock_controller())
    assert coordinator.thermistors is None

    registry.async_update_entity(disabled.entity_id, disabled_by=None)
    coordinator, _ = _coordinator(hass, _mock_controller())
    assert coordinator.thermistors is not None


ENERGY_UNIQUE_IDS = {
    f"{MAC}_energy_today",
    f"{MAC}_energy_yesterday",
    f"{MAC}_energy_this_week",
    f"{MAC}_energy_last_week",
    f"{MAC}_energy_this_year",
    f"{MAC}_energy_last_year",
}


async def test_energy_sensors_exist_only_when_the_option_is_on(
    hass: HomeAssistant,
) -> None:
    """Off is the default, and six sensors stuck at unknown are not a feature."""
    coordinator, entry = _coordinator(hass, _mock_controller())
    hass.config_entries.async_update_entry(entry, options={CONF_ENABLE_ENERGY: True})

    entities = await _setup_platform(sensor_platform, hass, coordinator, entry)

    assert ENERGY_UNIQUE_IDS <= {entity.unique_id for entity in entities}


async def test_turning_the_option_off_removes_the_energy_sensors(
    hass: HomeAssistant,
) -> None:
    """Including the ones every install created before they followed the option."""
    from homeassistant.helpers import entity_registry as er

    coordinator, entry = _coordinator(hass, _mock_controller())
    registry = er.async_get(hass)
    for unique_id in ENERGY_UNIQUE_IDS:
        registry.async_get_or_create("sensor", DOMAIN, unique_id, config_entry=entry)
    kept = registry.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}_indoor_temperature", config_entry=entry
    )

    await _setup_platform(sensor_platform, hass, coordinator, entry)

    for unique_id in ENERGY_UNIQUE_IDS:
        assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None
    assert registry.async_get(kept.entity_id) is not None


async def test_ventilation_setup_skips_the_outdoor_sensor(
    hass: HomeAssistant,
) -> None:
    """A VAM is indoor-only: an outdoor sensor would never leave unknown."""
    coordinator, entry = _coordinator(
        hass,
        _mock_controller(),
        {CONF_MAC: MAC, CONF_DEVICE_TYPE: DEVICE_TYPE_VENTILATION},
    )
    entities = await _setup_platform(sensor_platform, hass, coordinator, entry)

    unique_ids = {entity.unique_id for entity in entities}
    assert f"{MAC}_indoor_temperature" in unique_ids
    assert f"{MAC}_outdoor_temperature" not in unique_ids
    assert f"{MAC}_error_code" not in unique_ids
    assert not THERMISTOR_UNIQUE_IDS & unique_ids


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


async def test_energy_sensors_report_controller_counter_totals(
    hass: HomeAssistant,
) -> None:
    coordinator, entry = _coordinator(hass, _mock_controller())
    hass.config_entries.async_update_entry(entry, options={CONF_ENABLE_ENERGY: True})
    coordinator.data = {
        "energy_consumption": {
            "energy_today": (12.3, 1.0),
            "energy_yesterday": (9.8,),
            "energy_this_week": (45.6,),
            "energy_last_week": (41.2,),
            "energy_this_year": (678.9,),
            "energy_last_year": (1234.5,),
        }
    }
    entities = await _setup_platform(sensor_platform, hass, coordinator, entry)
    by_id = {entity.unique_id: entity for entity in entities}

    assert by_id[f"{MAC}_energy_today"].native_value == 12.3
    assert by_id[f"{MAC}_energy_yesterday"].native_value == 9.8
    assert by_id[f"{MAC}_energy_this_week"].native_value == 45.6
    assert by_id[f"{MAC}_energy_last_week"].native_value == 41.2
    assert by_id[f"{MAC}_energy_this_year"].native_value == 678.9
    assert by_id[f"{MAC}_energy_last_year"].native_value == 1234.5

    assert (
        by_id[f"{MAC}_energy_today"].state_class
        is SensorStateClass.TOTAL_INCREASING
    )
    for period in (
        "energy_yesterday",
        "energy_this_week",
        "energy_last_week",
        "energy_this_year",
        "energy_last_year",
    ):
        assert by_id[f"{MAC}_{period}"].state_class is None


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

    assert [entity.unique_id for entity in entities] == [
        f"{MAC}_clean_filter",
        f"{MAC}_defrost",
        f"{MAC}_unit_error",
        f"{MAC}_unit_warning",
        f"{MAC}_unit_alarm",
    ]
    sensor = entities[0]
    assert sensor.is_on is True

    controller.clean_filter_indicator.status = SimpleNamespace(
        clean_filter_indicator=False
    )
    assert sensor.is_on is False

    controller.clean_filter_indicator.status = None
    assert sensor.is_on is None


async def test_unit_status_sensors_report_the_flags(hass: HomeAssistant) -> None:
    """#159: defrost drives automations; the conditions are diagnostic."""
    controller = _mock_controller()
    coordinator, entry = _coordinator(hass, controller)
    by_id = {
        entity.unique_id: entity
        for entity in await _setup_platform(
            binary_sensor_platform, hass, coordinator, entry
        )
    }
    defrost = by_id[f"{MAC}_defrost"]
    error = by_id[f"{MAC}_unit_error"]

    assert defrost.is_on is False
    assert defrost.entity_category is None
    assert defrost.entity_registry_enabled_default is True
    assert error.entity_category is EntityCategory.DIAGNOSTIC
    assert error.entity_registry_enabled_default is False

    controller.unit_status.status = SimpleNamespace(
        defrost=True, error=True, warning=False, alarm=None, error_code="U4-01"
    )
    assert defrost.is_on is True
    assert error.is_on is True
    assert by_id[f"{MAC}_unit_alarm"].is_on is None

    controller.unit_status.status = None
    assert defrost.is_on is None


async def test_a_vam_gets_no_unit_status_sensors(hass: HomeAssistant) -> None:
    """pymadoka does not ask a VAM for 0x0100's unit status."""
    coordinator, entry = _coordinator(
        hass,
        _mock_controller(),
        {CONF_MAC: MAC, CONF_DEVICE_TYPE: DEVICE_TYPE_VENTILATION},
    )
    entities = await _setup_platform(
        binary_sensor_platform, hass, coordinator, entry
    )

    assert [entity.unique_id for entity in entities] == [f"{MAC}_clean_filter"]


async def test_the_error_code_sensor_shows_the_code(hass: HomeAssistant) -> None:
    controller = _mock_controller()
    coordinator, entry = _coordinator(hass, controller)
    by_id = {
        entity.unique_id: entity
        for entity in await _setup_platform(sensor_platform, hass, coordinator, entry)
    }
    code = by_id[f"{MAC}_error_code"]

    assert code.native_value is None
    assert code.entity_category is EntityCategory.DIAGNOSTIC
    controller.unit_status.status = SimpleNamespace(
        error_code="U4-01", last_error_code="U4-01"
    )
    assert code.native_value == "U4-01"
    # A healthy BRC1H still answers its last recorded code: attribute only.
    controller.unit_status.status = SimpleNamespace(
        error_code=None, last_error_code="U0-00"
    )
    assert code.native_value is None
    assert code.extra_state_attributes == {"last_error_code": "U0-00"}
    controller.unit_status.status = None
    assert code.native_value is None
    assert code.extra_state_attributes is None


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
