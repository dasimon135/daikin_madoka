"""Data update coordinator for Daikin Madoka thermostats."""

import asyncio
from datetime import timedelta
import logging

from pymadoka import ConnectionException, Controller
from pymadoka.connection import ConnectionStatus

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import BRC1H_NAME_PREFIX, CONNECT_TIMEOUT, DOMAIN

_LOGGER = logging.getLogger(__name__)


class MadokaCoordinator(DataUpdateCoordinator[dict]):
    """Poll one BRC1H controller and share the result with all its entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        controller: Controller,
        scan_interval: int,
        friendly_name: str | None = None,
    ) -> None:
        """Initialize the coordinator."""
        self.controller = controller
        # The BLE stack overwrites controller.connection.name with the
        # advertised local name ("Daikin"), so keep the user's chosen name here.
        self._friendly_name = friendly_name
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{controller.connection.address}",
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> dict:
        """Query all device features in one BLE session.

        A poll is also a recovery attempt: if the connection dropped (or the
        library aborted it after an unexpected error), try to re-establish it
        before querying, so entities recover without a manual reload.
        """
        if self.controller.connection.connection_status is not ConnectionStatus.CONNECTED:
            try:
                await asyncio.wait_for(self.controller.start(), timeout=CONNECT_TIMEOUT)
            except Exception as err:  # noqa: BLE001
                raise UpdateFailed(f"Could not reconnect to {self.address}: {err}") from err
            if (
                self.controller.connection.connection_status
                is not ConnectionStatus.CONNECTED
            ):
                raise UpdateFailed(f"Device {self.address} is not reachable")

        try:
            await self.controller.update()
        except (ConnectionAbortedError, ConnectionException) as err:
            raise UpdateFailed(f"Could not update {self.address}: {err}") from err

        # Snapshot (per-feature dict copies) so coordinator.data is not a live
        # view of controller state.
        status = {
            feature: dict(feature_status)
            for feature, feature_status in self.controller.refresh_status().items()
        }

        # pymadoka's Controller.update() swallows per-feature query timeouts, so
        # a connected-but-unresponsive device (e.g. an authenticated link that
        # never completes a GATT exchange) would otherwise look like a
        # successful poll of empty data. Treat "no feature answered" as failure
        # so the entry stays not-ready instead of exposing phantom entities.
        if not status:
            raise UpdateFailed(f"Device {self.address} did not answer any query")

        return status

    @property
    def address(self) -> str:
        """Return the MAC address of the device."""
        return self.controller.connection.address

    @property
    def device_name(self) -> str:
        """Return the display name of the device."""
        return self._friendly_name or self.controller.connection.name or self.address

    @property
    def device_info(self) -> DeviceInfo:
        """Return shared device registry information."""
        info = self.controller.info or {}
        model = info.get("Model Number String")
        return DeviceInfo(
            identifiers={(DOMAIN, self.address)},
            name=self.device_name,
            manufacturer="DAIKIN",
            model=f"{BRC1H_NAME_PREFIX}{model}" if model else BRC1H_NAME_PREFIX,
            sw_version=info.get("Software Revision String"),
            hw_version=info.get("Hardware Revision String"),
        )
