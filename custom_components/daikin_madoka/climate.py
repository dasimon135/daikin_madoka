"""Support for the Daikin Madoka HVAC."""
import logging

from pymadoka import (
    ConnectionException,
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

from .const import COORDINATORS, DOMAIN, MAX_TEMP, MIN_TEMP
from .entity import MadokaEntity

_LOGGER = logging.getLogger(__name__)

HA_MODE_TO_DAIKIN = {
    HVACMode.FAN_ONLY: OperationModeEnum.FAN,
    HVACMode.DRY: OperationModeEnum.DRY,
    HVACMode.COOL: OperationModeEnum.COOL,
    HVACMode.HEAT: OperationModeEnum.HEAT,
    HVACMode.AUTO: OperationModeEnum.AUTO,
    HVACMode.OFF: OperationModeEnum.AUTO,
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


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    coordinators = hass.data[DOMAIN][entry.entry_id][COORDINATORS]
    async_add_entities(
        DaikinMadokaClimate(coordinator) for coordinator in coordinators.values()
    )


class DaikinMadokaClimate(MadokaEntity, ClimateEntity):
    """Representation of a Daikin HVAC."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_hvac_modes = list(HA_MODE_TO_DAIKIN)
    _attr_fan_modes = list(HA_FAN_MODE_TO_DAIKIN)

    @property
    def _set_point(self):
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
    def supported_features(self):
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
    def current_temperature(self):
        """Return the current temperature."""
        if self.controller.temperatures.status is None:
            return None
        return self.controller.temperatures.status.indoor

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        if self._set_point is None or self._range_active:
            return None
        if self.hvac_mode == HVACMode.HEAT:
            return self._set_point.heating_set_point
        return self._set_point.cooling_set_point

    @property
    def target_temperature_low(self):
        """Return the lower (heating) setpoint in AUTO range mode."""
        if not self._range_active:
            return None
        return self._set_point.heating_set_point

    @property
    def target_temperature_high(self):
        """Return the upper (cooling) setpoint in AUTO range mode."""
        if not self._range_active:
            return None
        return self._set_point.cooling_set_point

    @property
    def min_temp(self):
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
    def max_temp(self):
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

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature (single setpoint or AUTO range)."""
        if self._set_point is None or self.controller.operation_mode.status is None:
            return

        new_cooling_set_point = self._set_point.cooling_set_point
        new_heating_set_point = self._set_point.heating_set_point

        target_low = kwargs.get(ATTR_TARGET_TEMP_LOW)
        target_high = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        target = kwargs.get(ATTR_TEMPERATURE)

        if target_low is not None:
            new_heating_set_point = round(target_low)
        if target_high is not None:
            new_cooling_set_point = round(target_high)

        if target is not None:
            operation_mode = self.controller.operation_mode.status.operation_mode
            if operation_mode != OperationModeEnum.HEAT:
                new_cooling_set_point = round(target)
            if operation_mode != OperationModeEnum.COOL:
                new_heating_set_point = round(target)

        try:
            await self.controller.set_point.update(
                SetPointStatus(new_cooling_set_point, new_heating_set_point)
            )
        except (ConnectionAbortedError, ConnectionException):
            _LOGGER.warning(
                "Could not set target temperature on %s: connection not available",
                self.coordinator.device_name,
            )
        await self.coordinator.async_request_refresh()

    @property
    def hvac_mode(self):
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
    def hvac_action(self):
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
            reference = (
                self.target_temperature
                if not self._range_active
                else self.target_temperature_high
            )
            if reference is None or self.current_temperature is None:
                return None
            if reference >= self.current_temperature:
                return HVACAction.HEATING
            return HVACAction.COOLING

        return DAIKIN_TO_HA_CURRENT_HVAC_MODE.get(
            self.controller.operation_mode.status.operation_mode
        )

    async def async_set_hvac_mode(self, hvac_mode):
        """Set HVAC mode."""
        try:
            if hvac_mode != HVACMode.OFF:
                await self.controller.operation_mode.update(
                    OperationModeStatus(HA_MODE_TO_DAIKIN.get(hvac_mode))
                )
            await self.controller.power_state.update(
                PowerStateStatus(hvac_mode != HVACMode.OFF)
            )
        except (ConnectionAbortedError, ConnectionException):
            _LOGGER.warning(
                "Could not set HVAC mode on %s: connection not available",
                self.coordinator.device_name,
            )
        await self.coordinator.async_request_refresh()

    @property
    def fan_mode(self):
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

    async def async_set_fan_mode(self, fan_mode):
        """Set fan mode."""
        try:
            await self.controller.fan_speed.update(
                FanSpeedStatus(
                    HA_FAN_MODE_TO_DAIKIN.get(fan_mode),
                    HA_FAN_MODE_TO_DAIKIN.get(fan_mode),
                )
            )
        except (ConnectionAbortedError, ConnectionException):
            _LOGGER.warning(
                "Could not set fan mode on %s: connection not available",
                self.coordinator.device_name,
            )
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self):
        """Turn device on."""
        try:
            await self.controller.power_state.update(PowerStateStatus(True))
        except (ConnectionAbortedError, ConnectionException):
            _LOGGER.warning(
                "Could not turn on %s: connection not available",
                self.coordinator.device_name,
            )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self):
        """Turn device off."""
        try:
            await self.controller.power_state.update(PowerStateStatus(False))
        except (ConnectionAbortedError, ConnectionException):
            _LOGGER.warning(
                "Could not turn off %s: connection not available",
                self.coordinator.device_name,
            )
        await self.coordinator.async_request_refresh()
