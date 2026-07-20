"""Data update coordinator for Daikin Madoka thermostats."""

import asyncio
from datetime import datetime, timedelta
import logging

from pymadoka import ConnectionException, Controller, PairingRequiredError
from pymadoka.connection import ConnectionStatus

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    BRC1H_NAME_PREFIX,
    CONF_MAC,
    CONF_PREFERRED_SOURCE,
    CONNECT_TIMEOUT,
    DOMAIN,
    POLL_TIMEOUT,
    STALE_GRACE,
)

_LOGGER = logging.getLogger(__name__)

# Consecutive failed polls before we raise a user-facing repair issue.
UNREACHABLE_THRESHOLD = 5
# Follow-up refresh delay after a command, to catch the device applying it
# without waiting a whole poll interval.
BOOST_DELAY = 4
DOCS_URL = "https://github.com/dasimon135/daikin_madoka#requirements"

# Every connect attempt against an unbonded BRC1H re-initiates an SMP
# exchange, which puts a pairing prompt on its screen. Retrying that each poll
# jams the thermostat's Bluetooth stack until the user toggles it off and on,
# so a reported pairing refusal backs off: PAIRING_RETRY_BASE seconds,
# doubling, capped at PAIRING_RETRY_MAX. The cap matters as much as the
# backoff — a user standing at the thermostat still needs a prompt to confirm
# within a reasonable wait.
PAIRING_RETRY_BASE = 60
PAIRING_RETRY_MAX = 300

# One BLE connect at a time across every Madoka device. Several thermostats
# reconnecting at once (as they do after a restart) contend for the same
# ESPHome proxies, and the resulting delays push the pairing handshake of
# perfectly valid bonds past its timeout.
CONNECT_LOCK_KEY = f"{DOMAIN}_connect_lock"


def _async_connect_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Return the connect lock shared by every Madoka coordinator."""
    return hass.data.setdefault(CONNECT_LOCK_KEY, asyncio.Lock())

# Typed config entry: runtime_data maps each normalized MAC to its coordinator.
type MadokaConfigEntry = ConfigEntry[dict[str, "MadokaCoordinator"]]


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
        self._pairing_issue_active = False
        self._boost_unsub: CALLBACK_TYPE | None = None
        # Pairing backoff: current delay and the instant the next attempt is
        # allowed. _last_pairing_error is chained onto the skipped polls'
        # UpdateFailed so the stale-value grace keeps recognising them as a
        # pairing situation rather than an ordinary dropout.
        self._pairing_retry_delay = 0.0
        self._pairing_retry_at: datetime | None = None
        self._last_pairing_error: PairingRequiredError | None = None
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
        except UpdateFailed as err:
            self._fail_count += 1
            if self._fail_count >= UNREACHABLE_THRESHOLD:
                self._raise_unreachable_issue()
            # Stale-value grace: a short BLE micro-drop should not punch holes
            # in graphs or flip entities unavailable, so the first STALE_GRACE
            # failures serve the last good data (counter incremented above, so
            # compare with <=: failures 1..STALE_GRACE are masked, the next one
            # propagates). The counter keeps rising, so the unreachable
            # threshold fires on its normal schedule. A pairing refusal never
            # heals on its own and must surface immediately; the raise site
            # chains PairingRequiredError as __cause__, which we inspect here
            # instead of tracking a per-cycle flag that would need careful
            # resetting at the start of every poll.
            # last_update_success gate: only mask success->transient dips. A
            # generic failure right after a surfaced one (e.g. a pairing
            # refusal) must not briefly resurrect entities on stale data
            # while an ERROR repair is on screen.
            if (
                self.last_update_success
                and self._fail_count <= STALE_GRACE
                and self.data
                and not isinstance(err.__cause__, PairingRequiredError)
            ):
                _LOGGER.debug(
                    "Poll %d/%d failed for %s, serving stale data: %s",
                    self._fail_count,
                    STALE_GRACE,
                    self.address,
                    err,
                )
                # Not a real success: leave the fail counter and any active
                # repair issues untouched.
                return self.data
            raise
        self._fail_count = 0
        self._clear_issues()
        self._async_persist_preferred_source()
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
            if (
                self._pairing_retry_at is not None
                and dt_util.utcnow() < self._pairing_retry_at
            ):
                # Deliberately do NOT touch the device: re-initiating SMP now
                # would re-prompt the screen and keep its stack jammed.
                raise UpdateFailed(
                    f"{self.address} is waiting for pairing; next attempt at "
                    f"{self._pairing_retry_at.isoformat()}"
                ) from self._last_pairing_error
            try:
                # Serialized: a connect storm across devices is what pushes
                # valid bonds past their pairing timeout in the first place.
                async with _async_connect_lock(self.hass):
                    await asyncio.wait_for(
                        self.controller.start(), timeout=CONNECT_TIMEOUT
                    )
            except PairingRequiredError as err:
                self._note_pairing_failure(err)
                self._raise_pairing_issue(err)
                raise UpdateFailed(str(err)) from err
            except Exception as err:  # noqa: BLE001
                # DeviceUnreachableError (and a wait_for TimeoutError) lands
                # here on purpose: both feed the threshold-based
                # device_unreachable repair, not a dedicated issue.
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
        # Pressing reconnect is the user saying they just dealt with the
        # thermostat, so honour it now instead of sitting out the backoff.
        self._clear_pairing_backoff()
        # The BRC1H stops advertising while connected and takes a moment to
        # resume after a disconnect; refreshing instantly would fail fast with
        # "not advertising" and defer the reconnect to the next poll.
        await asyncio.sleep(3)
        await self.async_request_refresh()

    @callback
    def _async_persist_preferred_source(self) -> None:
        """Remember which proxy carried the successful session.

        The stored source primes build_candidates on the next (re)connect and
        survives restarts, so the connection returns to the bonded proxy
        instead of whichever proxy wins on RSSI. Skipped for legacy multi-MAC
        entries (no CONF_MAC): they share one entry, and a single
        preferred_source cannot be right for several thermostats. None means
        local adapter / unknown backend — nothing worth pinning.
        """
        source = self.controller.connection.connected_source
        if (
            not source
            or self.config_entry is None
            or CONF_MAC not in self.config_entry.data
            or self.config_entry.data.get(CONF_PREFERRED_SOURCE) == source
        ):
            return
        # async_update_entry fires the entry's update listener; ours only
        # re-applies the poll interval from options, so no side effects here.
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_PREFERRED_SOURCE: source},
        )

    @callback
    def _raise_unreachable_issue(self) -> None:
        # A sustained pairing refusal also trips the failure threshold; the
        # unreachable advice (power/range) would be wrong next to the
        # pairing_required ERROR, so keep that single repair on screen.
        if self._pairing_issue_active:
            return
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
    def _note_pairing_failure(self, err: PairingRequiredError) -> None:
        """Grow the backoff so the next attempt does not re-prompt at once."""
        self._last_pairing_error = err
        self._pairing_retry_delay = min(
            max(self._pairing_retry_delay * 2, PAIRING_RETRY_BASE),
            PAIRING_RETRY_MAX,
        )
        self._pairing_retry_at = dt_util.utcnow() + timedelta(
            seconds=self._pairing_retry_delay
        )

    @callback
    def _clear_pairing_backoff(self) -> None:
        """Allow the next poll to connect immediately."""
        self._pairing_retry_delay = 0.0
        self._pairing_retry_at = None
        self._last_pairing_error = None

    @callback
    def _raise_pairing_issue(self, err: PairingRequiredError) -> None:
        """Raise an immediate repair: an auth refusal never heals on its own."""
        # No idempotence guard: async_create_issue updates in place, and
        # re-creating keeps the {proxies} placeholder current when a later
        # refusal went through a different proxy set. The flag only feeds
        # the unreachable-repair suppression and _clear_issues.
        self._pairing_issue_active = True
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"pairing_required_{self.address}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="pairing_required",
            translation_placeholders={
                "device": self.device_name,
                "proxies": self._proxy_names(err.tried_sources),
            },
            learn_more_url=DOCS_URL,
        )

    def _proxy_names(self, sources: list[str | None]) -> str:
        """Resolve proxy source MACs to human-readable scanner names."""
        names = []
        for source in sources:
            if source is None:
                names.append("local adapter")
                continue
            scanner = bluetooth.async_scanner_by_source(self.hass, source)
            names.append(getattr(scanner, "name", None) or source)
        return ", ".join(dict.fromkeys(names)) or "unknown"

    @callback
    def _clear_issues(self) -> None:
        # Always delete (idempotent): an issue persisted across a restart must
        # be cleared on the first success even though the flags are False on
        # the fresh coordinator instance.
        self._issue_active = False
        self._pairing_issue_active = False
        self._clear_pairing_backoff()
        ir.async_delete_issue(self.hass, DOMAIN, f"unreachable_{self.address}")
        ir.async_delete_issue(self.hass, DOMAIN, f"pairing_required_{self.address}")

    @callback
    def async_shutdown_extras(self) -> None:
        """Cancel a pending boost and clear the repair issues on unload."""
        if self._boost_unsub is not None:
            self._boost_unsub()
            self._boost_unsub = None
        self._clear_issues()

    @property
    def fail_count(self) -> int:
        """Consecutive failed polls (reset to 0 by a successful poll)."""
        return self._fail_count

    @property
    def pairing_retry_delay(self) -> float:
        """Current pairing backoff in seconds (0 when not backing off)."""
        return self._pairing_retry_delay

    @property
    def unreachable_issue_active(self) -> bool:
        """True while this coordinator has a device_unreachable repair open."""
        return self._issue_active

    @property
    def pairing_issue_active(self) -> bool:
        """True while this coordinator has a pairing_required repair open."""
        return self._pairing_issue_active

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
