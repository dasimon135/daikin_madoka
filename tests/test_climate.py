"""Tests for the climate entity: mode/fan mappings, setpoints, power."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymadoka import ConnectionException, FanSpeedEnum, OperationModeEnum
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.components.climate import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate.const import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    FAN_HIGH,
    FAN_LOW,
)
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.daikin_madoka.climate import (
    DAIKIN_TO_HA_MODE,
    HA_MODE_TO_DAIKIN,
    DaikinMadokaClimate,
)
from custom_components.daikin_madoka.const import (
    CONF_MAC,
    DOMAIN,
    MAX_TEMP,
    MIN_TEMP,
)
from custom_components.daikin_madoka.coordinator import MadokaCoordinator

MAC = "D0:CF:13:0F:11:F6"


def _set_point_status(
    cooling: int = 25,
    heating: int = 22,
    range_enabled: bool = False,
    cooling_lowerlimit: int | None = None,
    cooling_upperlimit: int | None = None,
    heating_lowerlimit: int | None = None,
    heating_upperlimit: int | None = None,
) -> SimpleNamespace:
    """Setpoint status shaped like pymadoka's SetPointStatus."""
    return SimpleNamespace(
        cooling_set_point=cooling,
        heating_set_point=heating,
        range_enabled=range_enabled,
        cooling_lowerlimit=cooling_lowerlimit,
        cooling_upperlimit=cooling_upperlimit,
        heating_lowerlimit=heating_lowerlimit,
        heating_upperlimit=heating_upperlimit,
    )


def _mock_controller() -> MagicMock:
    """Controller stub with a full, healthy feature snapshot."""
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.CONNECTED
    controller.power_state.status = SimpleNamespace(turn_on=True)
    controller.power_state.update = AsyncMock()
    controller.operation_mode.status = SimpleNamespace(
        operation_mode=OperationModeEnum.COOL
    )
    controller.operation_mode.update = AsyncMock()
    controller.fan_speed.status = SimpleNamespace(
        cooling_fan_speed=FanSpeedEnum.HIGH, heating_fan_speed=FanSpeedEnum.LOW
    )
    controller.fan_speed.update = AsyncMock()
    controller.temperatures.status = SimpleNamespace(indoor=23.0, outdoor=30.0)
    controller.set_point.status = _set_point_status()
    controller.set_point.update = AsyncMock()
    return controller


def _entity(
    hass: HomeAssistant, controller: MagicMock
) -> DaikinMadokaClimate:
    """Climate entity on a coordinator bound to an entry, boost stubbed out."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    token = config_entries.current_entry.set(entry)
    try:
        coordinator = MadokaCoordinator(hass, controller, scan_interval=60)
    finally:
        config_entries.current_entry.reset(token)
    # Write tests only assert the device command; the refresh boost would
    # schedule timers and re-poll the mocked controller for nothing.
    coordinator.async_boost = AsyncMock()
    entity = DaikinMadokaClimate(coordinator)
    entity.hass = hass
    return entity


# --- Mode mappings ---------------------------------------------------------


@pytest.mark.parametrize(("daikin_mode", "ha_mode"), list(DAIKIN_TO_HA_MODE.items()))
async def test_hvac_mode_maps_daikin_to_ha(
    hass: HomeAssistant, daikin_mode: OperationModeEnum, ha_mode: HVACMode
) -> None:
    controller = _mock_controller()
    controller.operation_mode.status = SimpleNamespace(operation_mode=daikin_mode)
    entity = _entity(hass, controller)

    assert entity.hvac_mode is ha_mode


async def test_hvac_mode_off_comes_from_power_state(hass: HomeAssistant) -> None:
    controller = _mock_controller()
    controller.power_state.status = SimpleNamespace(turn_on=False)
    entity = _entity(hass, controller)

    assert entity.hvac_mode is HVACMode.OFF
    assert entity.hvac_action is HVACAction.OFF


async def test_hvac_mode_unknown_while_statuses_missing(
    hass: HomeAssistant,
) -> None:
    controller = _mock_controller()
    controller.power_state.status = None
    entity = _entity(hass, controller)

    assert entity.hvac_mode is None
    assert entity.hvac_action is None


async def test_off_is_offered_but_not_a_device_mode(hass: HomeAssistant) -> None:
    """OFF must be selectable in HA yet never map to a Daikin operation mode."""
    entity = _entity(hass, _mock_controller())

    assert HVACMode.OFF in entity.hvac_modes
    assert HVACMode.OFF not in HA_MODE_TO_DAIKIN


@pytest.mark.parametrize(("ha_mode", "daikin_mode"), list(HA_MODE_TO_DAIKIN.items()))
async def test_set_hvac_mode_writes_mode_and_power(
    hass: HomeAssistant, ha_mode: HVACMode, daikin_mode: OperationModeEnum
) -> None:
    controller = _mock_controller()
    entity = _entity(hass, controller)

    await entity.async_set_hvac_mode(ha_mode)

    assert (
        controller.operation_mode.update.call_args[0][0].operation_mode
        is daikin_mode
    )
    assert controller.power_state.update.call_args[0][0].turn_on is True


async def test_set_hvac_mode_off_writes_power_only(hass: HomeAssistant) -> None:
    controller = _mock_controller()
    entity = _entity(hass, controller)

    await entity.async_set_hvac_mode(HVACMode.OFF)

    controller.operation_mode.update.assert_not_called()
    assert controller.power_state.update.call_args[0][0].turn_on is False


# --- Fan modes -------------------------------------------------------------


async def test_fan_mode_follows_cooling_speed_outside_heat(
    hass: HomeAssistant,
) -> None:
    entity = _entity(hass, _mock_controller())

    assert entity.fan_mode == FAN_HIGH


async def test_fan_mode_follows_heating_speed_in_heat(hass: HomeAssistant) -> None:
    controller = _mock_controller()
    controller.operation_mode.status = SimpleNamespace(
        operation_mode=OperationModeEnum.HEAT
    )
    entity = _entity(hass, controller)

    assert entity.fan_mode == FAN_LOW


async def test_set_fan_mode_writes_both_speeds(hass: HomeAssistant) -> None:
    controller = _mock_controller()
    entity = _entity(hass, controller)

    await entity.async_set_fan_mode(FAN_LOW)

    status = controller.fan_speed.update.call_args[0][0]
    assert status.cooling_fan_speed is FanSpeedEnum.LOW
    assert status.heating_fan_speed is FanSpeedEnum.LOW


# --- AUTO range / supported features ---------------------------------------


async def test_range_inactive_outside_auto(hass: HomeAssistant) -> None:
    controller = _mock_controller()
    controller.set_point.status = _set_point_status(range_enabled=True)
    entity = _entity(hass, controller)

    # COOL mode: range stays off even though the device has it enabled.
    assert entity._range_active is False
    assert entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE
    assert entity.target_temperature == 25
    assert entity.target_temperature_low is None


async def test_range_active_in_auto_with_range_enabled(
    hass: HomeAssistant,
) -> None:
    controller = _mock_controller()
    controller.operation_mode.status = SimpleNamespace(
        operation_mode=OperationModeEnum.AUTO
    )
    controller.set_point.status = _set_point_status(range_enabled=True)
    entity = _entity(hass, controller)

    assert entity._range_active is True
    assert entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    assert entity.target_temperature is None
    assert entity.target_temperature_low == 22
    assert entity.target_temperature_high == 25


async def test_auto_without_range_uses_single_setpoint(
    hass: HomeAssistant,
) -> None:
    controller = _mock_controller()
    controller.operation_mode.status = SimpleNamespace(
        operation_mode=OperationModeEnum.AUTO
    )
    entity = _entity(hass, controller)

    assert entity._range_active is False
    assert entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE


async def test_hvac_action_in_auto_range(hass: HomeAssistant) -> None:
    controller = _mock_controller()
    controller.operation_mode.status = SimpleNamespace(
        operation_mode=OperationModeEnum.AUTO
    )
    controller.set_point.status = _set_point_status(
        cooling=25, heating=22, range_enabled=True
    )
    entity = _entity(hass, controller)

    controller.temperatures.status = SimpleNamespace(indoor=20.0, outdoor=30.0)
    assert entity.hvac_action is HVACAction.HEATING
    controller.temperatures.status = SimpleNamespace(indoor=27.0, outdoor=30.0)
    assert entity.hvac_action is HVACAction.COOLING
    controller.temperatures.status = SimpleNamespace(indoor=23.0, outdoor=30.0)
    assert entity.hvac_action is HVACAction.IDLE


# --- Temperature limits ----------------------------------------------------


async def test_min_max_from_device_limits(hass: HomeAssistant) -> None:
    controller = _mock_controller()
    controller.set_point.status = _set_point_status(
        cooling_lowerlimit=18,
        cooling_upperlimit=30,
        heating_lowerlimit=10,
        heating_upperlimit=26,
    )
    entity = _entity(hass, controller)

    assert entity.min_temp == 10
    assert entity.max_temp == 30


async def test_min_max_defaults_without_device_limits(
    hass: HomeAssistant,
) -> None:
    controller = _mock_controller()
    entity = _entity(hass, controller)
    assert entity.min_temp == MIN_TEMP
    assert entity.max_temp == MAX_TEMP

    controller.set_point.status = None
    assert entity.min_temp == MIN_TEMP
    assert entity.max_temp == MAX_TEMP


# --- Setpoint writes -------------------------------------------------------


async def test_set_temperature_in_cool_updates_cooling_only(
    hass: HomeAssistant,
) -> None:
    controller = _mock_controller()
    entity = _entity(hass, controller)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 20.6})

    status = controller.set_point.update.call_args[0][0]
    assert status.cooling_set_point == 21
    assert status.heating_set_point == 22  # untouched


async def test_set_temperature_in_heat_updates_heating_only(
    hass: HomeAssistant,
) -> None:
    controller = _mock_controller()
    controller.operation_mode.status = SimpleNamespace(
        operation_mode=OperationModeEnum.HEAT
    )
    entity = _entity(hass, controller)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 19})

    status = controller.set_point.update.call_args[0][0]
    assert status.heating_set_point == 19
    assert status.cooling_set_point == 25  # untouched


async def test_set_temperature_in_auto_updates_both(hass: HomeAssistant) -> None:
    controller = _mock_controller()
    controller.operation_mode.status = SimpleNamespace(
        operation_mode=OperationModeEnum.AUTO
    )
    entity = _entity(hass, controller)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 24})

    status = controller.set_point.update.call_args[0][0]
    assert status.cooling_set_point == 24
    assert status.heating_set_point == 24


async def test_set_temperature_range_updates_both_setpoints(
    hass: HomeAssistant,
) -> None:
    controller = _mock_controller()
    controller.operation_mode.status = SimpleNamespace(
        operation_mode=OperationModeEnum.AUTO
    )
    controller.set_point.status = _set_point_status(range_enabled=True)
    entity = _entity(hass, controller)

    await entity.async_set_temperature(
        **{ATTR_TARGET_TEMP_LOW: 20, ATTR_TARGET_TEMP_HIGH: 26}
    )

    status = controller.set_point.update.call_args[0][0]
    assert status.heating_set_point == 20
    assert status.cooling_set_point == 26
    # The write echoes the device's own range mode instead of resetting it.
    assert status.range_enabled is True


async def test_set_temperature_without_status_is_a_noop(
    hass: HomeAssistant,
) -> None:
    controller = _mock_controller()
    controller.set_point.status = None
    entity = _entity(hass, controller)

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 24})

    controller.set_point.update.assert_not_called()


# --- Power -----------------------------------------------------------------


async def test_turn_on_and_off_write_power_state(hass: HomeAssistant) -> None:
    controller = _mock_controller()
    entity = _entity(hass, controller)

    await entity.async_turn_on()
    assert controller.power_state.update.call_args[0][0].turn_on is True

    await entity.async_turn_off()
    assert controller.power_state.update.call_args[0][0].turn_on is False


async def test_command_failure_raises_homeassistant_error(
    hass: HomeAssistant,
) -> None:
    controller = _mock_controller()
    controller.power_state.update = AsyncMock(
        side_effect=ConnectionException("link lost")
    )
    entity = _entity(hass, controller)

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()
