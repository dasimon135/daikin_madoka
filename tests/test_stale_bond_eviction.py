"""bonded_sources must stop claiming a key the proxy no longer holds.

CONF_BONDED_SOURCES records what a session once succeeded through. It is not a
reading of the proxy's keystore, and the two drift apart: on 2026-08-27 a proxy
listed as bonded for two thermostats had lost both their keys while keeping a
third's. Every time HA elected that path, pymadoka called pair(), a real
numeric-comparison exchange started, and a 6-digit code lit up on the
thermostat waiting for a human who was not there. Proven, not inferred: the
ESPHome responders pushed the passkeys to HA as notifications (431206 for
Manon, 787382 for Salon) and BlueSight independently reported "this proxy does
not have the pairing key" for the same pair.

The allowed-source veto does not help there — it trusts the same stale list.

The discriminator is NOT "this path times out", which congestion also produces.
It is "this path NEVER succeeds". A valid bond re-encrypts silently and quickly
whenever the proxy is not busy, and any success clears the streak; a keyless
path cannot succeed on its own at all, because completing it needs a person at
the thermostat. So a streak of pairing timeouts on one source, unbroken by any
success on that same source, is the evidence — and it is what timeout_sources
already counts.

Evicting is safe here precisely because the veto exists: a dropped source stops
being paired on, so it stops prompting, and if it turns out to have been the
only usable path the unbonded_path repair says so by name and the reauth flow
fixes it in one user action.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka import PairingRequiredError
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.daikin_madoka.const import (
    BOND_STALE_TIMEOUTS,
    BOND_STALE_TIMEOUTS_CORROBORATED,
    CONF_BONDED_SOURCES,
    CONF_MAC,
    CONF_PREFERRED_SOURCE,
    DOMAIN,
)
from custom_components.daikin_madoka.coordinator import (
    MadokaCoordinator,
    async_pairing_state,
)

MAC = "D0:CF:13:0F:11:F6"
STALE = "AA:BB:CC:11:22:33"
GOOD = "DD:EE:FF:44:55:66"

BLUETOOTH = "homeassistant.components.bluetooth"


def _timeout_error(evidence: dict) -> PairingRequiredError:
    return PairingRequiredError(
        MAC, list(evidence), reason="timeout_streak",
        timeout_rounds=3, evidence=evidence,
    )


def _controller(err=None) -> MagicMock:
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.connection.connected_source = None
    controller.connection.pair_timeout = 8.0
    controller.connection.pairing_timeout_rounds = 0
    controller.info = {}
    controller.start = AsyncMock(side_effect=err)
    controller.update = AsyncMock()
    controller.refresh_status.return_value = {}
    return controller


def _coordinator(hass, entry, controller) -> MadokaCoordinator:
    token = config_entries.current_entry.set(entry)
    try:
        return MadokaCoordinator(hass, controller, scan_interval=60)
    finally:
        config_entries.current_entry.reset(token)


def _entry(hass: HomeAssistant, bonded=None, preferred=None) -> MockConfigEntry:
    data = {CONF_MAC: MAC, CONF_BONDED_SOURCES: bonded or [STALE, GOOD]}
    if preferred:
        data[CONF_PREFERRED_SOURCE] = preferred
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    return entry


def _patched_bluetooth():
    return (
        patch(f"{BLUETOOTH}.async_address_present", return_value=True),
        patch(
            f"{BLUETOOTH}.async_scanner_by_source",
            return_value=SimpleNamespace(name="atomebuanderie"),
        ),
    )


async def _time_out(hass, entry, source=STALE) -> None:
    coordinator = _coordinator(hass, entry, _controller(_timeout_error({source: "timeout"})))
    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()


async def _succeed_via(hass, entry, source) -> None:
    controller = _controller()
    controller.connection.connected_source = source
    controller.connection.connection_status = ConnectionStatus.CONNECTED
    controller.refresh_status.return_value = {"set_point": {"cooling_set_point": 25}}
    coordinator = _coordinator(hass, entry, controller)
    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()


async def test_a_path_that_never_succeeds_is_eventually_dropped(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    for _ in range(BOND_STALE_TIMEOUTS):
        await _time_out(hass, entry)

    assert entry.data[CONF_BONDED_SOURCES] == [GOOD]


async def test_it_takes_the_whole_streak(hass: HomeAssistant) -> None:
    """One bad round is congestion; the claim is that it NEVER works."""
    entry = _entry(hass)
    for _ in range(BOND_STALE_TIMEOUTS - 1):
        await _time_out(hass, entry)

    assert entry.data[CONF_BONDED_SOURCES] == [STALE, GOOD]


async def test_a_single_success_on_that_path_saves_it(
    hass: HomeAssistant,
) -> None:
    """A congested but VALID bond gets through sooner or later, and that is
    the whole difference between it and a keyless path."""
    entry = _entry(hass)
    for _ in range(BOND_STALE_TIMEOUTS - 1):
        await _time_out(hass, entry)
    await _succeed_via(hass, entry, STALE)
    for _ in range(BOND_STALE_TIMEOUTS - 1):
        await _time_out(hass, entry)

    assert STALE in entry.data[CONF_BONDED_SOURCES]


async def test_a_success_elsewhere_does_not_save_it(hass: HomeAssistant) -> None:
    """Only a success on the ACCUSED path is an acquittal for it."""
    entry = _entry(hass)
    for _ in range(BOND_STALE_TIMEOUTS - 1):
        await _time_out(hass, entry)
    await _succeed_via(hass, entry, GOOD)
    await _time_out(hass, entry)

    assert entry.data[CONF_BONDED_SOURCES] == [GOOD]


async def test_the_last_known_bond_is_never_dropped(hass: HomeAssistant) -> None:
    """An empty list reads as "unrestricted" everywhere, so emptying it would
    switch the whole anti-prompt policy off instead of tightening it."""
    entry = _entry(hass, bonded=[STALE])
    for _ in range(BOND_STALE_TIMEOUTS * 2):
        await _time_out(hass, entry)

    assert entry.data[CONF_BONDED_SOURCES] == [STALE]


async def test_dropping_a_bond_drops_the_sticky_preference_with_it(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass, preferred=STALE)
    for _ in range(BOND_STALE_TIMEOUTS):
        await _time_out(hass, entry)

    assert CONF_PREFERRED_SOURCE not in entry.data


async def test_the_streak_is_cleared_once_it_has_been_acted_on(
    hass: HomeAssistant,
) -> None:
    """Otherwise the counter sits at the threshold and re-evicts every round."""
    entry = _entry(hass)
    for _ in range(BOND_STALE_TIMEOUTS):
        await _time_out(hass, entry)

    assert STALE not in async_pairing_state(hass, MAC).timeout_sources


async def test_an_unattributable_timeout_never_costs_anyone_a_bond(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    for _ in range(BOND_STALE_TIMEOUTS * 2):
        await _time_out(hass, entry, source=None)

    assert entry.data[CONF_BONDED_SOURCES] == [STALE, GOOD]


# --------------------------------------------------------------------------
# Congestion does not pick a favourite proxy
# --------------------------------------------------------------------------
#
# Field case, Salon, 2026-08-29. One proxy took every attempt of every round
# for seventeen hours and timed out on all of them, while the other proxies
# polled the same thermostat normally in between. The streak rule above never
# reached its threshold, because the honest reading of a timeout — congestion
# until proven otherwise — kept the bar deliberately high.
#
# But congestion is a property of the AIR, not of a proxy: it cannot make one
# path fail while another authenticates against the same device minutes apart.
# So a success ELSEWHERE, while this path's streak is running, removes the
# innocent explanation and the same evidence becomes conclusive sooner.
#
# The mirror of AUTH_CORROBORATION_WINDOW_S, which downgrades a refusal that a
# recent session contradicts. Here a contemporaneous success on another path
# upgrades a timeout streak instead.


async def test_a_corroborated_streak_is_conclusive_sooner(
    hass: HomeAssistant,
) -> None:
    """A success on another path rules out the congestion defence."""
    entry = _entry(hass)

    await _time_out(hass, entry)
    # The air is demonstrably fine: this proves it against the same device.
    await _succeed_via(hass, entry, GOOD)
    for _ in range(BOND_STALE_TIMEOUTS_CORROBORATED - 1):
        await _time_out(hass, entry)

    assert entry.data[CONF_BONDED_SOURCES] == [GOOD]


async def test_an_uncorroborated_streak_still_takes_the_long_road(
    hass: HomeAssistant,
) -> None:
    """The guard rail: with nothing to contradict it, a timeout is congestion.

    Without a contemporaneous success elsewhere, the short threshold must not
    apply — a proxy dropped on congestion alone costs a re-pair with a human at
    the thermostat, which is the asymmetry the long streak exists to respect.
    """
    entry = _entry(hass)

    for _ in range(BOND_STALE_TIMEOUTS_CORROBORATED):
        await _time_out(hass, entry)

    assert entry.data[CONF_BONDED_SOURCES] == [STALE, GOOD]


async def test_the_path_authenticating_itself_clears_the_corroboration(
    hass: HomeAssistant,
) -> None:
    """A path that authenticates has answered the accusation, whatever backed it.

    Corroboration is an argument ABOUT a streak, so it cannot outlive the
    streak it qualifies: this path just proved it holds a bond.
    """
    entry = _entry(hass)

    await _time_out(hass, entry)
    await _succeed_via(hass, entry, GOOD)
    await _succeed_via(hass, entry, STALE)

    for _ in range(BOND_STALE_TIMEOUTS_CORROBORATED):
        await _time_out(hass, entry)

    assert entry.data[CONF_BONDED_SOURCES] == [STALE, GOOD]
