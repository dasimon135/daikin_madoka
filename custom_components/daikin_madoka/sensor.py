"""Support for Daikin Madoka sensors."""

from homeassistant.components import bluetooth
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
)

from .const import COORDINATORS, DOMAIN
from .coordinator import MadokaCoordinator
from .entity import MadokaEntity


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin sensors based on config_entry."""
    coordinators = hass.data[DOMAIN][entry.entry_id][COORDINATORS]
    entities = []
    for coordinator in coordinators.values():
        entities.append(MadokaIndoorSensor(coordinator))
        entities.append(MadokaOutdoorSensor(coordinator))
        entities.append(MadokaRssiSensor(coordinator))
    async_add_entities(entities)


class MadokaTemperatureSensor(MadokaEntity, SensorEntity):
    """Base representation of a Madoka temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS


class MadokaIndoorSensor(MadokaTemperatureSensor):
    """Indoor temperature sensor."""

    _attr_translation_key = "indoor_temperature"

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "indoor_temperature")

    @property
    def native_value(self):
        """Return the indoor temperature."""
        if self.controller.temperatures.status is None:
            return None
        return self.controller.temperatures.status.indoor


class MadokaOutdoorSensor(MadokaTemperatureSensor):
    """Outdoor temperature sensor."""

    _attr_translation_key = "outdoor_temperature"

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "outdoor_temperature")

    @property
    def native_value(self):
        """Return the outdoor temperature."""
        if self.controller.temperatures.status is None:
            return None
        return self.controller.temperatures.status.outdoor


class MadokaRssiSensor(MadokaEntity, SensorEntity):
    """Bluetooth signal strength of the thermostat, from HA's BLE tracker."""

    _attr_translation_key = "rssi"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "rssi")

    @property
    def native_value(self):
        """Return the RSSI of the last advertisement seen by HA."""
        service_info = bluetooth.async_last_service_info(
            self.hass, self.coordinator.address, connectable=True
        )
        if service_info is None:
            return None
        return service_info.rssi
