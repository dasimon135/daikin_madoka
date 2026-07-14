"""Data update coordinator for Daikin Madoka thermostats."""

from datetime import timedelta
import logging

from pymadoka import ConnectionException, Controller

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class MadokaCoordinator(DataUpdateCoordinator[dict]):
    """Poll one BRC1H controller and share the result with all its entities."""

    def __init__(
        self, hass: HomeAssistant, controller: Controller, scan_interval: int
    ) -> None:
        """Initialize the coordinator."""
        self.controller = controller
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{controller.connection.address}",
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> dict:
        """Query all device features in one BLE session."""
        try:
            await self.controller.update()
        except (ConnectionAbortedError, ConnectionException) as err:
            raise UpdateFailed(
                f"Could not update {self.controller.connection.address}: {err}"
            ) from err
        return self.controller.refresh_status()

    @property
    def address(self) -> str:
        """Return the MAC address of the device."""
        return self.controller.connection.address

    @property
    def device_name(self) -> str:
        """Return the display name of the device."""
        return self.controller.connection.name or self.address

    @property
    def device_info(self) -> DeviceInfo:
        """Return shared device registry information."""
        info = self.controller.info or {}
        model = info.get("Model Number String")
        return DeviceInfo(
            identifiers={(DOMAIN, self.address)},
            name=self.device_name,
            manufacturer="DAIKIN",
            model=f"BRC1H{model}" if model else "BRC1H",
            sw_version=info.get("Software Revision String"),
            hw_version=info.get("Hardware Revision String"),
        )
