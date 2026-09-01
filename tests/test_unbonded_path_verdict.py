"""Tier 4: Home Assistant only ever routed us somewhere we may not pair.

pymadoka >= 0.3.12 can refuse to pair on a path the integration did not
sanction, because filtering the CANDIDATE list cannot control where the
connection lands (habluetooth keeps only the address and re-scores every path
itself). When every path it chose was unsanctioned for three consecutive
rounds it says so with reason="unbonded_path".

That verdict is unlike the other two. Nothing was refused and nothing timed
out — pair() was never called, which is the entire point: the thermostat
screen stayed dark. So it must convict nobody and evict no bond. But it also
cannot be ignored: only a human can pair the proxy the connection keeps
landing on, so it earns a repair, a slow cadence and a Fix button.

Field case behind it (2026-08-27): the Salon thermostat put eight pairing
prompts on its screen in one hour via a proxy that was not in its
bonded_sources at all.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymadoka import PairingRequiredError
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.daikin_madoka.const import (
    CONF_BONDED_SOURCES,
    CONF_MAC,
    DOMAIN,
    TIMEOUT_BACKOFF_INTERVAL_S,
)
from custom_components.daikin_madoka.coordinator import (
    BACKOFF_UNBONDED_PATH,
    MadokaCoordinator,
    async_pairing_state,
)

MAC = "D0:CF:13:0F:11:F6"
BONDED = "AA:BB:CC:11:22:33"
LANDED_ON = "DD:EE:FF:44:55:66"

BLUETOOTH = "homeassistant.components.bluetooth"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Same waiver as the other entry-setup suites: HA's bluetooth scanner
    schedules a device-expiry timer that outlives the test."""
    return True


def _error() -> PairingRequiredError:
    """What the library raises after UNBONDED_PATH_ROUNDS such rounds."""
    return PairingRequiredError(
        MAC,
        [LANDED_ON],
        reason="unbonded_path",
        timeout_rounds=3,
        evidence={LANDED_ON: "unbonded"},
    )


def _controller() -> MagicMock:
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.connection.connected_source = None
    controller.connection.pair_timeout = 8.0
    controller.connection.pairing_timeout_rounds = 0
    controller.info = {}
    controller.start = AsyncMock(side_effect=_error())
    controller.stop = AsyncMock()
    controller.read_info = AsyncMock()
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
        data={CONF_MAC: MAC, CONF_BONDED_SOURCES: [BONDED]},
        unique_id=MAC,
        title="Salon",
    )
    entry.add_to_hass(hass)
    return entry


def _patched_bluetooth():
    return (
        patch(f"{BLUETOOTH}.async_address_present", return_value=True),
        patch(
            f"{BLUETOOTH}.async_scanner_by_source",
            return_value=SimpleNamespace(name="atomesalon"),
        ),
    )


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry):
    """One failing poll. Returns (coordinator, the patched reauth trigger).

    The trigger is patched rather than left to run: opening a real flow
    schedules a background task that outlives the test and makes teardown
    intermittently fail, and HA's flow machinery is not what any of these
    tests is about — the routing decision is.

    The entry is marked LOADED because that is the state a polling coordinator
    is really in, and the recovery affordances check for it.
    """
    entry.mock_state(hass, config_entries.ConfigEntryState.LOADED)
    coordinator = _coordinator(hass, entry, _controller())
    present, scanner = _patched_bluetooth()
    with present, scanner, patch.object(
        MadokaCoordinator, "_async_start_reauth", autospec=True
    ) as start_reauth:
        await coordinator.async_refresh()
    return coordinator, start_reauth


# --------------------------------------------------------------------------
# It convicts nobody
# --------------------------------------------------------------------------


async def test_unbonded_path_does_not_suspend_reconnects(
    hass: HomeAssistant,
) -> None:
    """Suspension is for a PROVEN refusal. Nothing was refused here."""
    entry = _entry(hass)
    await _refresh(hass, entry)

    assert async_pairing_state(hass, MAC).suspended is False


async def test_unbonded_path_never_evicts_a_bond(hass: HomeAssistant) -> None:
    """The landed-on proxy holds no bond BY DEFINITION — that is why it was
    skipped. Charging it a refusal would be recording evidence we refused to
    collect, and could evict a perfectly good bond elsewhere."""
    entry = _entry(hass)
    await _refresh(hass, entry)

    assert entry.data[CONF_BONDED_SOURCES] == [BONDED]
    assert async_pairing_state(hass, MAC).auth_failures == {}


async def test_unbonded_path_does_not_raise_the_refusal_repair(
    hass: HomeAssistant,
) -> None:
    """`pairing_required` says the thermostat refused the bond. It did not."""
    entry = _entry(hass)
    await _refresh(hass, entry)

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"pairing_required_{MAC}") is None
    assert registry.async_get_issue(DOMAIN, f"pairing_slow_{MAC}") is None


# --------------------------------------------------------------------------
# ...but it is not ignored either
# --------------------------------------------------------------------------


async def test_unbonded_path_raises_its_own_repair_naming_the_proxy(
    hass: HomeAssistant,
) -> None:
    """The proxy name is the whole actionable content: go pair with THAT one."""
    entry = _entry(hass)
    await _refresh(hass, entry)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"unbonded_path_{MAC}")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_placeholders["proxies"] == "atomesalon"


async def test_unbonded_path_slows_the_poll_cadence(hass: HomeAssistant) -> None:
    """Retries are cheap (no SMP) but futile while the scoring stands."""
    entry = _entry(hass)
    coordinator, _ = await _refresh(hass, entry)

    assert coordinator.update_interval == timedelta(
        seconds=TIMEOUT_BACKOFF_INTERVAL_S
    )
    assert async_pairing_state(hass, MAC).backoff_reason == BACKOFF_UNBONDED_PATH


async def test_unbonded_path_offers_a_way_out(hass: HomeAssistant) -> None:
    """Only a human can fix this, so give them the button that does it.

    Asserted on the coordinator's own call rather than on a materialised flow:
    whether HA then opens one depends on the entry state its guard checks, and
    that machinery is already covered per-tier in test_reauth.py. What belongs
    here is the routing decision — that THIS verdict, unlike the timeout tier,
    reaches for the reauth affordance at all.
    """
    entry = _entry(hass)
    _, start_reauth = await _refresh(hass, entry)

    start_reauth.assert_called_once()
