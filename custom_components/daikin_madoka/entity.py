"""Base entity for Daikin Madoka."""

from collections.abc import Awaitable, Callable
from typing import Any

from pymadoka import ConnectionException, Controller

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
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
    def controller(self) -> Controller:
        """Return the pymadoka controller."""
        return self.coordinator.controller

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        return self.coordinator.device_info

    async def _async_execute(
        self, action: str, *calls: Callable[[], Awaitable[Any]]
    ) -> None:
        """Run device write commands, surface failures, refresh shared state.

        Each call is a zero-arg callable returning an awaitable, so a failure
        in an earlier command never leaves an unawaited coroutine behind.
        """
        try:
            for call in calls:
                await call()
        except (ConnectionAbortedError, ConnectionException, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={
                    "action": action,
                    "device": self.coordinator.device_name,
                },
            ) from err
        # Refresh now and again shortly after, so the UI reflects the device
        # applying the command without waiting a full poll interval.
        await self.coordinator.async_boost()
