"""Support for Daikin Madoka numbers."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory

from . import DOMAIN
from .const import CONTROLLERS

from pymadoka import Controller
from pymadoka.feature import ConnectionException, ConnectionStatus
from pymadoka.features.eye_brightness import EyeBrightnessStatus


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin Madoka numbers based on config_entry."""
    entities = []
    for controller in hass.data[DOMAIN][entry.entry_id][CONTROLLERS].values():
        entities.append(MadokaEyeBrightnessNumber(controller))
    async_add_entities(entities)


class MadokaEyeBrightnessNumber(NumberEntity):
    """Number to control the controller eye (LED) brightness."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 19
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, controller: Controller) -> None:
        self.controller = controller

    @property
    def available(self):
        return self.controller.connection.connection_status is ConnectionStatus.CONNECTED

    @property
    def unique_id(self):
        return f"{self.controller.connection.address}_eye_brightness"

    @property
    def name(self):
        base_name = (
            self.controller.connection.name
            if self.controller.connection.name is not None
            else self.controller.connection.address
        )
        return f"{base_name} Eye Brightness"

    @property
    def icon(self):
        return "mdi:brightness-6"

    @property
    def native_value(self):
        if self.controller.eye_brightness.status is None:
            return None
        return self.controller.eye_brightness.status.brightness

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.controller.connection.address)},
            "name": (
                self.controller.connection.name
                if self.controller.connection.name is not None
                else self.controller.connection.address
            ),
            "manufacturer": "DAIKIN",
            "model": "BRC1H",
        }

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.controller.eye_brightness.update(
                EyeBrightnessStatus(int(value))
            )
        except ConnectionAbortedError:
            pass
        except ConnectionException:
            pass
