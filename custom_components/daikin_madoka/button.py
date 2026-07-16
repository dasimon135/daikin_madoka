"""Support for Daikin Madoka buttons."""

from pymadoka.features.clean_filter import ResetCleanFilterTimerStatus

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .const import COORDINATORS, DOMAIN
from .coordinator import MadokaCoordinator
from .entity import MadokaEntity


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Daikin Madoka buttons based on config_entry."""
    coordinators = hass.data[DOMAIN][entry.entry_id][COORDINATORS]
    entities = []
    for coordinator in coordinators.values():
        entities.append(MadokaResetFilterButton(coordinator))
        entities.append(MadokaReconnectButton(coordinator))
    async_add_entities(entities)


class MadokaResetFilterButton(MadokaEntity, ButtonEntity):
    """Button to reset the clean filter timer."""

    _attr_translation_key = "reset_filter"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "reset_filter")

    async def async_press(self) -> None:
        """Reset the filter timer on the device."""
        await self._async_execute(
            "reset the filter timer",
            lambda: self.controller.reset_clean_filter_timer.update(
                ResetCleanFilterTimerStatus()
            ),
        )


class MadokaReconnectButton(MadokaEntity, ButtonEntity):
    """Force a fresh Bluetooth connection to the thermostat."""

    _attr_translation_key = "reconnect"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "reconnect")

    @property
    def available(self) -> bool:
        """Always pressable — its whole purpose is to recover a dead link."""
        return True

    async def async_press(self) -> None:
        """Drop and re-establish the BLE connection, then refresh."""
        await self.coordinator.async_reconnect()
