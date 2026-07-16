"""Data update coordinator for Daikin Madoka thermostats."""

import asyncio
from datetime import timedelta
import logging

from pymadoka import ConnectionException, Controller
from pymadoka.connection import ConnectionStatus

from homeassistant.components import bluetooth
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import BRC1H_NAME_PREFIX, CONNECT_TIMEOUT, DOMAIN, POLL_TIMEOUT

_LOGGER = logging.getLogger(__name__)

# Consecutive failed polls before we raise a user-facing repair issue.
UNREACHABLE_THRESHOLD = 5
# Follow-up refresh delay after a command, to catch the device applying it
# without waiting a whole poll interval.
BOOST_DELAY = 4
DOCS_URL = "https://github.com/dasimon135/daikin_madoka#requirements"


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
        self._fail_count = 0
        self._issue_active = False
        self._boost_unsub: CALLBACK_TYPE | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{controller.connection.address}",
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> dict:
        """Poll the device, tracking sustained failures for a repair issue."""
        try:
            data = await self._async_poll()
        except UpdateFailed:
            self._fail_count += 1
            if self._fail_count >= UNREACHABLE_THRESHOLD:
                self._raise_unreachable_issue()
            raise
        self._fail_count = 0
        self._clear_unreachable_issue()
        return data

    async def _async_poll(self) -> dict:
        """Query all device features in one BLE session.

        A poll is also a recovery attempt: if the connection dropped (or the
        library aborted it after an unexpected error), try to re-establish it
        before querying, so entities recover without a manual reload.
        """
        if self.controller.connection.connection_status is not ConnectionStatus.CONNECTED:
            # Fail fast when the device is not even advertising: HA's BLE
            # tracker already knows, so don't burn 30s of connect attempts
            # (and a proxy connection slot) every poll for an absent device.
            if not bluetooth.async_address_present(
                self.hass, self.address, connectable=True
            ):
                raise UpdateFailed(f"Device {self.address} is not advertising")
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
            async with asyncio.timeout(POLL_TIMEOUT):
                await self.controller.update()
        except (ConnectionAbortedError, ConnectionException) as err:
            raise UpdateFailed(f"Could not update {self.address}: {err}") from err
        except TimeoutError as err:
            raise UpdateFailed(
                f"Polling {self.address} exceeded {POLL_TIMEOUT}s"
            ) from err

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

    async def async_boost(self) -> None:
        """Refresh now and once more shortly after.

        Called after a write command so the UI reflects the device applying it
        (or a stale value snapping back) without waiting a whole poll interval.
        """
        await self.async_request_refresh()
        if self._boost_unsub is not None:
            self._boost_unsub()

        @callback
        def _followup(_now) -> None:
            self._boost_unsub = None
            self.hass.async_create_task(self.async_request_refresh())

        self._boost_unsub = async_call_later(self.hass, BOOST_DELAY, _followup)

    async def async_reconnect(self) -> None:
        """Force a fresh BLE connection, then refresh."""
        conn = self.controller.connection
        try:
            await self.controller.stop()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Reconnect: stop failed for %s", self.address, exc_info=True)
        # stop() -> cleanup() sets the library's _closing flag, which makes the
        # next start() bail out immediately; clear it so the coordinator's poll
        # can actually re-establish the link. Also clear _paired so the fresh
        # connection re-authenticates (the bond is per proxy).
        conn._closing = False
        conn._paired = False
        conn.connection_status = ConnectionStatus.DISCONNECTED
        # The BRC1H stops advertising while connected and takes a moment to
        # resume after a disconnect; refreshing instantly would fail fast with
        # "not advertising" and defer the reconnect to the next poll.
        await asyncio.sleep(3)
        await self.async_request_refresh()

    @callback
    def _raise_unreachable_issue(self) -> None:
        if self._issue_active:
            return
        self._issue_active = True
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"unreachable_{self.address}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="device_unreachable",
            translation_placeholders={"device": self.device_name},
            learn_more_url=DOCS_URL,
        )

    @callback
    def _clear_unreachable_issue(self) -> None:
        # Always delete (idempotent): an issue persisted across a restart must
        # be cleared on the first success even though _issue_active is False on
        # the fresh coordinator instance.
        self._issue_active = False
        ir.async_delete_issue(self.hass, DOMAIN, f"unreachable_{self.address}")

    @callback
    def async_shutdown_extras(self) -> None:
        """Cancel a pending boost and clear the repair issue on unload."""
        if self._boost_unsub is not None:
            self._boost_unsub()
            self._boost_unsub = None
        self._clear_unreachable_issue()

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
