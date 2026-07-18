"""Support for Daikin Madoka numbers."""

from pymadoka.features.eye_brightness import EyeBrightnessStatus

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MadokaConfigEntry, MadokaCoordinator
from .entity import MadokaEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MadokaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Daikin Madoka numbers based on config_entry."""
    async_add_entities(
        MadokaEyeBrightnessNumber(coordinator)
        for coordinator in entry.runtime_data.values()
    )


class MadokaEyeBrightnessNumber(MadokaEntity, NumberEntity):
    """Number to control the controller eye (LED) brightness."""

    _attr_translation_key = "eye_brightness"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 19
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "eye_brightness")

    @property
    def native_value(self) -> int | None:
        """Return the current LED brightness."""
        if self.controller.eye_brightness.status is None:
            return None
        return self.controller.eye_brightness.status.brightness

    async def async_set_native_value(self, value: float) -> None:
        """Set the LED brightness on the device."""
        await self._async_execute(
            "set eye brightness",
            lambda: self.controller.eye_brightness.update(EyeBrightnessStatus(int(value))),
        )
