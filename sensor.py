"""Support for Daikin AC sensors."""
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTemperature

from . import DOMAIN
from .const import (
    CONTROLLERS,
)

from pymadoka import Controller
from pymadoka import ConnectionException
from pymadoka.connection import ConnectionStatus


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Old way of setting up the Daikin sensors.

    Can only be called when a user accidentally mentions the platform in their
    config. But even in that case it would have been ignored.
    """


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin climate based on config_entry."""
    ent = []
    for controller in hass.data[DOMAIN][entry.entry_id][CONTROLLERS].values():
        ent.append(MadokaSensor(controller))
    async_add_entities(ent)


class MadokaSensor(SensorEntity):
    """Representation of a Sensor."""

    def __init__(self, controller: Controller) -> None:
        """Initialize the sensor."""
        self.controller = controller
 
    @property   
    def available(self):
        """Return the availability."""
        return self.controller.connection.connection_status is ConnectionStatus.CONNECTED

    @property
    def unique_id(self):
        """Return a unique ID."""
        return f"{self.controller.connection.address}_temperature"

    @property
    def name(self):
        """Return the name of the thermostat, if any."""
        base_name = self.controller.connection.name
        if base_name is None:
            base_name = self.controller.connection.address
        return f"{base_name} Temperature"


    @property
    def native_value(self):
        """Return the internal state of the sensor."""
        if self.controller.temperatures.status is None:
            return None
        return self.controller.temperatures.status.indoor

    @property
    def device_class(self):
        """Return the class of this device."""
        return SensorDeviceClass.TEMPERATURE

    @property
    def icon(self):
        """Return the icon of this device."""
        return None

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def state_class(self):
        """Return the state class for long-term statistics support."""
        return SensorStateClass.MEASUREMENT

    async def async_update(self):
        """Retrieve latest state."""
        try:
            await self.controller.temperatures.query()
        except ConnectionAbortedError:
            pass
        except ConnectionException:
            pass

    @property
    def device_info(self):
        """Return a device description for device registry."""
        return {
            "identifiers": {(DOMAIN, self.controller.connection.address)},
            "name": self.controller.connection.name
            if self.controller.connection.name is not None
            else self.controller.connection.address,
            "manufacturer": "DAIKIN",
        }
