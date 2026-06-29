"""Support for Daikin AC sensors."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTemperature

from . import DOMAIN
from .const import CONTROLLERS

from pymadoka import Controller
from pymadoka.feature import ConnectionException, ConnectionStatus


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Old way of setting up the Daikin sensors.

    Can only be called when a user accidentally mentions the platform in their
    config. But even in that case it would have been ignored.
    """


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin sensors based on config_entry."""
    entities = []
    for controller in hass.data[DOMAIN][entry.entry_id][CONTROLLERS].values():
        entities.append(MadokaIndoorSensor(controller))
        entities.append(MadokaOutdoorSensor(controller))
    async_add_entities(entities)


class MadokaSensor(SensorEntity):
    """Base representation of a Madoka temperature sensor."""

    def __init__(self, controller: Controller, suffix: str, name: str) -> None:
        """Initialize the sensor."""
        self.controller = controller
        self._suffix = suffix
        self._name = name

    @property
    def available(self):
        """Return the availability."""
        return self.controller.connection.connection_status is ConnectionStatus.CONNECTED

    @property
    def unique_id(self):
        """Return a unique ID."""
        return f"{self.controller.connection.address}_{self._suffix}"

    @property
    def name(self):
        """Return the name of the thermostat, if any."""
        base_name = (
            self.controller.connection.name
            if self.controller.connection.name is not None
            else self.controller.connection.address
        )
        return f"{base_name} {self._name}"

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
    def device_info(self):
        """Return device registry information shared with the climate entity."""
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

    async def async_update(self):
        """Retrieve latest state."""
        try:
            await self.controller.temperatures.query()
        except ConnectionAbortedError:
            pass
        except ConnectionException:
            pass

    @property
    def native_value(self):
        """Return the sensor value."""
        raise NotImplementedError


class MadokaIndoorSensor(MadokaSensor):
    """Indoor temperature sensor."""

    def __init__(self, controller: Controller) -> None:
        super().__init__(controller, "indoor_temperature", "Indoor Temperature")

    @property
    def native_value(self):
        """Return the indoor temperature."""
        if self.controller.temperatures.status is None:
            return None
        return self.controller.temperatures.status.indoor


class MadokaOutdoorSensor(MadokaSensor):
    """Outdoor temperature sensor."""

    def __init__(self, controller: Controller) -> None:
        super().__init__(controller, "outdoor_temperature", "Outdoor Temperature")

    @property
    def native_value(self):
        """Return the outdoor temperature."""
        if self.controller.temperatures.status is None:
            return None
        return self.controller.temperatures.status.outdoor
