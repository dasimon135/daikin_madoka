"""Support for Daikin Madoka binary sensors."""

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DEVICE_TYPE, DEFAULT_DEVICE_TYPE, DEVICE_TYPE_VENTILATION
from .coordinator import MadokaConfigEntry, MadokaCoordinator
from .entity import MadokaEntity


@dataclass(frozen=True, kw_only=True)
class MadokaUnitStatusDescription(BinarySensorEntityDescription):
    """A flag of pymadoka's UnitStatus (function 0x0100)."""

    attribute: str


# Defrost drives automations (underfloor heating while a ducted unit blows no
# heat, #159), so it is a primary entity. The three conditions are diagnostic
# and off by default: the error code sensor already says when one is raised.
UNIT_STATUS_SENSORS: tuple[MadokaUnitStatusDescription, ...] = (
    MadokaUnitStatusDescription(
        key="defrost",
        translation_key="defrost",
        attribute="defrost",
        device_class=BinarySensorDeviceClass.RUNNING,
    ),
    MadokaUnitStatusDescription(
        key="unit_error",
        translation_key="unit_error",
        attribute="error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    MadokaUnitStatusDescription(
        key="unit_warning",
        translation_key="unit_warning",
        attribute="warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    MadokaUnitStatusDescription(
        key="unit_alarm",
        translation_key="unit_alarm",
        attribute="alarm",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MadokaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Daikin Madoka binary sensors based on config_entry."""
    # pymadoka does not ask a VAM for the unit status (nobody has seen one
    # answer it), so these would sit at unknown for good.
    is_ventilation = (
        entry.data.get(CONF_DEVICE_TYPE, DEFAULT_DEVICE_TYPE) == DEVICE_TYPE_VENTILATION
    )
    entities: list[BinarySensorEntity] = []
    for coordinator in entry.runtime_data.values():
        entities.append(MadokaFilterBinarySensor(coordinator))
        if not is_ventilation:
            entities.extend(
                MadokaUnitStatusBinarySensor(coordinator, description)
                for description in UNIT_STATUS_SENSORS
            )
    async_add_entities(entities)


class MadokaFilterBinarySensor(MadokaEntity, BinarySensorEntity):
    """Binary sensor for the clean filter indicator."""

    _attr_translation_key = "clean_filter"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "clean_filter")

    @property
    def is_on(self) -> bool | None:
        """Return True when the filter needs cleaning."""
        if self.controller.clean_filter_indicator.status is None:
            return None
        return self.controller.clean_filter_indicator.status.clean_filter_indicator


class MadokaUnitStatusBinarySensor(MadokaEntity, BinarySensorEntity):
    """Defrost / hot start, or an error condition, of the indoor unit."""

    entity_description: MadokaUnitStatusDescription

    def __init__(
        self,
        coordinator: MadokaCoordinator,
        description: MadokaUnitStatusDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the flag, or None when the thermostat has not reported it."""
        status = self.controller.unit_status.status
        if status is None:
            return None
        value: bool | None = getattr(status, self.entity_description.attribute)
        return value
