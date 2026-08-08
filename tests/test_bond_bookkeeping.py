"""The bonded-proxy list must be evidence-based, in both directions.

Two defects, one design (review of 2026-07-26, section 4):

1. **Under-recorded.** A source was persisted only after a *fully successful
   poll*. A connect that authenticated but whose GATT poll then failed PROVED
   the bond and recorded nothing - and pymadoka's on_disconnect clears
   connected_source, so the evidence was gone for good.
2. **Append-only.** Nothing was ever removed, so a reflashed, replaced or
   manually unpaired proxy stayed "bonded" forever and every reconnect kept
   retrying that dead path - feeding the very storm the restriction exists to
   prevent.

The rules that follow: record the bond the moment the authenticated connect
succeeds; drop a proxy after BOND_EVICTION_FAILURES consecutive *proven*
refusals attributed to it; never empty the list (an empty list means
"unrestricted", which would switch the anti-storm policy off rather than
protect the device); and never attribute a refusal to a proxy the evidence
cannot single out.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka import ConnectionException, PairingRequiredError
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.daikin_madoka.const import (
    BOND_EVICTION_FAILURES,
    CONF_BONDED_SOURCES,
    CONF_MAC,
    CONF_PAIRING_STATE,
    CONF_PREFERRED_SOURCE,
    DOMAIN,
)
from custom_components.daikin_madoka.coordinator import (
    PAIRING_STATE_KEY,
    MadokaCoordinator,
    async_pairing_state,
    async_restore_pairing_state,
)

MAC = "D0:CF:13:0F:11:F6"
PROXY_A = "AA:BB:CC:11:22:33"
PROXY_B = "DD:EE:FF:44:55:66"
BLUETOOTH = "homeassistant.components.bluetooth"


def _controller(source: str | None = PROXY_A, rounds: int = 0) -> MagicMock:
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.connection.connected_source = source
    controller.connection.pair_timeout = 8.0
    controller.connection.pairing_timeout_rounds = rounds
    controller.start = AsyncMock()
    controller.update = AsyncMock()
    controller.refresh_status.return_value = {"set_point": {"cooling_set_point": 25}}
    return controller


def _connects(controller: MagicMock) -> None:
    """Make start() succeed the way the library does: status flips to CONNECTED."""

    async def _start() -> None:
        controller.connection.connection_status = ConnectionStatus.CONNECTED

    controller.start = AsyncMock(side_effect=_start)


def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, controller: MagicMock
) -> MadokaCoordinator:
    token = config_entries.current_entry.set(entry)
    try:
        return MadokaCoordinator(hass, controller, scan_interval=60)
    finally:
        config_entries.current_entry.reset(token)


def _entry(hass: HomeAssistant, **data) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC, **data})
    entry.add_to_hass(hass)
    return entry


def _patched_bluetooth():
    return (
        patch(f"{BLUETOOTH}.async_address_present", return_value=True),
        patch(
            f"{BLUETOOTH}.async_scanner_by_source",
            return_value=SimpleNamespace(name="Proxy"),
        ),
    )


# --------------------------------------------------------------------------
# Recording: the bond is proven by the connect, not by the poll
# --------------------------------------------------------------------------


async def test_an_authenticated_connect_records_the_bond_even_if_the_poll_fails(
    hass: HomeAssistant,
) -> None:
    """The evidence is the authentication; a failed GATT read does not undo it."""
    entry = _entry(hass)
    controller = _controller()
    _connects(controller)
    controller.update = AsyncMock(side_effect=ConnectionException("no answer"))
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_A]


async def test_a_connect_through_the_fallback_path_records_nothing(
    hass: HomeAssistant,
) -> None:
    """connected_source stays None on the library's single-path fallback.

    That path lets habluetooth's scorer choose a proxy and pairs with it
    unconditionally, so recording it would launder an auto-pairing into a
    policy-approved bond.
    """
    entry = _entry(hass)
    controller = _controller(source=None)
    _connects(controller)
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert CONF_BONDED_SOURCES not in entry.data


async def test_a_failed_connect_records_nothing(hass: HomeAssistant) -> None:
    """A stale connected_source from an earlier session proves nothing now."""
    entry = _entry(hass)
    controller = _controller()
    controller.start = AsyncMock(side_effect=ConnectionException("device off"))
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    assert CONF_BONDED_SOURCES not in entry.data


# --------------------------------------------------------------------------
# Eviction: a proven refusal, attributed, repeated
# --------------------------------------------------------------------------


def _refuse(coordinator: MadokaCoordinator, sources: list[str | None]) -> None:
    """Make the next connect fail with a PROVEN refusal over ``sources``."""
    coordinator.controller.start = AsyncMock(
        side_effect=PairingRequiredError(MAC, sources)
    )
    # rounds == 0 is the library's side channel for "a path actively rejected".
    coordinator.controller.connection.pairing_timeout_rounds = 0


async def _refuse_repeatedly(
    hass: HomeAssistant,
    coordinator: MadokaCoordinator,
    sources: list[str | None],
    times: int,
) -> None:
    """Refuse ``times`` times, each one standing for a deliberate user retry.

    A proven refusal quarantines the device, and the quarantine is exactly what
    stops the NEXT automatic poll from touching it. So consecutive refusals can
    only ever come from a human pressing Reconnect (or completing a reauth
    flow), which lifts the suspension first. Reproduce that here rather than
    pretending automatic polls could stack the evidence on their own.
    """
    present, scanner = _patched_bluetooth()
    with present, scanner:
        for _ in range(times):
            async_pairing_state(hass, MAC).suspended = False
            _refuse(coordinator, sources)
            await coordinator.async_refresh()


async def test_a_proxy_that_keeps_refusing_is_evicted(hass: HomeAssistant) -> None:
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]})
    coordinator = _coordinator(hass, entry, _controller())

    await _refuse_repeatedly(hass, coordinator, [PROXY_A], BOND_EVICTION_FAILURES)

    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_B]


async def test_one_refusal_is_not_enough(hass: HomeAssistant) -> None:
    """A single refusal can be a proxy mid-reboot; the streak has to repeat."""
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]})
    coordinator = _coordinator(hass, entry, _controller())

    await _refuse_repeatedly(hass, coordinator, [PROXY_A], BOND_EVICTION_FAILURES - 1)

    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_A, PROXY_B]


async def test_evicting_the_last_bonded_source_is_refused(
    hass: HomeAssistant,
) -> None:
    """An empty list means "unrestricted", which would DISABLE the policy.

    Removing the last known-good path would not protect the thermostat: it
    would let unattended polls pair with any proxy in range. Recovery for a
    device with no working bond is the reauth flow, deliberately, with a human.
    """
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A]})
    coordinator = _coordinator(hass, entry, _controller())

    await _refuse_repeatedly(hass, coordinator, [PROXY_A], BOND_EVICTION_FAILURES + 3)

    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_A]


def _refuse_with_evidence(
    coordinator: MadokaCoordinator,
    sources: list[str | None],
    evidence: dict[str | None, str],
) -> None:
    """A refusal as pymadoka >= 0.3.10 reports it: with a per-path verdict."""
    err = PairingRequiredError(MAC, sources, reason="rejected", evidence=evidence)
    coordinator.controller.start = AsyncMock(side_effect=err)


async def _refuse_with_evidence_repeatedly(
    hass: HomeAssistant,
    coordinator: MadokaCoordinator,
    sources: list[str | None],
    evidence: dict[str | None, str],
    times: int,
) -> None:
    present, scanner = _patched_bluetooth()
    with present, scanner:
        for _ in range(times):
            async_pairing_state(hass, MAC).suspended = False
            _refuse_with_evidence(coordinator, sources, evidence)
            await coordinator.async_refresh()


async def test_evidence_attributes_a_refusal_in_a_multi_path_round(
    hass: HomeAssistant,
) -> None:
    """The whole point of the per-path verdict: eviction in a real home.

    With three or four proxies a round is never single-path, so the
    "exactly one tried source" rule made eviction unreachable and a dead
    proxy stayed on the bonded list forever.
    """
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]})
    coordinator = _coordinator(hass, entry, _controller())

    await _refuse_with_evidence_repeatedly(
        hass,
        coordinator,
        [PROXY_A, PROXY_B],
        {PROXY_A: "rejected", PROXY_B: "timeout"},
        BOND_EVICTION_FAILURES,
    )

    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_B]
    assert async_pairing_state(hass, MAC).auth_failures.get(PROXY_B) is None


async def test_evidence_never_charges_a_path_that_only_timed_out(
    hass: HomeAssistant,
) -> None:
    """A timeout is congestion until proven otherwise, per path as well."""
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]})
    coordinator = _coordinator(hass, entry, _controller())

    await _refuse_with_evidence_repeatedly(
        hass,
        coordinator,
        [PROXY_A, PROXY_B],
        {PROXY_A: "timeout", PROXY_B: "timeout"},
        BOND_EVICTION_FAILURES + 2,
    )

    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_A, PROXY_B]
    assert async_pairing_state(hass, MAC).auth_failures == {}


async def test_evidence_still_never_empties_the_bonded_list(
    hass: HomeAssistant,
) -> None:
    """Both paths proven dead: evict one, keep the last one regardless."""
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]})
    coordinator = _coordinator(hass, entry, _controller())

    await _refuse_with_evidence_repeatedly(
        hass,
        coordinator,
        [PROXY_A, PROXY_B],
        {PROXY_A: "rejected", PROXY_B: "rejected"},
        BOND_EVICTION_FAILURES + 3,
    )

    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_B]


async def test_a_multi_path_refusal_evicts_nothing(hass: HomeAssistant) -> None:
    """Without a per-path verdict, tried_sources is flat: a 2-path round
    cannot say WHICH path refused.

    pymadoka <= 0.3.9 reports the round, not a per-path verdict, so
    attribution is impossible here. Under-evicting costs a few futile retries;
    over-evicting deletes the one path that still works.
    """
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]})
    coordinator = _coordinator(hass, entry, _controller())

    await _refuse_repeatedly(
        hass, coordinator, [PROXY_A, PROXY_B], BOND_EVICTION_FAILURES + 2
    )

    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_A, PROXY_B]
    assert async_pairing_state(hass, MAC).auth_failures == {}


async def test_a_local_adapter_refusal_evicts_nothing(hass: HomeAssistant) -> None:
    """A None source is the local adapter: there is nothing to remove."""
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]})
    coordinator = _coordinator(hass, entry, _controller())

    await _refuse_repeatedly(hass, coordinator, [None], BOND_EVICTION_FAILURES + 1)

    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_A, PROXY_B]


async def test_a_timeout_streak_never_costs_a_bond(hass: HomeAssistant) -> None:
    """Only a rejection proves anything; a timeout is congestion until proven."""
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]})
    controller = _controller(rounds=3)
    # reason= is mandatory to model a streak since the 0.3.10 pin: the
    # constructor defaults to "rejected" (it keeps pre-0.3.10 call sites
    # meaning what they meant), so a bare PairingRequiredError is now a
    # REFUSAL and the round counter no longer says otherwise.
    controller.start = AsyncMock(
        side_effect=PairingRequiredError(
            MAC, [PROXY_A], reason="timeout_streak", timeout_rounds=3
        )
    )
    coordinator = _coordinator(hass, entry, controller)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        for _ in range(BOND_EVICTION_FAILURES + 2):
            await coordinator.async_refresh()

    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_A, PROXY_B]
    assert async_pairing_state(hass, MAC).auth_failures == {}


async def test_a_successful_session_clears_the_eviction_streak(
    hass: HomeAssistant,
) -> None:
    """Consecutive is load-bearing: an acquitted path starts again from zero."""
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]})
    controller = _controller()
    coordinator = _coordinator(hass, entry, controller)

    await _refuse_repeatedly(hass, coordinator, [PROXY_A], BOND_EVICTION_FAILURES - 1)

    present, scanner = _patched_bluetooth()
    with present, scanner:
        async_pairing_state(hass, MAC).suspended = False
        _connects(controller)
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert async_pairing_state(hass, MAC).auth_failures == {}

    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    await _refuse_repeatedly(hass, coordinator, [PROXY_A], BOND_EVICTION_FAILURES - 1)

    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_A, PROXY_B]


async def test_eviction_drops_a_stale_preferred_source(hass: HomeAssistant) -> None:
    """A sticky proxy that is no longer allowed would just match nothing."""
    entry = _entry(
        hass,
        **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B], CONF_PREFERRED_SOURCE: PROXY_A},
    )
    coordinator = _coordinator(hass, entry, _controller())

    await _refuse_repeatedly(hass, coordinator, [PROXY_A], BOND_EVICTION_FAILURES)

    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_B]
    assert CONF_PREFERRED_SOURCE not in entry.data


async def test_the_eviction_streak_survives_a_restart(hass: HomeAssistant) -> None:
    """A setup retry rebuilds everything; evidence held on it would never add up."""
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]})
    coordinator = _coordinator(hass, entry, _controller())

    await _refuse_repeatedly(hass, coordinator, [PROXY_A], 1)

    assert entry.data[CONF_PAIRING_STATE][MAC]["auth_failures"] == {PROXY_A: 1}

    hass.data.pop(PAIRING_STATE_KEY, None)
    async_restore_pairing_state(hass, entry)

    assert async_pairing_state(hass, MAC).auth_failures == {PROXY_A: 1}


# --------------------------------------------------------------------------
# A round that cannot name the path must charge nobody (#53)
# --------------------------------------------------------------------------
#
# Under HA the candidate handed to establish_connection does not pin the path:
# habluetooth keeps only the address and re-picks a scanner by RSSI. So a
# refusal is attributable only when a link existed and the wrapper named the
# scanner that carried it. pymadoka-ng 0.3.11 keys everything else under None.
#
# The trap this closes: a non-empty evidence mapping naming no proven source
# used to fall through to the legacy "exactly one tried source" rule, which
# would answer "which proxy refused?" with the candidate we merely aimed at —
# reinstating the very guess the library stopped making.


async def test_an_unattributable_refusal_charges_nobody(hass: HomeAssistant) -> None:
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]})
    coordinator = _coordinator(hass, entry, _controller())

    await _refuse_with_evidence_repeatedly(
        hass,
        coordinator,
        [PROXY_A],
        {None: "rejected"},
        BOND_EVICTION_FAILURES + 2,
    )

    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_A, PROXY_B]
    assert async_pairing_state(hass, MAC).auth_failures == {}


async def test_a_single_source_round_no_longer_overrides_the_evidence(
    hass: HomeAssistant,
) -> None:
    """The legacy rule is for libraries that carry no evidence at all.

    One candidate and one unattributable verdict is exactly the shape the old
    fall-through mistook for proof. Asserted on the FIRST refusal, before the
    eviction threshold: eviction pops the counter it acted on, so counting at
    the threshold cannot tell "never charged" from "charged, then evicted".
    """
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]})
    coordinator = _coordinator(hass, entry, _controller())

    await _refuse_with_evidence_repeatedly(
        hass, coordinator, [PROXY_A], {None: "rejected"}, 1
    )

    assert async_pairing_state(hass, MAC).auth_failures == {}


async def test_a_proven_source_is_still_charged_alongside_an_unknown_one(
    hass: HomeAssistant,
) -> None:
    """Partial knowledge is still knowledge: the named path answers for itself."""
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]})
    coordinator = _coordinator(hass, entry, _controller())

    await _refuse_with_evidence_repeatedly(
        hass,
        coordinator,
        [PROXY_A, PROXY_B],
        {PROXY_A: "rejected", None: "rejected"},
        BOND_EVICTION_FAILURES,
    )

    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_B]


async def test_a_library_with_no_evidence_still_uses_the_legacy_rule(
    hass: HomeAssistant,
) -> None:
    """pymadoka <= 0.3.9 has no mapping, and a single-source round is unambiguous."""
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [PROXY_A, PROXY_B]})
    coordinator = _coordinator(hass, entry, _controller())

    await _refuse_repeatedly(hass, coordinator, [PROXY_A], BOND_EVICTION_FAILURES)

    assert entry.data[CONF_BONDED_SOURCES] == [PROXY_B]
