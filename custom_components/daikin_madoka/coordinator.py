"""Data update coordinator for Daikin Madoka thermostats."""

import asyncio
import logging
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from time import monotonic
from typing import Any

from pymadoka import ConnectionException, Controller, PairingRequiredError
from pymadoka.connection import ConnectionStatus
from pymadoka.feature import Feature, FeatureStatus

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    AUTH_CORROBORATION_WINDOW_S,
    AUTOMATIC_PAIR_TIMEOUT,
    BOND_EVICTION_FAILURES,
    BRC1H_NAME_PREFIX,
    CANDIDATE_CONNECT_OVERHEAD_S,
    CONF_BONDED_SOURCES,
    CONF_MAC,
    CONF_PAIRING_STATE,
    CONF_PREFERRED_SOURCE,
    CONNECT_TIMEOUT,
    DOMAIN,
    ENERGY_CONSUMPTION_COMMAND,
    ENERGY_PARAMETERS,
    ENERGY_PRIVILEGE_COMMAND,
    ENERGY_PRIVILEGE_PARAMETER,
    ENERGY_SCAN_INTERVAL,
    MIN_PAIR_TIMEOUT,
    PAIRING_CONNECT_TIMEOUT,
    PAIRING_WINDOW_TIMEOUT,
    PAIRING_WINDOW_TTL_S,
    POLL_TIMEOUT,
    ROUND_HEADROOM_S,
    STALE_GRACE,
    TIMEOUT_BACKOFF_INTERVAL_S,
)

try:  # pragma: no cover - exercised only against a future pymadoka
    from pymadoka.connection import PAIRING_TIMEOUT_ROUNDS
except ImportError:  # pragma: no cover - upstream rename guard
    PAIRING_TIMEOUT_ROUNDS = 3

_LOGGER = logging.getLogger(__name__)

# Consecutive failed polls before we raise a user-facing repair issue — and,
# since the cadence brake became verdict-independent, before we stop polling a
# hopeless device at full speed.
UNREACHABLE_THRESHOLD = 5
# Why the poll cadence is braked. The brake itself is one mechanism (see
# _async_slow_to_backoff_cadence); the reason is what the diagnostic sensor and
# the repair issues key off, and the two must not be confused: "every path timed
# out while pairing" is a story about a bond, "five polls failed" is a story
# about a device.
BACKOFF_PAIRING = "pairing_timeout"
BACKOFF_UNREACHABLE = "unreachable"
# Consecutive polls skipped for lock contention before we stop serving stale
# data. Three: a skip means another Madoka device held the connect lock for a
# whole connect budget, so three of them is minutes of a device we have not
# touched once. Below that, a genuinely busy startup (four thermostats
# reconnecting through the same proxies) would flip healthy entities
# unavailable for no reason.
MAX_CONSECUTIVE_SKIPS = 3
# Follow-up refresh delay after a command, to catch the device applying it
# without waiting a whole poll interval.
BOOST_DELAY = 4
DOCS_URL = "https://github.com/dasimon135/daikin_madoka#requirements"

# One BLE connect at a time across every Madoka device. Several thermostats
# reconnecting at once (as they do after a restart) contend for the same
# ESPHome proxies, and the resulting delays push the pairing handshake of
# perfectly valid bonds past its timeout.
CONNECT_LOCK_KEY = f"{DOMAIN}_connect_lock"
PAIRING_STATE_KEY = f"{DOMAIN}_pairing_state"


class MadokaEnergyStatus(FeatureStatus):
    """Energy counters reported by the Madoka in tenths of a kWh."""

    def __init__(self) -> None:
        for period in ENERGY_PARAMETERS:
            setattr(self, period, None)

    def get_values(self) -> dict[int, bytearray]:
        """Request every counter in a single response."""
        return {parameter: bytearray() for parameter in ENERGY_PARAMETERS.values()}

    def set_values(self, values: dict[int, bytearray]) -> None:
        """Decode each counter's total and optional period breakdown."""
        for period, parameter in ENERGY_PARAMETERS.items():
            raw = values.get(parameter)
            if raw is None or len(raw) < 4:
                continue
            setattr(
                self,
                period,
                tuple(
                    round(struct.unpack_from("<I", raw, offset)[0] / 10, 1)
                    for offset in range(0, len(raw) - len(raw) % 4, 4)
                ),
            )


class MadokaEnergyConsumption(Feature):
    """Read the Madoka's internal energy counters over the active connection."""

    def __init__(self, connection) -> None:
        super().__init__(connection)
        self._next_query = 0.0

    def query_cmd_id(self) -> int:
        return ENERGY_CONSUMPTION_COMMAND

    def update_cmd_id(self) -> int:
        raise NotImplementedError

    def new_status(self) -> FeatureStatus:
        return MadokaEnergyStatus()

    @property
    def cache_is_fresh(self) -> bool:
        """Return whether the last complete read is still fresh."""
        return self.status is not None and monotonic() < self._next_query

    async def query(self) -> FeatureStatus:
        """Enable energy access, then request every counter separately."""
        if self.cache_is_fresh:
            return self.status
        await self._send_command(
            ENERGY_PRIVILEGE_COMMAND,
            bytearray((ENERGY_PRIVILEGE_PARAMETER, 1, 1)),
        )
        status = MadokaEnergyStatus()
        for parameter in ENERGY_PARAMETERS.values():
            response = await self._send_command(
                ENERGY_CONSUMPTION_COMMAND, bytearray((parameter, 0))
            )
            values = self._parse_energy_values(bytearray(response))
            raw = values.get(parameter)
            if raw is None or len(raw) < 4:
                raise ValueError(f"Energy response omitted parameter {parameter:#x}")
            status.set_values({parameter: raw})
        self.status = status
        self._next_query = monotonic() + ENERGY_SCAN_INTERVAL
        return status

    @staticmethod
    def _parse_energy_values(response: bytearray) -> dict[int, bytearray]:
        """Decode energy parameters without trusting the inaccurate size byte."""
        if len(response) < 6:
            raise ValueError("Energy response is too short")
        values: dict[int, bytearray] = {}
        index = 4
        while index < len(response):
            if index + 1 >= len(response):
                raise ValueError("Energy response has a truncated parameter header")
            parameter = response[index]
            size = 0 if response[index + 1] == 0xFF else response[index + 1]
            end = index + 2 + size
            if end > len(response):
                # The thermostat sometimes advertises the complete period
                # breakdown but omits its trailing, not-yet-populated slots.
                # Keep the available prefix; callers still require the full
                # four-byte total before accepting this response.
                values[parameter] = response[index + 2 :]
                break
            values[parameter] = response[index + 2 : end]
            index = end
        return values


def _async_connect_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Return the connect lock shared by every Madoka coordinator."""
    return hass.data.setdefault(CONNECT_LOCK_KEY, asyncio.Lock())


class _PollSkipped(Exception):
    """This poll never touched the device, so it proves nothing about it.

    Raised when the shared connect lock could not be taken in time. Deliberately
    NOT an UpdateFailed: a skipped cycle must not increment the failure counter,
    must not extend the timeout streak, must not close a pairing window and must
    not flip healthy entities unavailable. It is queue congestion between our own
    coordinators, not a statement about the thermostat.
    """


def _describe(err: BaseException) -> str:
    """Render an exception for a user-visible message.

    str(TimeoutError()) is the empty string, and several BLE errors raise with
    no message at all, which produced log lines ending in a bare "…: ". The
    class name is never empty and is exactly the missing information.
    """
    return str(err) or type(err).__name__


def connection_profile(
    pairing_window: bool, n_candidates: int = 1
) -> tuple[float, float]:
    """Return (inner pair budget, outer connect budget) for one attempt.

    The profile follows from who initiated the attempt: an automatic reconnect
    needs no human (a bonded path re-encrypts on its own), a user-opened
    pairing window does. The published budgets are CEILINGS; what comes back is
    shaped so a full pymadoka round fits inside the outer budget:

        n_candidates * (CANDIDATE_CONNECT_OVERHEAD_S + pair) + headroom <= outer

    That is the real invariant (see const.py). pymadoka only increments its
    pairing-round counter after walking EVERY candidate, so with the ceilings
    applied per attempt a house with two or more proxies per thermostat never
    completed a single round: no verdict, no backoff, and a dead bond polling
    at full cadence forever.

    Two knobs, in this order:
      * shrink the pair budget to the per-candidate share, but never below
        MIN_PAIR_TIMEOUT — under that floor a slow-but-valid bond starts
        failing again, which is the regression v3.7.1 existed to fix;
      * when the floor binds, stretch the OUTER budget instead. A longer
        attempt is affordable (the cadence brake keeps a stalling device to one
        attempt per TIMEOUT_BACKOFF_INTERVAL_S); an attempt that can never
        conclude anything is not.
    """
    if pairing_window:
        pair_ceiling, outer = PAIRING_WINDOW_TIMEOUT, PAIRING_CONNECT_TIMEOUT
    else:
        pair_ceiling, outer = AUTOMATIC_PAIR_TIMEOUT, CONNECT_TIMEOUT
    # A count of 0 (no path at all) behaves like 1: the library reports
    # DeviceUnreachableError before any budget matters.
    paths = max(1, n_candidates)
    share = (outer - ROUND_HEADROOM_S) / paths - CANDIDATE_CONNECT_OVERHEAD_S
    pair = max(MIN_PAIR_TIMEOUT, min(pair_ceiling, share))
    outer = max(outer, paths * (CANDIDATE_CONNECT_OVERHEAD_S + pair) + ROUND_HEADROOM_S)
    return pair, outer


@dataclass
class MadokaPairingState:
    """Per-device pairing state that must outlive a config entry retry.

    A failed setup raises ConfigEntryNotReady, and HA then re-runs
    async_setup_entry with a brand-new Controller, Connection and coordinator.
    Anything held on those objects resets on every retry, so it never
    accumulates and the device gets hammered forever — which is exactly how a
    single pairing problem turned into a storm. Keyed by MAC in hass.data,
    this survives.
    """

    last_error: PairingRequiredError | None = None
    # True while the user has deliberately asked to pair: unbonded proxies are
    # reachable and the pairing budget is widened for human confirmation.
    pairing_window: bool = False
    # Continuation of the library's all-paths-timed-out streak across
    # Connection rebuilds. The 3-round threshold behind PairingRequiredError
    # is unreachable without it: CONNECT_TIMEOUT cuts a setup attempt after
    # ~2 rounds, and the rebuilt Connection would restart the count at zero
    # (field incident 2026-07-21: an endless prompt salvo every 600s).
    # Reset whenever a verdict is concluded, mirroring the library, which
    # zeroes its own counter at both raise sites: a spent streak must not be
    # re-injected into the next Connection.
    timeout_rounds: int = 0
    # True once a pairing refusal has been PROVEN (a path explicitly rejected
    # the bond). Automatic reconnects then stop touching the device entirely —
    # every new attempt re-initiates SMP, which prompts a screen nobody is
    # watching and, repeated, jams the BRC1H's Bluetooth stack. Lifted only by
    # a deliberate user pairing action (the reconnect button) or a successful
    # session. A mere timeout streak must never set this: on a bonded path a
    # timeout means congestion, and convicting on it falsely locks out a
    # perfectly healthy thermostat (field incident 2026-07-26).
    suspended: bool = False
    # True while the device's poll cadence is braked to
    # TIMEOUT_BACKOFF_INTERVAL_S. Not a conviction — the device is still probed,
    # just far more slowly. Survives the coordinator rebuild for the same reason
    # timeout_rounds does.
    backoff: bool = False
    # WHY the brake is on: BACKOFF_PAIRING (the library concluded every path
    # timed out while pairing) or BACKOFF_UNREACHABLE (UNREACHABLE_THRESHOLD
    # polls failed in a row, whatever the cause). The brake must not depend on
    # a verdict ever forming — that side channel needs a full pymadoka round to
    # complete and silently stopped working once, leaving a dead bond polling
    # forever — but the two reasons tell opposite stories to the user, so they
    # are kept apart here rather than collapsed into the flag.
    backoff_reason: str | None = None
    # Consecutive failed polls. Shared, not per-coordinator: HA builds a fresh
    # coordinator on every setup retry and each one performs exactly one
    # refresh, so an instance attribute could never reach UNREACHABLE_THRESHOLD
    # and the device_unreachable repair could never fire — two dead thermostats
    # produced zero notifications (field incident 2026-07-26).
    fail_count: int = 0
    # Consecutive polls that never reached the device because the shared connect
    # lock stayed busy. Deliberately NOT persisted: it is a statement about
    # contention between our own coordinators right now, worthless after a
    # restart. Shared for the same reason fail_count is — a rebuilt coordinator
    # would restart it at zero and the ceiling would never be reached.
    skipped_polls: int = 0
    # Consecutive PROVEN pairing refusals per proxy source. Lives here, not on
    # the coordinator, for the same reason as everything above: the counter has
    # to survive the rebuild a setup retry performs, or a bond can never
    # accumulate enough evidence to be evicted. Cleared for a source the moment
    # that source authenticates again.
    auth_failures: dict[str, int] = field(default_factory=dict)
    # monotonic() at the last fully successful poll, or None. The acquittal a
    # "rejected" verdict is checked against (see AUTH_CORROBORATION_WINDOW_S),
    # and CONSUMED when it is spent, so one good session excuses exactly one
    # refusal: a bond that really died still surfaces on the next round rather
    # than being forgiven for as long as the window happens to stay open.
    #
    # Deliberately absent from as_stored(): monotonic timestamps are
    # process-local, so a restored one would be meaningless arithmetic against
    # a fresh process's clock — and restoring an acquittal would hand the new
    # process a free pass no session in it ever earned.
    last_success: float | None = None

    def as_stored(self) -> dict[str, Any] | None:
        """Serialize the durable part of the verdict, or None if it is empty.

        pairing_window is deliberately absent: a window is permission for one
        deliberate attempt by a user who is standing at the thermostat, so it
        must never be restored by a restart. An all-default verdict serializes
        to None so a healthy device stores nothing at all.
        """
        stored: dict[str, Any] = {}
        if self.suspended:
            stored["suspended"] = True
        if self.backoff:
            stored["backoff"] = True
            if self.backoff_reason:
                stored["backoff_reason"] = self.backoff_reason
        if self.timeout_rounds:
            stored["timeout_rounds"] = self.timeout_rounds
        if self.fail_count:
            # Clamped: past the threshold a larger number changes nothing, and
            # the clamp is what stops a permanently dead device from rewriting
            # the config entry on every single poll.
            stored["fail_count"] = min(self.fail_count, UNREACHABLE_THRESHOLD)
        if self.auth_failures:
            stored["auth_failures"] = dict(self.auth_failures)
        if self.last_error is not None:
            stored["last_error"] = {
                "tried_sources": list(self.last_error.tried_sources)
            }
        return stored or None

    @classmethod
    def from_stored(
        cls, address: str, stored: Mapping[str, Any]
    ) -> "MadokaPairingState":
        """Rebuild a verdict persisted by as_stored()."""

        def _count(key: str) -> int:
            value = stored.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else 0

        last_error = None
        raw_error = stored.get("last_error")
        if isinstance(raw_error, Mapping):
            sources = raw_error.get("tried_sources")
            last_error = PairingRequiredError(
                address, list(sources) if isinstance(sources, list) else []
            )
        raw_failures = stored.get("auth_failures")
        auth_failures = {}
        if isinstance(raw_failures, Mapping):
            auth_failures = {
                str(source): count
                for source, count in raw_failures.items()
                if isinstance(count, int) and not isinstance(count, bool) and count > 0
            }
        return cls(
            last_error=last_error,
            timeout_rounds=_count("timeout_rounds"),
            suspended=bool(stored.get("suspended")),
            backoff=bool(stored.get("backoff")),
            backoff_reason=(
                reason
                if isinstance(reason := stored.get("backoff_reason"), str)
                else None
            ),
            fail_count=_count("fail_count"),
            auth_failures=auth_failures,
        )


def async_pairing_state(hass: HomeAssistant, address: str) -> MadokaPairingState:
    """Return (creating if needed) the shared pairing state for a device."""
    store: dict[str, MadokaPairingState] = hass.data.setdefault(
        PAIRING_STATE_KEY, {}
    )
    return store.setdefault(address, MadokaPairingState())


@callback
def async_restore_pairing_state(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rehydrate the verdicts this entry persisted, once per MAC.

    Without this an HA restart forgets everything the integration concluded
    about a bond: the quarantine silently disarms itself and the whole
    diagnosis has to be re-derived from scratch, at poll cadence, while the
    thermostat is prompted again. A MAC that already has a live state is left
    alone — memory always wins, so a reload can never resurrect a quarantine
    that a successful connect cleared just before it.
    """
    stored = entry.data.get(CONF_PAIRING_STATE)
    if not isinstance(stored, Mapping):
        return
    store: dict[str, MadokaPairingState] = hass.data.setdefault(PAIRING_STATE_KEY, {})
    for address, raw in stored.items():
        if address in store or not isinstance(raw, Mapping):
            continue
        store[address] = MadokaPairingState.from_stored(address, raw)


@callback
def async_forget_pairing_state(
    hass: HomeAssistant, entry: ConfigEntry, address: str, *, persisted: bool = True
) -> None:
    """Drop a device's verdict from memory, and optionally from the entry.

    persisted=False is the unload case: the entry keeps its durable shadow (the
    next setup rehydrates it) but the live copy goes, so an entry that is
    deleted and re-added starts from a clean slate — the user's last-resort
    escape hatch out of a wrong quarantine.
    """
    store: dict[str, MadokaPairingState] = hass.data.get(PAIRING_STATE_KEY) or {}
    store.pop(address, None)
    if not persisted:
        return
    saved = entry.data.get(CONF_PAIRING_STATE)
    if not isinstance(saved, Mapping) or address not in saved:
        return
    remaining = {key: value for key, value in saved.items() if key != address}
    data = {**entry.data}
    if remaining:
        data[CONF_PAIRING_STATE] = remaining
    else:
        data.pop(CONF_PAIRING_STATE, None)
    hass.config_entries.async_update_entry(entry, data=data)

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
        # Make energy another controller feature so Controller.update() queries
        # it on the authenticated BLE session it already owns.
        controller.energy_consumption = MadokaEnergyConsumption(controller.connection)
        # The BLE stack overwrites controller.connection.name with the
        # advertised local name ("Daikin"), so keep the user's chosen name here.
        self._friendly_name = friendly_name
        self._issue_active = False
        self._pairing_issue_active = False
        self._pairing_slow_issue_active = False
        self._boost_unsub: CALLBACK_TYPE | None = None
        # Cancel handle of the pairing window's time-to-live timer, and the
        # poll interval to go back to once a timeout backoff ends.
        self._pairing_window_unsub: CALLBACK_TYPE | None = None
        self._normal_interval: timedelta | None = None
        # Pairing suspension and window live in hass.data keyed by MAC, so
        # they survive the Controller/coordinator being rebuilt on a config
        # entry retry. last_error is chained onto the skipped polls'
        # UpdateFailed so the stale-value grace keeps recognising them as a
        # pairing situation rather than an ordinary dropout.
        self._pairing = async_pairing_state(hass, controller.connection.address)
        # Resume the all-paths-timed-out streak on the freshly built
        # Connection. pymadoka >= 0.3.10 exposes a supported setter for this;
        # before that the read-only property left no option but the private
        # attribute (async_reconnect already pokes sibling privates).
        resume = getattr(controller.connection, "resume_pairing_timeout_rounds", None)
        if callable(resume):
            resume(self._pairing.timeout_rounds)
        else:
            controller.connection._pairing_timeout_rounds = self._pairing.timeout_rounds
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{controller.connection.address}",
            update_interval=timedelta(seconds=scan_interval),
        )
        # The pairing budget has exactly one owner (see connection_profile):
        # apply it up front so the controller never runs on whatever default
        # it happened to be built with.
        self._async_apply_pair_budget()
        if self._pairing.backoff:
            # A rebuild must not hand a stalling device the fast cadence back;
            # the state is per-MAC precisely so it outlives this object.
            self._normal_interval = timedelta(seconds=scan_interval)
            self.update_interval = timedelta(seconds=TIMEOUT_BACKOFF_INTERVAL_S)
        if self._pairing.pairing_window:
            # Same reason: a window whose TTL timer died with the previous
            # coordinator would otherwise stay open forever.
            self._async_arm_pairing_window_timer()

    async def _async_update_data(self) -> dict:
        """Poll the device, persisting whatever the attempt established."""
        if self._pairing.backoff:
            # Re-arm before anything else can lose it. update_interval is public
            # and gets rewritten from outside — notably by the entry update
            # listener, which fires on every async_update_entry including the
            # ones _async_persist_pairing_state performs. A brake that only
            # _async_enter_timeout_backoff could re-apply was silently dropped
            # by the first failure that carried no pairing verdict.
            self._async_slow_to_backoff_cadence()
        try:
            return await self._async_update_data_inner()
        finally:
            # Every branch below mutates the shared verdict (failure counter,
            # suspension, backoff), and every mutation must reach the durable
            # copy — including the clearing a success performs. A persisted
            # quarantine that a later success could not erase would be worse
            # than no persistence at all.
            self._async_persist_pairing_state()

    async def _async_update_data_inner(self) -> dict:
        """Poll the device, tracking sustained failures for a repair issue."""
        try:
            data = await self._async_poll()
        except _PollSkipped as err:
            # A cycle that never reached the device. Serve the last good data if
            # there is any (the device is not known to be in trouble, so its
            # entities must not blink), otherwise report a plain failure — but
            # in NEITHER case touch a counter, a streak or the pairing window.
            # Raised inside this except clause, the UpdateFailed below does not
            # re-enter the sibling clause, so nothing is accumulated.
            #
            # ...but "proves nothing" must not become "hides everything". Serving
            # the last good data on every skip forever kept last_update_success
            # True, so entities showed hours-old temperatures as current, with
            # nothing above DEBUG and no counter anywhere. Bounded here: after
            # MAX_CONSECUTIVE_SKIPS the device goes unavailable honestly, because
            # we genuinely do not know anything about it.
            self._pairing.skipped_polls += 1
            _LOGGER.debug("Skipping this poll of %s: %s", self.address, err)
            if self.data and self._pairing.skipped_polls < MAX_CONSECUTIVE_SKIPS:
                return self.data
            if self._pairing.skipped_polls >= MAX_CONSECUTIVE_SKIPS:
                _LOGGER.warning(
                    "%s has not been polled for %d cycles in a row (%s); its "
                    "entities go unavailable rather than keep showing stale "
                    "values",
                    self.address,
                    self._pairing.skipped_polls,
                    err,
                )
            raise UpdateFailed(str(err)) from None
        except UpdateFailed as err:
            # This cycle reached the device, so whatever the outcome, we are not
            # being starved by the connect lock.
            self._pairing.skipped_polls = 0
            self._pairing.fail_count += 1
            if self._pairing.fail_count >= UNREACHABLE_THRESHOLD:
                self._raise_unreachable_issue()
                # Verdict-INDEPENDENT brake. Whether or not pymadoka ever
                # concluded anything (it needs a full candidate round to
                # complete, and for a whole release it never got one), a device
                # that has failed five polls in a row must stop being retried
                # every 60s: each attempt takes a proxy connection slot and
                # re-initiates SMP against a thermostat that answers none of it.
                self._async_slow_to_backoff_cadence(BACKOFF_UNREACHABLE)
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
                and self._pairing.fail_count <= STALE_GRACE
                and self.data
                and not isinstance(err.__cause__, PairingRequiredError)
            ):
                _LOGGER.debug(
                    "Poll %d/%d failed for %s, serving stale data: %s",
                    self._pairing.fail_count,
                    STALE_GRACE,
                    self.address,
                    err,
                )
                # Not a real success: leave the fail counter and any active
                # repair issues untouched.
                return self.data
            raise
        self._pairing.skipped_polls = 0
        self._pairing.fail_count = 0
        # The device answered over an authenticated link. Stamped here rather
        # than at connect time on purpose: this is the strongest statement
        # available — the bond held AND the GATT session worked — and it also
        # covers a poll over an already-open link, which never goes through the
        # connect path at all.
        self._pairing.last_success = monotonic()
        self._clear_issues()
        # Full clear only here: an unload (which also runs _clear_issues via
        # async_shutdown_extras) must NOT lift the suspension, or a simple
        # entry reload would grant a dead bond a fresh prompt salvo.
        self._clear_pairing_suspension()
        self._async_restore_normal_interval()
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
            if self._pairing.suspended and not self._pairing.pairing_window:
                # Deliberately do NOT touch the device: a concluded pairing
                # refusal never heals on its own, and re-initiating SMP would
                # re-prompt the screen and keep its stack jammed. The repair
                # is re-raised so a rebuilt coordinator (entry retry, reload)
                # keeps the story on screen — and the flag it sets keeps the
                # redundant device_unreachable repair suppressed.
                if not self._pairing_issue_active and self._pairing.last_error:
                    self._raise_pairing_issue(self._pairing.last_error)
                raise UpdateFailed(
                    f"{self.address} needs pairing; automatic reconnects are "
                    "suspended until the reconnect button is pressed"
                ) from self._pairing.last_error
            try:
                await self._async_connect()
            except UpdateFailed:
                # A pairing window is permission for ONE deliberate attempt.
                # That attempt just failed, so the window closes here: leaving
                # it open would keep unbonded proxies reachable by unattended
                # polls, keep the human-sized SMP budget on them, and keep the
                # dead-bond quarantine disarmed — the exact combination that
                # turns one failed Reconnect into a pairing storm.
                self._async_close_pairing_window()
                raise

        energy = self.controller.energy_consumption
        use_cached_energy = energy.cache_is_fresh
        if use_cached_energy:
            # Controller.update() counts every Feature.query() return as a device
            # answer. A cache hit performs no I/O, so do not let it make a fully
            # unresponsive device look healthy. Restore it before refresh_status
            # so the cached counters remain in the coordinator snapshot.
            self.controller.energy_consumption = None
        try:
            async with asyncio.timeout(POLL_TIMEOUT):
                await self.controller.update()
        except (ConnectionAbortedError, ConnectionException) as err:
            raise UpdateFailed(
                f"Could not update {self.address}: {_describe(err)}"
            ) from err
        except TimeoutError as err:
            raise UpdateFailed(
                f"Polling {self.address} exceeded {POLL_TIMEOUT}s"
            ) from err
        finally:
            if use_cached_energy:
                self.controller.energy_consumption = energy

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

    async def _async_connect(self) -> None:
        """Establish the BLE link under the profile this attempt earned.

        Everything about the attempt — which proxies are reachable, how long
        pairing may take, how long the whole connect may take — follows from
        one bit: is a user-initiated pairing window open? (The candidate
        filtering side of it lives in __init__._candidates.)
        """
        # Sized against the paths this attempt will actually walk: pymadoka
        # counts a pairing round only after the LAST candidate, so a budget that
        # fits one path and not N never lets it conclude anything.
        connect_budget = self._async_apply_pair_budget(self._async_candidate_count())
        # Serialized: a connect storm across devices is what pushes valid bonds
        # past their pairing timeout in the first place. But the lock is shared
        # by EVERY Madoka coordinator and is held across pymadoka's own retry
        # backoff (sleep 5->10->20->40->60 plus a fixed 2s, all inside start()),
        # so one device with a dead bond used to park it for a whole connect
        # budget while doing nothing — delaying healthy devices, congesting the
        # proxies and manufacturing the pair timeouts that convict them. That is
        # the cascade that turned one bad bond into several (field incident
        # 2026-07-26), and waiting was unbounded: N stuck devices stacked N
        # budgets serially before this coordinator's own timer even started.
        #
        # The sleeps live inside the library and cannot be moved out without an
        # upstream change, so the locked region cannot be shortened here: bound
        # the WAIT instead. Waiting longer than one full attempt of our own
        # profile means the queue ahead of us is already longer than the work we
        # came to do — give up this cycle and let the next poll retry. Combined
        # with the timeout backoff, a stalling device polls rarely and therefore
        # contends rarely.
        lock = _async_connect_lock(self.hass)
        try:
            async with asyncio.timeout(connect_budget):
                await lock.acquire()
        except TimeoutError:
            raise _PollSkipped(
                f"another Madoka device held the connect lock for "
                f"{connect_budget}s"
            ) from None
        try:
            await asyncio.wait_for(self.controller.start(), timeout=connect_budget)
        except PairingRequiredError as err:
            self._async_note_pairing_error(err)
            raise UpdateFailed(_describe(err)) from err
        except Exception as err:
            # DeviceUnreachableError (and a wait_for TimeoutError) lands
            # here on purpose: both feed the threshold-based
            # device_unreachable repair, not a dedicated issue.
            # Persist the all-paths-timed-out streak first: this is the
            # very failure mode where the attempt got cancelled before
            # the library could reach its own 3-round conclusion. The max
            # guard keeps a round-less failure (device off, streak-blind
            # future library) from erasing an accumulated streak.
            self._note_timeout_rounds()
            raise UpdateFailed(
                f"Could not reconnect to {self.address}: {_describe(err)}"
            ) from err
        finally:
            lock.release()
        if (
            self.controller.connection.connection_status
            is not ConnectionStatus.CONNECTED
        ):
            raise UpdateFailed(f"Device {self.address} is not reachable")
        # The link is up AND authenticated, which is the whole proof a bond
        # exists on this path — record it here rather than after a full poll.
        # A connect that authenticates but whose controller.update() then fails
        # used to record nothing at all, and on_disconnect clears
        # connected_source, so the evidence was simply lost.
        self._async_record_bonded_source()

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
        except Exception:
            _LOGGER.debug("Reconnect: stop failed for %s", self.address, exc_info=True)
        # stop() -> cleanup() sets the library's _closing flag, which makes the
        # next start() bail out immediately; clear it so the coordinator's poll
        # can actually re-establish the link. Also clear _paired so the fresh
        # connection re-authenticates (the bond is per proxy).
        conn._closing = False
        conn._paired = False
        conn.connection_status = ConnectionStatus.DISCONNECTED
        # Pressing reconnect means the user is at the thermostat and ready to
        # accept a pairing code: lift the suspension, reach proxies that hold
        # no bond yet, and widen the pairing budget so a human can actually
        # compare codes and confirm.
        self._clear_pairing_suspension()
        # And lift the retry brake, so the refresh below runs at the
        # configured cadence and — crucially — the NEXT one does too if this
        # attempt fails. Without it the poll triggered here re-armed the brake
        # on its way in and a failed reconnect was followed by 900s of
        # silence, which is what made the button look inert.
        self.async_clear_backoff()
        self._async_persist_pairing_state()
        self._async_open_pairing_window()
        # The BRC1H stops advertising while connected and takes a moment to
        # resume after a disconnect; refreshing instantly would fail fast with
        # "not advertising" and defer the reconnect to the next poll.
        await asyncio.sleep(3)
        await self.async_request_refresh()

    @callback
    def _async_persist_pairing_state(self) -> None:
        """Mirror the shared verdict into the config entry.

        hass.data keeps the verdict across a coordinator rebuild; only the
        entry keeps it across an HA restart. Written on every change, so the
        stored copy can never be more pessimistic than reality.
        """
        entry = self.config_entry
        # A coordinator built outside a real entry (or against one that was
        # removed mid-poll) has nothing to write to.
        if entry is None or self.hass.config_entries.async_get_entry(
            entry.entry_id
        ) is None:
            return
        saved = entry.data.get(CONF_PAIRING_STATE)
        saved = dict(saved) if isinstance(saved, Mapping) else {}
        updated = dict(saved)
        current = self._pairing.as_stored()
        if current is None:
            updated.pop(self.address, None)
        else:
            updated[self.address] = current
        if updated == saved:
            return
        data = {**entry.data}
        if updated:
            data[CONF_PAIRING_STATE] = updated
        else:
            data.pop(CONF_PAIRING_STATE, None)
        self.hass.config_entries.async_update_entry(entry, data=data)

    @callback
    def _async_persist_preferred_source(self) -> None:
        """Remember which proxy carried the successful session.

        The stored source primes build_candidates on the next (re)connect and
        survives restarts, so the connection returns to the bonded proxy
        instead of whichever proxy wins on RSSI. It is also appended to the
        bonded-source list: a completed authenticated session is the only
        proof that this proxy holds a bond, and automatic reconnects are
        restricted to that list so they never start a new pairing.

        Skipped for legacy multi-MAC entries (no CONF_MAC): they share one
        entry, and a single preferred_source cannot be right for several
        thermostats. None means local adapter / unknown backend — nothing
        worth pinning.
        """
        source = self.controller.connection.connected_source
        if (
            not source
            or self.config_entry is None
            or CONF_MAC not in self.config_entry.data
        ):
            return
        # A completed session is the strongest possible acquittal for this path,
        # and an eviction streak only means anything while it is CONSECUTIVE.
        # Cleared here as well as in _async_record_bonded_source because a poll
        # over an already-open link never goes through the connect path at all.
        self._pairing.auth_failures.pop(source, None)
        bonded = list(self.config_entry.data.get(CONF_BONDED_SOURCES, []))
        if source not in bonded:
            bonded.append(source)
        if (
            self.config_entry.data.get(CONF_PREFERRED_SOURCE) == source
            and self.config_entry.data.get(CONF_BONDED_SOURCES) == bonded
        ):
            return
        # async_update_entry fires the entry's update listener; ours only
        # re-applies the poll interval from options, so no side effects here.
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={
                **self.config_entry.data,
                CONF_PREFERRED_SOURCE: source,
                CONF_BONDED_SOURCES: bonded,
            },
        )

    @callback
    def _async_record_bonded_source(self) -> None:
        """Record the proxy that just authenticated as holding a bond.

        Called as soon as controller.start() returns CONNECTED, which is the
        moment the bond is proven — everything after it (the GATT poll) can fail
        for reasons that say nothing about pairing.

        connected_source is None on the library's fallback single-device path
        (it only ever sets it in the candidates loop), and that path is exactly
        the one that lets HA's scorer pick a proxy and pair with it
        unconditionally: recording it would launder an auto-pairing into a
        policy-approved bond. The None guard is deliberate, keep it.
        """
        source = self.controller.connection.connected_source
        if (
            not source
            or self.config_entry is None
            or CONF_MAC not in self.config_entry.data
        ):
            return
        # This path just authenticated, so whatever it was accused of before is
        # over: a streak has to be CONSECUTIVE to mean anything.
        self._pairing.auth_failures.pop(source, None)
        bonded = list(self.config_entry.data.get(CONF_BONDED_SOURCES, []))
        if source in bonded:
            return
        bonded.append(source)
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_BONDED_SOURCES: bonded},
        )

    @callback
    def _async_evict_dead_bond(self, err: PairingRequiredError) -> None:
        """Drop a proxy from the bonded list once it has proven it holds no bond.

        Only reached from the PROVEN-rejection branch: a timeout streak is an
        inference and must never cost a bond.

        Attribution is the hard part. Since 0.3.10 the library reports a
        per-path verdict in `err.evidence`, so every source it marks
        "rejected" is individually proven and can be charged — which is what
        makes eviction reachable at all in a home with three or four proxies,
        where a round is never single-path.

        Since 0.3.11 those sources are also REAL. Before it, they were the
        candidates the library offered to HA, which is not what HA connects
        through: habluetooth keeps only the address and re-picks a scanner by
        RSSI, so a refusal could be charged to a proxy that was never in the
        round (#53). An attempt whose path cannot be proven is now keyed under
        None and charges nobody.

        Without that verdict (pymadoka <= 0.3.9) tried_sources is a flat list:
        a round over three proxies cannot say WHICH of them rejected, so only
        a single-source round is unambiguous and anything else records
        nothing. Under-evicting merely costs a few futile retries on a dead
        path, while over-evicting deletes the one path that still works and
        needs a human at the thermostat to restore.
        """
        for source in self._attributable_refusals(err):
            self._async_charge_bond_refusal(source)

    @callback
    def _attributable_refusals(self, err: PairingRequiredError) -> list[str]:
        """The sources this error PROVES hold no bond, if any."""
        evidence = getattr(err, "evidence", None)
        if isinstance(evidence, Mapping) and evidence:
            # A mapping that exists is AUTHORITATIVE, including when it names
            # nobody. Since pymadoka-ng 0.3.11 an attempt whose path could not
            # be proven — no link was ever established, so nothing named the
            # scanner — is keyed under None, so "no proven source" is a
            # statement ("this round cannot say which proxy failed"), not
            # silence. Falling through to the legacy rule here would answer it
            # with a guess, and the guess is the whole defect: under HA the
            # candidate we offered is not the path that was used (#53).
            #
            # Only "rejected" counts: a per-path "timeout" is congestion until
            # proven otherwise, exactly as a whole-round timeout streak is.
            return [
                source
                for source, verdict in evidence.items()
                if verdict == "rejected" and source
            ]
        # No mapping at all: pymadoka <= 0.3.9, where tried_sources is a flat
        # list and only a single-source round is unambiguous.
        if len(err.tried_sources) != 1 or not err.tried_sources[0]:
            return []
        return [err.tried_sources[0]]

    @callback
    def _async_charge_bond_refusal(self, source: str) -> None:
        """Record one proven refusal against a proxy, evicting past threshold."""
        # Clamped: past the threshold a bigger number changes nothing, and the
        # clamp is what stops a device whose eviction is blocked (last bonded
        # path) from rewriting the config entry on every poll, forever.
        failures = min(
            self._pairing.auth_failures.get(source, 0) + 1, BOND_EVICTION_FAILURES
        )
        self._pairing.auth_failures[source] = failures
        if failures < BOND_EVICTION_FAILURES:
            return
        entry = self.config_entry
        if entry is None or CONF_MAC not in entry.data:
            return
        bonded = list(entry.data.get(CONF_BONDED_SOURCES, []))
        if source not in bonded:
            return
        if len(bonded) <= 1:
            # NEVER empty the list. build_candidates treats an empty
            # CONF_BONDED_SOURCES as "unrestricted", so evicting the last entry
            # would not protect the device — it would silently switch the
            # anti-storm policy off and let unattended polls pair with any proxy
            # in range. The recovery path for a device with no working bond is
            # the reauth flow (a human, deliberately), not an eviction.
            _LOGGER.warning(
                "%s: %s keeps refusing the bond, but it is the only proxy known "
                "to hold one — keeping it and leaving recovery to a re-pair",
                self.address,
                self._proxy_names([source]),
            )
            return
        bonded.remove(source)
        self._pairing.auth_failures.pop(source, None)
        _LOGGER.warning(
            "%s: dropping %s from the bonded proxies after %d refusals; "
            "reconnects will use the remaining %d",
            self.address,
            self._proxy_names([source]),
            BOND_EVICTION_FAILURES,
            len(bonded),
        )
        data = {**entry.data, CONF_BONDED_SOURCES: bonded}
        if data.get(CONF_PREFERRED_SOURCE) == source:
            # Leaving a sticky proxy that is no longer allowed would just make
            # the ordering key match nothing; drop it and let the next
            # successful session elect a new one.
            data.pop(CONF_PREFERRED_SOURCE, None)
        self.hass.config_entries.async_update_entry(entry, data=data)

    @callback
    def _raise_unreachable_issue(self) -> None:
        # A sustained pairing refusal also trips the failure threshold; the
        # unreachable advice (power/range) would be wrong next to the
        # pairing_required ERROR (or the pairing_slow WARNING, which tells the
        # opposite story), so keep that single repair on screen.
        if self._pairing_issue_active or self._pairing_slow_issue_active:
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
    def _async_candidate_count(self) -> int:
        """How many paths this connect will walk, per the library's own list.

        Read from the very callback pymadoka will call, so the count can never
        drift from the filtering in __init__._candidates (bonded-source
        restriction, pairing window, free-slot ordering). The callback is total
        by contract and only reads HA's scanner registry — no radio, no I/O — so
        calling it once more per connect is free.

        1 on anything unexpected: that is the old, unshaped behaviour, which is
        correct for the single-path case and merely conservative otherwise.
        """
        candidates_callback = getattr(
            self.controller.connection, "candidates_callback", None
        )
        if not callable(candidates_callback):
            return 1
        try:
            return len(list(candidates_callback()))
        except Exception:  # budget shaping must never break connecting
            _LOGGER.debug(
                "Could not count candidate paths for %s; shaping the pairing "
                "budget for a single path",
                self.address,
                exc_info=True,
            )
            return 1

    @callback
    def _async_apply_pair_budget(self, n_candidates: int = 1) -> float:
        """Set the pairing budget for the active profile; return the outer one.

        THE single writer of connection.pair_timeout. Every other path (open a
        window, close a window, connect) goes through here, so the budget can
        never be left at a value some earlier code path happened to set — the
        leak that kept a 60s human budget armed on unattended reconnects.

        n_candidates shapes both budgets so a full pymadoka round fits (see
        connection_profile). The default of 1 is for the callers that only need
        the profile applied, not sized: they run nowhere near a connect, and the
        connect path re-applies it with the real count.
        """
        pair_budget, connect_budget = connection_profile(
            self._pairing.pairing_window, n_candidates
        )
        self.controller.connection.pair_timeout = pair_budget
        return connect_budget

    @callback
    def _async_open_pairing_window(self) -> None:
        """Grant one user-initiated pairing attempt, with a deadline."""
        self._pairing.pairing_window = True
        self._async_apply_pair_budget()
        self._async_arm_pairing_window_timer()

    @callback
    def _async_arm_pairing_window_timer(self) -> None:
        """(Re)start the window's time-to-live."""
        self._async_cancel_pairing_window_timer()

        @callback
        def _expire(_now) -> None:
            self._pairing_window_unsub = None
            if self._pairing.pairing_window:
                _LOGGER.debug(
                    "Pairing window for %s expired after %ss",
                    self.address,
                    PAIRING_WINDOW_TTL_S,
                )
            self._async_close_pairing_window()

        self._pairing_window_unsub = async_call_later(
            self.hass, PAIRING_WINDOW_TTL_S, _expire
        )

    @callback
    def _async_cancel_pairing_window_timer(self) -> None:
        if self._pairing_window_unsub is not None:
            self._pairing_window_unsub()
            self._pairing_window_unsub = None

    @callback
    def _async_close_pairing_window(self) -> None:
        """Close the window and hand the pairing budget back to AUTOMATIC.

        Idempotent, and the only close path: whether the attempt succeeded,
        failed or was never made, an open window lifts the bonded-proxy
        restriction and disarms the quarantine, so it must always end.
        """
        self._async_cancel_pairing_window_timer()
        if not self._pairing.pairing_window:
            return
        self._pairing.pairing_window = False
        self._async_apply_pair_budget()

    @callback
    def _note_timeout_rounds(self) -> None:
        """Carry the library's all-paths-timed-out streak across rebuilds."""
        rounds = getattr(self.controller.connection, "pairing_timeout_rounds", None)
        if isinstance(rounds, int) and not isinstance(rounds, bool):
            self._pairing.timeout_rounds = max(self._pairing.timeout_rounds, rounds)

    def _timed_out_rounds(self, err: PairingRequiredError) -> int:
        """How many all-timed-out rounds are behind this verdict, for the log.

        0.3.10 states it on the error; before that, the connection's counter
        was the only source and it is stale as soon as the library resets it.
        """
        rounds = getattr(err, "timeout_rounds", None)
        if isinstance(rounds, int) and not isinstance(rounds, bool) and rounds > 0:
            return rounds
        rounds = getattr(self.controller.connection, "pairing_timeout_rounds", None)
        if isinstance(rounds, int) and not isinstance(rounds, bool) and rounds > 0:
            return rounds
        return PAIRING_TIMEOUT_ROUNDS

    @callback
    def _async_note_pairing_error(self, err: PairingRequiredError) -> None:
        """Decide what a PairingRequiredError actually proves, and react.

        pymadoka raises the same exception for two epistemically different
        events: a path that explicitly REJECTED the authenticated bond (a
        human must re-pair) and every path merely TIMING OUT for
        PAIRING_TIMEOUT_ROUNDS rounds (an inference congestion alone can
        produce).

        Since 0.3.10 the library says which it is, in `err.reason`, and that
        is the only thing worth reading: it is the raise site's own verdict
        rather than something reconstructed from a side effect.

        Before 0.3.10 (0.3.10 is the pin, but an older library can still be
        installed in a container that has not been rebuilt) there was no
        verdict, only the public round counter — reset to 0 by the rejection
        raise, left at the threshold by the streak raise. Fragile, and note
        that 0.3.10 resets it at BOTH sites, so reading it there would invert
        the diagnosis: `reason` must be consulted FIRST, and the counter only
        when the attribute is absent.

        Anything we can read neither way takes the non-convicting branch — a
        wrong quarantine needs a human at the thermostat to undo, a wrong
        backoff only costs time.
        """
        reason = getattr(err, "reason", None)
        if isinstance(reason, str):
            # A reason we do not recognise is not evidence of a refusal.
            proven_rejection = reason == "rejected"
        else:
            rounds = getattr(
                self.controller.connection, "pairing_timeout_rounds", None
            )
            proven_rejection = (
                isinstance(rounds, int) and not isinstance(rounds, bool) and rounds == 0
            )
        if proven_rejection and self._async_spend_acquittal():
            # Contradicted by our own evidence. Fall through to the timeout
            # tier: still slowed down, still reported, but nothing convicted
            # and no human summoned to the thermostat.
            _LOGGER.warning(
                "%s reported a refused bond, but it completed an authenticated "
                "session less than %ss ago (tried via: %s) — treating this as "
                "congestion and slowing reconnects to %ss instead of asking for "
                "a re-pair; a repeat will be taken at face value",
                self.address,
                AUTH_CORROBORATION_WINDOW_S,
                self._proxy_names(err.tried_sources),
                TIMEOUT_BACKOFF_INTERVAL_S,
            )
            proven_rejection = False
        if proven_rejection:
            self._note_pairing_failure(err)
            self._async_evict_dead_bond(err)
            self._raise_pairing_issue(err)
            self._async_start_reauth()
            return
        _LOGGER.warning(
            "%s did not complete pairing after %s timed-out rounds; slowing "
            "reconnects to %ss instead of concluding it refused the bond",
            self.address,
            self._timed_out_rounds(err),
            TIMEOUT_BACKOFF_INTERVAL_S,
        )
        # Deliberately NOT _note_timeout_rounds(): the streak has just been
        # SPENT (see _async_enter_timeout_backoff), and reading the counter
        # here would only copy a value the library has already zeroed.
        self._async_enter_timeout_backoff(err)

    @callback
    def _async_spend_acquittal(self) -> bool:
        """Does a fresh authenticated session refute this refusal? Spend it if so.

        Consumed whether or not the caller ends up needing it again, which is
        what bounds the forgiveness to one refusal per proven-good session. The
        alternative — leaving the stamp in place — makes the outcome depend on
        whether TIMEOUT_BACKOFF_INTERVAL_S happens to exceed the window, and a
        genuinely dead bond could be excused twice over on that accident.
        """
        last = self._pairing.last_success
        if last is None or monotonic() - last > AUTH_CORROBORATION_WINDOW_S:
            return False
        self._pairing.last_success = None
        return True

    @callback
    def _async_start_reauth(self) -> None:
        """Ask HA for the recovery affordance that needs no entities at all.

        Deliberately NOT by raising ConfigEntryAuthFailed. The first refresh
        runs under raise_on_auth_failed=True, so that exception escapes
        async_setup_entry and puts the entry in SETUP_ERROR — no entities,
        exactly the catch-22 the unconditional degraded load just removed, and
        precisely on the post-restart poll where a rejection surfaces. It would
        also stop the coordinator rescheduling itself (_async_refresh skips
        _schedule_refresh when auth_failed), so the device could never recover
        on its own once a single rejection was recorded.

        async_start_reauth keeps the entry loaded and polling, is idempotent
        (HA drops the call while a reauth or reconfigure flow is already open
        for this entry), and adds a repair with a Fix button ALONGSIDE the
        Reconnect button rather than instead of it — two independent ways out,
        one of which survives having no dashboard and no entities.
        """
        entry = self.config_entry
        if entry is None:
            return
        if entry.state not in (
            ConfigEntryState.LOADED,
            ConfigEntryState.SETUP_IN_PROGRESS,
        ):
            # Nothing is running to re-authenticate. Since P1.1 a configured
            # device always loads, so these are the only two states a polling
            # coordinator can legitimately be in; anything else means the entry
            # is on its way out (unloading, being removed) and a pairing prompt
            # would outlive the thing it belongs to.
            return
        entry.async_start_reauth(self.hass)

    @callback
    def _note_pairing_failure(self, err: PairingRequiredError) -> None:
        """Suspend automatic reconnects: each retry would re-prompt the screen."""
        self._pairing.last_error = err
        self._pairing.suspended = True
        self._pairing.backoff = False
        # The accusation has been delivered; the streak starts over after the
        # user re-pairs.
        self._pairing.timeout_rounds = 0

    @callback
    def _async_enter_timeout_backoff(self, err: PairingRequiredError) -> None:
        """Slow down hard, but never convict.

        A timeout streak is evidence of something and proof of nothing. On a
        bonded path it usually means congestion (and the device recovers on
        its own), but a genuinely dead BRC1H bond also fails by silent
        timeout — so attempts must neither stop (the device would never come
        back without a human) nor continue at poll cadence (that is the SMP
        prompt storm). One attempt every TIMEOUT_BACKOFF_INTERVAL_S, plus a
        repair that suggests re-pairing without asserting a refusal.
        """
        self._pairing.last_error = err
        # The streak has been spent, exactly as upstream spends it: pymadoka
        # >= 0.3.10 zeroes its own round counter at this raise site. Our copy
        # exists only to RE-SEED a Connection that a setup retry rebuilt
        # mid-streak; carrying a spent streak into it would arm every rebuilt
        # Connection at the threshold, so a single all-timeout round would
        # re-raise the verdict immediately, for as long as the device stays
        # unreachable.
        #
        # This cannot weaken the brake. The brake is separate state — backoff,
        # backoff_reason and the pairing_slow repair, all set just below,
        # persisted with the rest of the verdict, re-armed at the top of every
        # poll and lifted only by a successful session or a deliberate user
        # action. Zero here means "count three fresh rounds before saying this
        # again", not "forget that pairing is not completing".
        self._pairing.timeout_rounds = 0
        self._raise_pairing_slow_issue(err)
        self._async_slow_to_backoff_cadence(BACKOFF_PAIRING)

    @callback
    def _async_slow_to_backoff_cadence(self, reason: str | None = None) -> None:
        """Brake the poll cadence to TIMEOUT_BACKOFF_INTERVAL_S. Idempotent.

        The ONE place the cadence is slowed, reached from two independent
        triggers: the library's timeout verdict, and a plain streak of
        UNREACHABLE_THRESHOLD failed polls. The second exists because the first
        cannot be relied on — pymadoka only forms a verdict after a full
        candidate round completes, and when that arithmetic was wrong (per
        attempt instead of per round) this brake was simply never reached and a
        dead bond re-initiated SMP every 60s forever.

        reason=None re-arms without re-diagnosing, for the callers that only
        want to make sure an active brake is still on the clock.

        Re-arming matters: async_update_entry fires the entry update listener,
        which re-applies the configured cadence, so any poll that persists
        pairing state can quietly hand a stalling device its fast interval back.
        """
        if reason is not None and self._pairing.backoff_reason != BACKOFF_PAIRING:
            # A pairing verdict is the more specific story and outranks a bare
            # failure streak; never let the streak overwrite it.
            self._pairing.backoff_reason = reason
        self._pairing.backoff = True
        backoff = timedelta(seconds=TIMEOUT_BACKOFF_INTERVAL_S)
        if self.update_interval != backoff:
            self._normal_interval = self.update_interval
            self.update_interval = backoff

    @callback
    def async_apply_scan_interval(self, scan_interval: int) -> None:
        """Adopt a newly configured cadence without dropping an active brake.

        The entry update listener calls this. It used to assign update_interval
        directly, which silently disarmed a running backoff — and it fires on
        every async_update_entry, including the ones this coordinator itself
        performs to persist a pairing verdict. While the brake is on, the user's
        interval is what we go BACK to, not what we poll at.
        """
        interval = timedelta(seconds=scan_interval)
        if self._pairing.backoff:
            self._normal_interval = interval
            return
        self.update_interval = interval

    @callback
    def async_clear_backoff(self) -> None:
        """Lift the retry brake because a human just intervened.

        The brake is an inference about a device NOBODY HAS TOUCHED: either
        the library's timeout verdict or a streak of failed polls. A user who
        has just walked to the thermostat and re-paired it is better
        information than any inference, so their action must be followed by an
        attempt now — not up to TIMEOUT_BACKOFF_INTERVAL_S later. Field report
        2026-07-26: a thermostat in pairing_slow was re-paired by hand and
        nothing happened for a quarter of an hour, so from the user's point of
        view their action did nothing at all.

        fail_count goes with it, and has to: it is the brake's OTHER trigger,
        so leaving it above the threshold would let the very attempt the user
        asked for re-engage the brake the moment it failed, and the next one
        would again be 900s away. The repairs themselves are left alone — they
        are removed by _clear_issues when an attempt actually succeeds, which
        is the only thing that proves the situation is over.

        Callers must trigger the attempt themselves: this is a @callback and
        the poll it enables is theirs to schedule.
        """
        if not self._pairing.backoff and not self._pairing.fail_count:
            return
        self._pairing.fail_count = 0
        self._async_restore_normal_interval()
        self._async_persist_pairing_state()

    @callback
    def _async_restore_normal_interval(self) -> None:
        """Back to the configured cadence after a successful poll."""
        self._pairing.backoff = False
        self._pairing.backoff_reason = None
        if self._normal_interval is None:
            return
        # If the user changed the poll interval meanwhile, the options update
        # listener already wrote it: leave their value alone.
        if self.update_interval == timedelta(seconds=TIMEOUT_BACKOFF_INTERVAL_S):
            self.update_interval = self._normal_interval
        self._normal_interval = None

    @callback
    def _clear_pairing_suspension(self) -> None:
        """Allow the next poll to connect immediately."""
        self._pairing.suspended = False
        self._pairing.timeout_rounds = 0
        self._pairing.last_error = None

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

    @callback
    def _raise_pairing_slow_issue(self, err: PairingRequiredError) -> None:
        """Warn that pairing never completes — without accusing the device.

        Deliberately WARNING, not ERROR, and worded as a suggestion: the only
        thing established here is that the handshake keeps timing out, which a
        congested proxy explains just as well as a lost bond.
        """
        self._pairing_slow_issue_active = True
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"pairing_slow_{self.address}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="pairing_slow",
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
        self._pairing_slow_issue_active = False
        # The window is permission for one deliberate attempt, not a standing
        # grant: close it as soon as the device is reachable again. The
        # suspension is deliberately NOT cleared here — this also runs on
        # unload, and a reload must not re-arm the prompt salvo.
        self._async_close_pairing_window()
        ir.async_delete_issue(self.hass, DOMAIN, f"unreachable_{self.address}")
        ir.async_delete_issue(self.hass, DOMAIN, f"pairing_required_{self.address}")
        ir.async_delete_issue(self.hass, DOMAIN, f"pairing_slow_{self.address}")

    @callback
    def async_shutdown_extras(self) -> None:
        """Cancel pending timers and clear the repair issues on unload."""
        if self._boost_unsub is not None:
            self._boost_unsub()
            self._boost_unsub = None
        # Explicit even though _clear_issues closes the window too: an armed
        # TTL callback must never outlive the coordinator it points at.
        self._async_cancel_pairing_window_timer()
        self._clear_issues()

    @property
    def fail_count(self) -> int:
        """Consecutive failed polls (reset to 0 by a successful poll)."""
        return self._pairing.fail_count

    @property
    def pairing_suspended(self) -> bool:
        """True while automatic reconnects are suspended pending a re-pair."""
        return self._pairing.suspended

    @property
    def unreachable_issue_active(self) -> bool:
        """True while this coordinator has a device_unreachable repair open."""
        return self._issue_active

    @property
    def pairing_issue_active(self) -> bool:
        """True while this coordinator has a pairing_required repair open."""
        return self._pairing_issue_active

    @property
    def pairing_slow_issue_active(self) -> bool:
        """True while this coordinator has a pairing_slow repair open."""
        return self._pairing_slow_issue_active

    @property
    def pairing_backoff(self) -> bool:
        """True while this device's polling is braked, for whatever reason."""
        return self._pairing.backoff

    @property
    def backoff_reason(self) -> str | None:
        """Why the poll cadence is braked (BACKOFF_* constant), if it is."""
        return self._pairing.backoff_reason if self._pairing.backoff else None

    @property
    def pairing_slow_suspected(self) -> bool:
        """True only when the brake is on because PAIRING keeps timing out.

        Distinct from pairing_backoff: a device that simply stopped answering is
        braked too, and telling its owner that pairing is slow would send them to
        the thermostat to re-pair a device that only needs its power back.
        """
        return (
            self._pairing.backoff and self._pairing.backoff_reason == BACKOFF_PAIRING
        ) or self._pairing_slow_issue_active

    @property
    def skipped_polls(self) -> int:
        """Consecutive polls that never reached the device (lock contention)."""
        return self._pairing.skipped_polls

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
