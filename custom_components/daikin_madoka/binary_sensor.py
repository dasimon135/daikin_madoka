"""Support for Daikin Madoka binary sensors."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from .const import COORDINATORS, DOMAIN
from .coordinator import MadokaCoordinator
from .entity import MadokaEntity


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin Madoka binary sensors based on config_entry."""
    coordinators = hass.data[DOMAIN][entry.entry_id][COORDINATORS]
    async_add_entities(
        MadokaFilterBinarySensor(coordinator) for coordinator in coordinators.values()
    )


class MadokaFilterBinarySensor(MadokaEntity, BinarySensorEntity):
    """Binary sensor for the clean filter indicator."""

    _attr_translation_key = "clean_filter"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "clean_filter")

    @property
    def is_on(self):
        """Return True when the filter needs cleaning."""
        if self.controller.clean_filter_indicator.status is None:
            return None
        return self.controller.clean_filter_indicator.status.clean_filter_indicator
