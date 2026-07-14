"""Base entity for Daikin Madoka."""

from pymadoka.connection import ConnectionStatus

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import MadokaCoordinator


class MadokaEntity(CoordinatorEntity[MadokaCoordinator]):
    """Common base: coordinator-backed, device-grouped, stable unique_id."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MadokaCoordinator, suffix: str | None = None) -> None:
        """Initialize the entity; suffix=None claims the device unique_id (climate)."""
        super().__init__(coordinator)
        address = coordinator.address
        self._attr_unique_id = address if suffix is None else f"{address}_{suffix}"

    @property
    def controller(self):
        """Return the pymadoka controller."""
        return self.coordinator.controller

    @property
    def available(self) -> bool:
        """Entity is available when polling works and the BLE link is up."""
        return (
            super().available
            and self.controller.connection.connection_status
            is ConnectionStatus.CONNECTED
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        return self.coordinator.device_info
