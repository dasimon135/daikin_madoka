"""Support for the Daikin Madoka HVAC."""
import copy
from typing import Any

from pymadoka import (
    FanSpeedEnum,
    FanSpeedStatus,
    OperationModeEnum,
    OperationModeStatus,
    PowerStateStatus,
    SetPointStatus,
)
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate.const import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MAX_TEMP, MIN_TEMP
from .coordinator import MadokaConfigEntry
from .entity import MadokaEntity

# HVACMode.OFF has no Daikin operation mode on purpose: the BRC1H models
# "off" as a power state, not a mode. async_set_hvac_mode handles OFF by
# writing the power state only, and hvac_mode reports OFF from the power
# state before consulting this map.
HA_MODE_TO_DAIKIN = {
    HVACMode.FAN_ONLY: OperationModeEnum.FAN,
    HVACMode.DRY: OperationModeEnum.DRY,
    HVACMode.COOL: OperationModeEnum.COOL,
    HVACMode.HEAT: OperationModeEnum.HEAT,
    HVACMode.AUTO: OperationModeEnum.AUTO,
}

DAIKIN_TO_HA_MODE = {
    OperationModeEnum.FAN: HVACMode.FAN_ONLY,
    OperationModeEnum.DRY: HVACMode.DRY,
    OperationModeEnum.COOL: HVACMode.COOL,
    OperationModeEnum.HEAT: HVACMode.HEAT,
    OperationModeEnum.AUTO: HVACMode.AUTO,
}

HA_FAN_MODE_TO_DAIKIN = {
    FAN_LOW: FanSpeedEnum.LOW,
    FAN_MEDIUM: FanSpeedEnum.MID,
    FAN_HIGH: FanSpeedEnum.HIGH,
    FAN_AUTO: FanSpeedEnum.AUTO,
}

DAIKIN_TO_HA_FAN_MODE = {
    FanSpeedEnum.LOW: FAN_LOW,
    FanSpeedEnum.MID: FAN_MEDIUM,
    FanSpeedEnum.HIGH: FAN_HIGH,
    FanSpeedEnum.AUTO: FAN_AUTO,
}

DAIKIN_TO_HA_CURRENT_HVAC_MODE = {
    OperationModeEnum.FAN: HVACAction.FAN,
    OperationModeEnum.DRY: HVACAction.DRYING,
    OperationModeEnum.COOL: HVACAction.COOLING,
    OperationModeEnum.HEAT: HVACAction.HEATING,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MadokaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Daikin climate based on config_entry."""
    async_add_entities(
        DaikinMadokaClimate(coordinator) for coordinator in entry.runtime_data.values()
    )


class DaikinMadokaClimate(MadokaEntity, ClimateEntity):
    """Representation of a Daikin HVAC."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    # The BLE frames carry 1/128 degree resolution, but pymadoka-ng's SetPointStatus API is
    # whole-degree only (int setpoints, round() on decode), so a finer step
    # would just snap back after the next poll.
    _attr_target_temperature_step = 1
    _attr_hvac_modes = [*HA_MODE_TO_DAIKIN, HVACMode.OFF]
    _attr_fan_modes = list(HA_FAN_MODE_TO_DAIKIN)

    @property
    def _set_point(self) -> SetPointStatus | None:
        return self.controller.set_point.status

    @property
    def _range_active(self) -> bool:
        """Dual setpoint UI applies in AUTO mode when the device has it enabled."""
        return (
            self.hvac_mode == HVACMode.AUTO
            and self._set_point is not None
            and bool(self._set_point.range_enabled)
        )

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return the list of supported features."""
        features = (
            ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if self._range_active:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        else:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        if self.controller.temperatures.status is None:
            return None
        return self.controller.temperatures.status.indoor

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        if self._set_point is None or self._range_active:
            return None
        if self.hvac_mode == HVACMode.HEAT:
            return self._set_point.heating_set_point
        return self._set_point.cooling_set_point

    @property
    def target_temperature_low(self) -> float | None:
        """Return the lower (heating) setpoint in AUTO range mode."""
        if not self._range_active:
            return None
        return self._set_point.heating_set_point

    @property
    def target_temperature_high(self) -> float | None:
        """Return the upper (cooling) setpoint in AUTO range mode."""
        if not self._range_active:
            return None
        return self._set_point.cooling_set_point

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature, read from the device when reported."""
        if self._set_point is not None:
            limits = [
                limit
                for limit in (
                    self._set_point.cooling_lowerlimit,
                    self._set_point.heating_lowerlimit,
                )
                if limit
            ]
            if limits:
                return min(limits)
        return MIN_TEMP

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature, read from the device when reported."""
        if self._set_point is not None:
            limits = [
                limit
                for limit in (
                    self._set_point.cooling_upperlimit,
                    self._set_point.heating_upperlimit,
                )
                if limit
            ]
            if limits:
                return max(limits)
        return MAX_TEMP

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature (single setpoint or AUTO range)."""
        if self._set_point is None or self.controller.operation_mode.status is None:
            return

        # Copy the parsed status so the write echoes the device's own range
        # mode and limits instead of resetting them.
        new_status: SetPointStatus = copy.copy(self._set_point)

        target_low = kwargs.get(ATTR_TARGET_TEMP_LOW)
        target_high = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        target = kwargs.get(ATTR_TEMPERATURE)

        if target_low is not None:
            new_status.heating_set_point = round(target_low)
        if target_high is not None:
            new_status.cooling_set_point = round(target_high)

        if target is not None:
            operation_mode = self.controller.operation_mode.status.operation_mode
            if operation_mode != OperationModeEnum.HEAT:
                new_status.cooling_set_point = round(target)
            if operation_mode != OperationModeEnum.COOL:
                new_status.heating_set_point = round(target)

        await self._async_execute(
            "set target temperature",
            lambda: self.controller.set_point.update(new_status),
        )

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return current operation ie. heat, cool, idle."""
        if self.controller.power_state.status is None:
            return None
        if self.controller.operation_mode.status is None:
            return None

        if self.controller.power_state.status.turn_on is False:
            return HVACMode.OFF

        return DAIKIN_TO_HA_MODE.get(
            self.controller.operation_mode.status.operation_mode
        )

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the HVAC current action."""
        if self.controller.power_state.status is None:
            return None
        if self.controller.operation_mode.status is None:
            return None

        if self.controller.power_state.status.turn_on is False:
            return HVACAction.OFF

        if (
            self.controller.operation_mode.status.operation_mode
            == OperationModeEnum.AUTO
        ):
            current = self.current_temperature
            if current is None:
                return None
            if self._range_active:
                low = self.target_temperature_low
                high = self.target_temperature_high
                if low is None or high is None:
                    return None
                if current < low:
                    return HVACAction.HEATING
                if current > high:
                    return HVACAction.COOLING
                return HVACAction.IDLE
            if self.target_temperature is None:
                return None
            if self.target_temperature >= current:
                return HVACAction.HEATING
            return HVACAction.COOLING

        return DAIKIN_TO_HA_CURRENT_HVAC_MODE.get(
            self.controller.operation_mode.status.operation_mode
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode.

        OFF is a power-state write only (the device keeps its last operation
        mode); every other mode writes the operation mode and turns the unit
        on in the same session.
        """
        calls = []
        if hvac_mode != HVACMode.OFF:
            calls.append(
                lambda: self.controller.operation_mode.update(
                    OperationModeStatus(HA_MODE_TO_DAIKIN[hvac_mode])
                )
            )
        calls.append(
            lambda: self.controller.power_state.update(
                PowerStateStatus(hvac_mode != HVACMode.OFF)
            )
        )
        await self._async_execute("set HVAC mode", *calls)

    @property
    def fan_mode(self) -> str | None:
        """Return the fan setting."""
        if self.controller.fan_speed.status is None:
            return None
        if self.hvac_mode == HVACMode.HEAT:
            return DAIKIN_TO_HA_FAN_MODE.get(
                self.controller.fan_speed.status.heating_fan_speed
            )
        return DAIKIN_TO_HA_FAN_MODE.get(
            self.controller.fan_speed.status.cooling_fan_speed
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan mode."""
        await self._async_execute(
            "set fan mode",
            lambda: self.controller.fan_speed.update(
                FanSpeedStatus(
                    HA_FAN_MODE_TO_DAIKIN.get(fan_mode),
                    HA_FAN_MODE_TO_DAIKIN.get(fan_mode),
                )
            ),
        )

    async def async_turn_on(self) -> None:
        """Turn device on."""
        await self._async_execute(
            "turn on", lambda: self.controller.power_state.update(PowerStateStatus(True))
        )

    async def async_turn_off(self) -> None:
        """Turn device off."""
        await self._async_execute(
            "turn off",
            lambda: self.controller.power_state.update(PowerStateStatus(False)),
        )
