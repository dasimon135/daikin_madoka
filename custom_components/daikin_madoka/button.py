"""Support for Daikin Madoka buttons."""

import logging

from pymadoka import ConnectionException
from pymadoka.features.clean_filter import ResetCleanFilterTimerStatus

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .const import COORDINATORS, DOMAIN
from .coordinator import MadokaCoordinator
from .entity import MadokaEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin Madoka buttons based on config_entry."""
    coordinators = hass.data[DOMAIN][entry.entry_id][COORDINATORS]
    async_add_entities(
        MadokaResetFilterButton(coordinator) for coordinator in coordinators.values()
    )


class MadokaResetFilterButton(MadokaEntity, ButtonEntity):
    """Button to reset the clean filter timer."""

    _attr_translation_key = "reset_filter"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "reset_filter")

    async def async_press(self) -> None:
        """Reset the filter timer on the device."""
        try:
            await self.controller.reset_clean_filter_timer.update(
                ResetCleanFilterTimerStatus()
            )
        except (ConnectionAbortedError, ConnectionException):
            _LOGGER.warning(
                "Could not reset filter timer on %s: connection not available",
                self.coordinator.device_name,
            )
        await self.coordinator.async_request_refresh()
