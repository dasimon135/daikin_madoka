"""Which proxy keeps timing out? Count it, per source.

`auth_failures` counts PROVEN refusals per proxy, which is what the eviction
bookkeeping needs. Nothing counts timeouts per proxy — and timeouts are what
actually happens in the field: over two days of logs, every pairing failure on
this maintainer's install was a timeout and not one was a refusal.

That gap had a cost. Answering "which bond looks dead on Manon?" meant grepping
two days of raw log lines and joining them against bonded_sources by hand, and
the answer (six of its seven timeouts came through a proxy it IS bonded with)
was invisible from diagnostics alone.

Purely observational: nothing here changes a cadence, a verdict or a bond. It
exists so the NEXT decision — whether a timing-out bond is dead or the proxy is
merely congested — can be made from recorded evidence instead of archaeology.
Deliberately so, because acting on it automatically is now dangerous: since the
allowed-source veto landed, dropping a proxy from bonded_sources stops it being
paired on at all, so a wrong eviction costs a trip to the thermostat.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka import PairingRequiredError
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.daikin_madoka.const import (
    CONF_BONDED_SOURCES,
    CONF_MAC,
    CONF_PAIRING_STATE,
    DOMAIN,
)
from custom_components.daikin_madoka.coordinator import (
    MadokaCoordinator,
    MadokaPairingState,
    async_pairing_state,
)

MAC = "D0:CF:13:0F:11:F6"
BUSY = "AA:BB:CC:11:22:33"
GOOD = "DD:EE:FF:44:55:66"

BLUETOOTH = "homeassistant.components.bluetooth"


def _timeout_error(evidence: dict) -> PairingRequiredError:
    return PairingRequiredError(
        MAC,
        list(evidence),
        reason="timeout_streak",
        timeout_rounds=3,
        evidence=evidence,
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


def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, controller: MagicMock
) -> MadokaCoordinator:
    token = config_entries.current_entry.set(entry)
    try:
        return MadokaCoordinator(hass, controller, scan_interval=60)
    finally:
        config_entries.current_entry.reset(token)


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: MAC, CONF_BONDED_SOURCES: [BUSY, GOOD]},
    )
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


async def _fail(hass, entry, evidence: dict) -> MadokaCoordinator:
    coordinator = _coordinator(hass, entry, _controller(_timeout_error(evidence)))
    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()
    return coordinator


async def test_a_timeout_is_counted_against_the_path_it_happened_on(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    await _fail(hass, entry, {BUSY: "timeout"})

    assert async_pairing_state(hass, MAC).timeout_sources == {BUSY: 1}


async def test_timeouts_accumulate_per_source(hass: HomeAssistant) -> None:
    """The signal is a streak on ONE proxy, not a total across all of them."""
    entry = _entry(hass)
    await _fail(hass, entry, {BUSY: "timeout"})
    await _fail(hass, entry, {BUSY: "timeout", GOOD: "timeout"})

    assert async_pairing_state(hass, MAC).timeout_sources == {BUSY: 2, GOOD: 1}


async def test_only_timeouts_are_counted(hass: HomeAssistant) -> None:
    """A refusal is already counted by auth_failures, and a transient failure
    is not evidence about pairing at all."""
    entry = _entry(hass)
    await _fail(hass, entry, {BUSY: "transient", GOOD: "rejected"})

    assert async_pairing_state(hass, MAC).timeout_sources == {}


async def test_an_unattributable_round_is_charged_to_nobody(
    hass: HomeAssistant,
) -> None:
    """pymadoka keys a path it could not prove under None; counting that would
    invent a proxy that was never in the conversation."""
    entry = _entry(hass)
    await _fail(hass, entry, {None: "timeout"})

    assert async_pairing_state(hass, MAC).timeout_sources == {}


async def test_a_successful_session_clears_that_path_only(
    hass: HomeAssistant,
) -> None:
    """A streak has to be CONSECUTIVE to mean anything — the same rule
    auth_failures already follows."""
    entry = _entry(hass)
    await _fail(hass, entry, {BUSY: "timeout", GOOD: "timeout"})

    controller = _controller()
    controller.connection.connected_source = GOOD
    controller.connection.connection_status = ConnectionStatus.CONNECTED
    # A poll that answers nothing is a FAILED poll ("did not answer any
    # query"), which never reaches the acquittal.
    controller.refresh_status.return_value = {
        "set_point": {"cooling_set_point": 25}
    }
    coordinator = _coordinator(hass, entry, controller)
    present, scanner = _patched_bluetooth()
    with present, scanner:
        await coordinator.async_refresh()

    assert async_pairing_state(hass, MAC).timeout_sources == {BUSY: 1}


def test_the_counts_survive_a_restart() -> None:
    """A diagnosis reached at 2am must not have to be re-derived."""
    state = MadokaPairingState(timeout_sources={BUSY: 2})
    restored = MadokaPairingState.from_stored(MAC, state.as_stored())

    assert restored.timeout_sources == {BUSY: 2}


def test_a_clean_device_stores_nothing() -> None:
    assert MadokaPairingState().as_stored() is None


async def test_the_counts_reach_diagnostics(hass: HomeAssistant) -> None:
    """Resolved to proxy names: the whole point is reading it without a
    MAC-to-name lookup table in your head."""
    from custom_components.daikin_madoka.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = _entry(hass)
    await _fail(hass, entry, {BUSY: "timeout"})

    present, scanner = _patched_bluetooth()
    with present, scanner:
        payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["pairing_state"]["timeout_sources"] == {"atomebuanderie": 1}


async def test_the_persisted_copy_follows_the_live_one(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    await _fail(hass, entry, {BUSY: "timeout"})

    assert entry.data[CONF_PAIRING_STATE][MAC]["timeout_sources"] == {BUSY: 1}
