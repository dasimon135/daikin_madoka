"""PairingRequiredError means two different things; treat them differently.

pymadoka raises PairingRequiredError both when a path explicitly REJECTED the
authenticated bond (proof that a human must re-pair) and when every path
merely TIMED OUT for three rounds (an inference, which congestion alone can
produce). The pinned library (0.3.10) says which on the error itself, in
`reason`; before that the two raises shared one constructor and were
byte-identical, leaving only the public round counter as a side channel —
reset to 0 by the rejection raise, left at >= PAIRING_TIMEOUT_ROUNDS by the
streak. Both paths are exercised here: the fallback is still live code for
anyone running an older library.

Field incident 2026-07-26: convicting on both is what falsely quarantined a
thermostat whose bond was intact (Parents) - a manual Reconnect restored it
instantly. Ignoring timeouts is not an option either: a genuinely dead BRC1H
bond fails by silent timeout, so ignoring them recreates the v3.6.0 prompt
storm. Hence three tiers:

1. explicit rejection -> suspend + ERROR repair (a human is proven necessary);
2. timeout streak -> no conviction, a long capped poll interval and a
   WARNING repair that suggests rather than accuses;
3. anything else -> unchanged retry cadence.
"""

import contextlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka import PairingRequiredError
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.daikin_madoka.const import (
    AUTH_CORROBORATION_WINDOW_S,
    CONF_MAC,
    DOMAIN,
    TIMEOUT_BACKOFF_INTERVAL_S,
)
from custom_components.daikin_madoka.coordinator import (
    BACKOFF_PAIRING,
    MadokaCoordinator,
    async_pairing_state,
)

MAC = "D0:CF:13:0F:11:F6"
SOURCE = "AA:BB:CC:11:22:33"
STREAK = 3

BLUETOOTH = "homeassistant.components.bluetooth"
COORDINATOR = "custom_components.daikin_madoka.coordinator"


def _error(reason: str | None = None, sources: list[str | None] | None = None):
    """A PairingRequiredError as the pinned pymadoka (0.3.10) raises it.

    reason=None models a PRE-0.3.10 library, which had no verdict at all: the
    attribute is deleted, because 0.3.10's constructor always sets one and
    DEFAULTS IT TO "rejected" (so that pre-0.3.10 call sites keep meaning what
    they meant). That default is why every "streak" here now has to say so
    explicitly — a bare PairingRequiredError is a refusal.
    """
    if reason is None:
        err = PairingRequiredError(MAC, sources if sources is not None else [SOURCE])
        with contextlib.suppress(AttributeError):
            del err.reason
        return err
    return PairingRequiredError(
        MAC,
        sources if sources is not None else [SOURCE],
        reason=reason,
        timeout_rounds=STREAK if reason == "timeout_streak" else 0,
    )


def _controller(rounds: int | None = 0, err=None) -> MagicMock:
    """Disconnected controller whose connect raises PairingRequiredError.

    rounds is what the library leaves on connection.pairing_timeout_rounds at
    the moment it raises: 0 after a proven rejection, >= 3 after a streak
    (pymadoka <= 0.3.9). None models a library that dropped the property, and
    then no `reason` is stated either — the pre-0.3.10 world, where the
    coordinator has nothing at all to read.

    Since the 0.3.10 pin the counter is no longer the channel (the library
    zeroes it at BOTH raise sites), so unless err says otherwise the default
    exception carries the verdict `rounds` is shorthand for.
    """
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.connection.connected_source = SOURCE
    controller.connection.pair_timeout = 8.0
    if rounds is None:
        del controller.connection.pairing_timeout_rounds
    else:
        controller.connection.pairing_timeout_rounds = rounds
    if err is None:
        err = (
            _error()
            if rounds is None
            else _error("rejected" if rounds == 0 else "timeout_streak")
        )
    controller.start = AsyncMock(side_effect=err)
    controller.update = AsyncMock()
    controller.refresh_status.return_value = {"set_point": {"cooling_set_point": 25}}
    return controller


def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, controller: MagicMock
) -> MadokaCoordinator:
    token = config_entries.current_entry.set(entry)
    try:
        return MadokaCoordinator(hass, controller, scan_interval=60)
    finally:
        config_entries.current_entry.reset(token)


def _patched_bluetooth():
    return (
        patch(f"{BLUETOOTH}.async_address_present", return_value=True),
        patch(
            f"{BLUETOOTH}.async_scanner_by_source",
            return_value=SimpleNamespace(name="Proxy Salon"),
        ),
    )


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    return entry


# --------------------------------------------------------------------------
# Tier 1: an explicit rejection is proof, and still convicts
# --------------------------------------------------------------------------


async def test_explicit_rejection_quarantines_immediately(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    controller = _controller(rounds=0)
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, f"pairing_required_{MAC}")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert registry.async_get_issue(DOMAIN, f"pairing_slow_{MAC}") is None
    assert async_pairing_state(hass, MAC).suspended is True
    assert coordinator.update_interval == timedelta(seconds=60)


# --------------------------------------------------------------------------
# Tier 2: a timeout streak is an inference - back off, do not convict
# --------------------------------------------------------------------------


async def test_timeout_streak_does_not_convict(hass: HomeAssistant) -> None:
    """No suspension, no accusation: a congested path is not a dead bond."""
    entry = _entry(hass)
    controller = _controller(rounds=STREAK)
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    registry = ir.async_get(hass)
    assert not coordinator.last_update_success
    assert async_pairing_state(hass, MAC).suspended is False
    assert registry.async_get_issue(DOMAIN, f"pairing_required_{MAC}") is None


async def test_timeout_streak_raises_a_warning_repair(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    controller = _controller(rounds=STREAK)
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"pairing_slow_{MAC}")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "pairing_slow"
    assert issue.translation_placeholders == {
        "device": "Daikin",
        "proxies": "Proxy Salon",
    }
    assert coordinator.pairing_slow_issue_active is True


async def test_timeout_streak_lengthens_the_poll_interval(
    hass: HomeAssistant,
) -> None:
    """Never ignored: SMP attempts keep going but at a capped, slow cadence."""
    entry = _entry(hass)
    controller = _controller(rounds=STREAK)
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    assert coordinator.update_interval == timedelta(seconds=TIMEOUT_BACKOFF_INTERVAL_S)


async def test_the_verdict_spends_the_timeout_streak(hass: HomeAssistant) -> None:
    """Mirror the library: raising the streak verdict consumes the streak.

    pymadoka 0.3.10 zeroes its own round counter at the streak raise. Our copy
    exists ONLY to re-seed a rebuilt Connection (a setup retry throws the old
    one away mid-streak); keeping a spent streak in it would hand every
    rebuilt Connection a counter already at the threshold, so the next single
    all-timeout round would re-raise the verdict immediately, forever.

    What must NOT change is the brake itself: the device stays in pairing_slow
    at the 900s cadence. Only the counter is reset.
    """
    entry = _entry(hass)
    controller = _controller(rounds=STREAK)
    coordinator = _coordinator(hass, entry, controller)
    state = async_pairing_state(hass, MAC)
    state.timeout_rounds = STREAK  # accumulated across earlier rebuilds

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    assert state.timeout_rounds == 0
    assert state.backoff is True
    assert coordinator.backoff_reason == BACKOFF_PAIRING
    assert coordinator.update_interval == timedelta(seconds=TIMEOUT_BACKOFF_INTERVAL_S)
    assert coordinator.pairing_slow_issue_active is True


async def test_a_rebuild_after_a_verdict_starts_the_streak_from_zero(
    hass: HomeAssistant,
) -> None:
    """The seed is the whole reason the counter is persisted at all."""
    entry = _entry(hass)
    coordinator = _coordinator(hass, entry, _controller(rounds=STREAK))
    async_pairing_state(hass, MAC).timeout_rounds = STREAK

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    second = _coordinator(hass, entry, _controller(rounds=STREAK))

    second.controller.connection.resume_pairing_timeout_rounds.assert_called_once_with(0)
    # The brake is separate state and survives the rebuild untouched.
    assert second.update_interval == timedelta(seconds=TIMEOUT_BACKOFF_INTERVAL_S)
    assert second.backoff_reason == BACKOFF_PAIRING


async def test_backoff_still_probes_the_device(hass: HomeAssistant) -> None:
    """Unlike a quarantine, the next poll is allowed to try again."""
    entry = _entry(hass)
    controller = _controller(rounds=STREAK)
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert controller.start.await_count == 2


async def test_a_success_restores_the_interval_and_clears_the_repair(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    controller = _controller(rounds=STREAK)
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()
        assert coordinator.update_interval == timedelta(
            seconds=TIMEOUT_BACKOFF_INTERVAL_S
        )

        controller.connection.connection_status = ConnectionStatus.CONNECTED
        await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.update_interval == timedelta(seconds=60)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"pairing_slow_{MAC}") is None
    assert coordinator.pairing_slow_issue_active is False


async def test_backoff_suppresses_the_unreachable_repair(
    hass: HomeAssistant,
) -> None:
    """Power/range advice would contradict the pairing warning on screen."""
    entry = _entry(hass)
    controller = _controller(rounds=STREAK)
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        for _ in range(6):
            await coordinator.async_refresh()

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"pairing_slow_{MAC}") is not None
    assert registry.async_get_issue(DOMAIN, f"unreachable_{MAC}") is None


# --------------------------------------------------------------------------
# The side channel is fragile: an unreadable counter must not convict
# --------------------------------------------------------------------------


async def test_a_missing_round_counter_takes_the_safe_branch(
    hass: HomeAssistant,
) -> None:
    """Without evidence we never accuse: back off, do not quarantine.

    Neither a reason nor a counter — a library older than 0.3.10 that also
    dropped the property. The coordinator has nothing to read and must still
    take the safe branch.
    """
    entry = _entry(hass)
    controller = _controller(rounds=None)
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    registry = ir.async_get(hass)
    assert async_pairing_state(hass, MAC).suspended is False
    assert registry.async_get_issue(DOMAIN, f"pairing_required_{MAC}") is None
    assert registry.async_get_issue(DOMAIN, f"pairing_slow_{MAC}") is not None


# --------------------------------------------------------------------------
# pymadoka >= 0.3.10 states the reason outright; it outranks the side channel
# --------------------------------------------------------------------------


async def test_reason_rejected_convicts_even_when_the_counter_says_streak(
    hass: HomeAssistant,
) -> None:
    """The library's own verdict wins over the counter we used to infer it.

    0.3.10 resets the streak at BOTH raise sites, so the counter no longer
    discriminates anything — reading it first would silently invert the
    diagnosis on the version we are about to run.
    """
    entry = _entry(hass)
    controller = _controller(rounds=STREAK, err=_error(reason="rejected"))
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    registry = ir.async_get(hass)
    assert async_pairing_state(hass, MAC).suspended is True
    assert registry.async_get_issue(DOMAIN, f"pairing_required_{MAC}") is not None
    assert registry.async_get_issue(DOMAIN, f"pairing_slow_{MAC}") is None


async def test_reason_timeout_streak_never_convicts_whatever_the_counter_says(
    hass: HomeAssistant,
) -> None:
    """rounds == 0 used to mean "proven rejection"; the reason says otherwise.

    This is the false quarantine of field incident 2026-07-26, now impossible:
    a thermostat whose bond is intact must never be locked out on an inference.
    """
    entry = _entry(hass)
    controller = _controller(rounds=0, err=_error(reason="timeout_streak"))
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    registry = ir.async_get(hass)
    assert async_pairing_state(hass, MAC).suspended is False
    assert registry.async_get_issue(DOMAIN, f"pairing_required_{MAC}") is None
    assert registry.async_get_issue(DOMAIN, f"pairing_slow_{MAC}") is not None
    assert coordinator.update_interval == timedelta(seconds=TIMEOUT_BACKOFF_INTERVAL_S)


async def test_an_unknown_reason_takes_the_safe_branch(hass: HomeAssistant) -> None:
    """A value we do not understand is not evidence of a refusal."""
    entry = _entry(hass)
    controller = _controller(rounds=0, err=_error(reason="something_new"))
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    registry = ir.async_get(hass)
    assert async_pairing_state(hass, MAC).suspended is False
    assert registry.async_get_issue(DOMAIN, f"pairing_required_{MAC}") is None
    assert registry.async_get_issue(DOMAIN, f"pairing_slow_{MAC}") is not None


async def test_legacy_library_still_uses_the_side_channel(
    hass: HomeAssistant,
) -> None:
    """A pre-0.3.10 library states no reason: its rejections must still land."""
    entry = _entry(hass)
    controller = _controller(rounds=0, err=_error())  # no reason attribute
    coordinator = _coordinator(hass, entry, controller)
    assert not hasattr(controller.start.side_effect, "reason")

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    assert async_pairing_state(hass, MAC).suspended is True
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"pairing_required_{MAC}")


async def test_backoff_survives_a_coordinator_rebuild(hass: HomeAssistant) -> None:
    """A setup retry rebuilds everything; the slow cadence must not reset.

    This is the same lesson as the timeout streak itself: HA builds a fresh
    Controller and coordinator on every ConfigEntryNotReady retry, so any
    protection held on those objects evaporates and the device gets hammered
    at full speed again.
    """
    entry = _entry(hass)
    first = _coordinator(hass, entry, _controller(rounds=STREAK))

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await first.async_refresh()

    second = _coordinator(hass, entry, _controller(rounds=STREAK))

    assert second.update_interval == timedelta(seconds=TIMEOUT_BACKOFF_INTERVAL_S)


# --------------------------------------------------------------------------
# Tier 1b: a rejection that contradicts a fresh success is not proof
# --------------------------------------------------------------------------
#
# Field incident 2026-08-07 (issue #51). After an HA restart the Salon
# thermostat connected and polled successfully at 22:11:44, then at 22:14:00
# BOTH of its proxies "refused the authenticated bond" and it was quarantined
# for 11 hours until someone walked to it.
#
# A device that authenticates at 22:11:44 has not lost its bond on two separate
# proxies at 22:14:00. pymadoka classifies refusals from error TEXT
# ("insufficient authentication", ATT error=5), and the BRC1H accepts a single
# central: a stale proxy link, or contention while every coordinator reconnects
# at once after a restart, produces that exact text without the bond being
# gone. So a rejection arriving moments after a proven-good session is
# downgraded to the timeout tier — the same cost asymmetry the rest of this
# module rests on: a wrong quarantine needs a human at the thermostat, a wrong
# backoff only costs time.


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _connects(controller: MagicMock) -> None:
    """Make start() succeed the way the library does: status flips to CONNECTED."""

    async def _start() -> None:
        controller.connection.connection_status = ConnectionStatus.CONNECTED

    controller.start = AsyncMock(side_effect=_start)


def _rejects(controller: MagicMock) -> None:
    """Back to a start() that raises a proven refusal."""
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.start = AsyncMock(side_effect=_error("rejected"))


async def test_rejection_soon_after_a_successful_session_does_not_convict(
    hass: HomeAssistant,
) -> None:
    """The 22:11 success refutes the 22:14 refusal: back off, do not quarantine."""
    entry = _entry(hass)
    controller = _controller(rounds=0)
    coordinator = _coordinator(hass, entry, controller)
    clock = _Clock()

    present, scanner = _patched_bluetooth()
    with present, scanner, patch(f"{COORDINATOR}.monotonic", new=clock):
        _connects(controller)
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        clock.now += 136  # 22:11:44 -> 22:14:00
        _rejects(controller)
        await coordinator.async_refresh()

    registry = ir.async_get(hass)
    assert async_pairing_state(hass, MAC).suspended is False
    assert registry.async_get_issue(DOMAIN, f"pairing_required_{MAC}") is None
    # Downgraded to the timeout tier, not ignored: slow cadence + soft repair.
    assert registry.async_get_issue(DOMAIN, f"pairing_slow_{MAC}") is not None
    assert coordinator.update_interval == timedelta(seconds=TIMEOUT_BACKOFF_INTERVAL_S)


async def test_the_acquittal_is_spent_so_a_repeat_rejection_convicts(
    hass: HomeAssistant,
) -> None:
    """One session excuses one refusal. A bond that really died still surfaces.

    Deliberately independent of the backoff interval: the acquittal is consumed
    when it is used, so the second refusal convicts even if it arrives while
    the corroboration window is still open.
    """
    entry = _entry(hass)
    controller = _controller(rounds=0)
    coordinator = _coordinator(hass, entry, controller)
    clock = _Clock()

    present, scanner = _patched_bluetooth()
    with present, scanner, patch(f"{COORDINATOR}.monotonic", new=clock):
        _connects(controller)
        await coordinator.async_refresh()
        clock.now += 136
        _rejects(controller)
        await coordinator.async_refresh()
        assert async_pairing_state(hass, MAC).suspended is False
        clock.now += 10
        await coordinator.async_refresh()

    assert async_pairing_state(hass, MAC).suspended is True
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"pairing_required_{MAC}")


async def test_rejection_long_after_the_last_success_still_convicts(
    hass: HomeAssistant,
) -> None:
    """The window is what makes the success relevant; outside it, proof stands."""
    entry = _entry(hass)
    controller = _controller(rounds=0)
    coordinator = _coordinator(hass, entry, controller)
    clock = _Clock()

    present, scanner = _patched_bluetooth()
    with present, scanner, patch(f"{COORDINATOR}.monotonic", new=clock):
        _connects(controller)
        await coordinator.async_refresh()
        clock.now += AUTH_CORROBORATION_WINDOW_S + 1
        _rejects(controller)
        await coordinator.async_refresh()

    assert async_pairing_state(hass, MAC).suspended is True
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"pairing_required_{MAC}")


async def test_a_rejection_with_no_session_behind_it_convicts(
    hass: HomeAssistant,
) -> None:
    """Nothing to corroborate with — the very first poll after a restart."""
    entry = _entry(hass)
    controller = _controller(rounds=0)
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    assert async_pairing_state(hass, MAC).suspended is True


async def test_the_acquittal_is_never_persisted(hass: HomeAssistant) -> None:
    """A monotonic stamp means nothing in the next process.

    Restoring one would hand a fresh HA a free pass it did not earn, and the
    numbers are not even comparable across a reboot.
    """
    entry = _entry(hass)
    controller = _controller(rounds=0)
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        _connects(controller)
        await coordinator.async_refresh()

    state = async_pairing_state(hass, MAC)
    assert state.last_success is not None
    assert "last_success" not in (state.as_stored() or {})
