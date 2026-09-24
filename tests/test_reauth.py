"""Recovery from a proven pairing refusal, without needing any entity.

The Reconnect button was the integration's only escape hatch, and it lives on
the device: when a thermostat produced no entities it produced no button
either, and even when it did, pressing it was fire-and-forget - the user got
no feedback at all about whether the re-pairing had worked.

async_step_reauth is Home Assistant's canonical answer to "our credentials are
no longer accepted": HA renders a Fix button on the config entry itself, which
works with zero entities, survives restarts, and gives the user a form that
reports success or failure.

The trigger is deliberately ConfigEntry.async_start_reauth and NOT raising
ConfigEntryAuthFailed - see MadokaCoordinator._async_start_reauth for why
(the first refresh re-raises it, which would send the entry to SETUP_ERROR and
stop the coordinator rescheduling itself).
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymadoka import ConnectionException, PairingRequiredError
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.daikin_madoka.const import (
    CONF_BONDED_SOURCES,
    CONF_MAC,
    CONF_PAIRING_STATE,
    CONF_PREFERRED_SOURCE,
    DOMAIN,
    TIMEOUT_BACKOFF_INTERVAL_S,
)
from custom_components.daikin_madoka.coordinator import (
    UNREACHABLE_THRESHOLD,
    MadokaCoordinator,
    async_pairing_state,
)

MAC = "D0:CF:13:0F:11:F6"
SOURCE = "AA:BB:CC:11:22:33"
OTHER_SOURCE = "AA:BB:CC:11:22:44"
BLUETOOTH = "homeassistant.components.bluetooth"
VALIDATE = (
    "custom_components.daikin_madoka.config_flow.FlowHandler._async_validate_device"
)
SETUP_ENTRY = "custom_components.daikin_madoka.async_setup_entry"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """HA's own bluetooth scanner expiry timer (same waiver as elsewhere)."""
    return True


def _controller(rounds: int = 0) -> MagicMock:
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.connection.connected_source = SOURCE
    controller.connection.pairing_timeout_rounds = rounds
    controller.update = AsyncMock()
    controller.refresh_status.return_value = {"set_point": {"cooling_set_point": 25}}
    return controller


def _entry(
    hass: HomeAssistant, loaded: bool = True, **data
) -> MockConfigEntry:
    """A configured entry. Since P1.1 a configured device always loads, so
    LOADED is the state a polling coordinator is really in - and the state the
    reauth trigger requires."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_MAC: MAC, **data}, unique_id=MAC, title="Salon"
    )
    entry.add_to_hass(hass)
    if loaded:
        entry.mock_state(hass, config_entries.ConfigEntryState.LOADED)
    return entry


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
            return_value=SimpleNamespace(name="Proxy"),
        ),
    )


def _reauth_flows(hass: HomeAssistant) -> list[dict]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"].get("source") == config_entries.SOURCE_REAUTH
    ]


# --------------------------------------------------------------------------
# 1. The trigger: a PROVEN rejection, and only that
# --------------------------------------------------------------------------


async def test_proven_rejection_starts_a_reauth_flow(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """rounds == 0 is pymadoka's side channel for an explicit refusal."""
    entry = _entry(hass)
    controller = _controller(rounds=0)
    controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE]))
    coordinator = _coordinator(hass, entry, controller)
    present, scanner = _patched_bluetooth()

    with present, scanner:
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    flows = _reauth_flows(hass)
    assert len(flows) == 1
    assert flows[0]["step_id"] == "reauth_confirm"


async def test_timeout_streak_does_not_start_a_reauth_flow(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """A streak proves nothing; asking the user to walk over would be a lie."""
    entry = _entry(hass)
    controller = _controller(rounds=3)
    # Explicit since the 0.3.10 pin: PairingRequiredError defaults to
    # reason="rejected", so a streak must say so or it reads as a refusal.
    controller.start = AsyncMock(
        side_effect=PairingRequiredError(
            MAC, [SOURCE], reason="timeout_streak", timeout_rounds=3
        )
    )
    coordinator = _coordinator(hass, entry, controller)
    present, scanner = _patched_bluetooth()

    with present, scanner:
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _reauth_flows(hass) == []


async def test_the_entry_stays_loaded_while_reauth_is_pending(
    hass: HomeAssistant, mock_bluetooth
) -> None:
    """The whole point of not raising ConfigEntryAuthFailed.

    ConfigEntryAuthFailed out of the FIRST refresh is re-raised by HA
    (raise_on_auth_failed=True), which would mark the entry SETUP_ERROR and
    leave the device with no entities at all - the catch-22 again - and would
    also stop the coordinator rescheduling itself.
    """
    # Real setup: HA moves the entry through SETUP_IN_PROGRESS itself, which is
    # the state the first refresh runs in.
    entry = _entry(hass, loaded=False)
    controller = _controller(rounds=0)
    controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE]))
    controller.stop = AsyncMock()
    controller.read_info = AsyncMock()
    controller.info = {}

    with (
        patch(
            "custom_components.daikin_madoka.Controller",
            MagicMock(return_value=controller),
        ),
        patch("custom_components.daikin_madoka.async_register_card", AsyncMock()),
        patch("custom_components.daikin_madoka.COMPONENT_TYPES", ["button"]),
        patch(f"{BLUETOOTH}.async_address_present", return_value=True),
        patch(
            f"{BLUETOOTH}.async_scanner_by_source",
            return_value=SimpleNamespace(name="Proxy"),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert len(_reauth_flows(hass)) == 1

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# --------------------------------------------------------------------------
# 2. The flow itself
# --------------------------------------------------------------------------


async def test_reauth_happy_path_clears_the_quarantine(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """A successful re-pairing must leave no trace of the verdict behind."""
    entry = _entry(hass, **{CONF_PAIRING_STATE: {MAC: {"suspended": True}}})
    state = async_pairing_state(hass, MAC)
    state.suspended = True
    state.fail_count = 9

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["name"] == "Salon"

    with (
        patch(SETUP_ENTRY, return_value=True),
        patch(VALIDATE, return_value=(None, SOURCE)) as validate,
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    validate.assert_called_once_with(MAC)
    # The proxy that just completed the pairing is now a proven bonded path.
    assert entry.data[CONF_PREFERRED_SOURCE] == SOURCE
    assert entry.data[CONF_BONDED_SOURCES] == [SOURCE]
    # ...and nothing survives of the quarantine, in memory or on disk.
    assert CONF_PAIRING_STATE not in entry.data
    assert async_pairing_state(hass, MAC).suspended is False
    assert async_pairing_state(hass, MAC).fail_count == 0


async def test_reauth_failure_reports_it_in_the_form(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """Unlike the fire-and-forget button, the user is told what happened."""
    entry = _entry(hass, **{CONF_PAIRING_STATE: {MAC: {"suspended": True}}})
    async_pairing_state(hass, MAC).suspended = True

    result = await entry.start_reauth_flow(hass)

    with patch(VALIDATE, return_value=("pairing_failed", None)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "pairing_failed"}
    # A failed attempt proves nothing new: the quarantine stands.
    assert async_pairing_state(hass, MAC).suspended is True
    assert entry.data[CONF_PAIRING_STATE][MAC]["suspended"] is True

    # ...and the user can walk over and try again without restarting anything.
    with (
        patch(SETUP_ENTRY, return_value=True),
        patch(VALIDATE, return_value=(None, SOURCE)),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def _brake(hass: HomeAssistant, coordinator: MadokaCoordinator) -> None:
    """Drive the coordinator into the retry brake through failed polls."""
    coordinator.controller.start = AsyncMock(side_effect=ConnectionException("silent"))
    present, scanner = _patched_bluetooth()
    with present, scanner:
        for _ in range(UNREACHABLE_THRESHOLD):
            await coordinator.async_refresh()
    assert coordinator.update_interval == timedelta(seconds=TIMEOUT_BACKOFF_INTERVAL_S)


async def test_reauth_submission_lifts_the_brake_and_retries_immediately(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """The user is standing at the thermostat; a 900s timer is not an answer.

    Same field report as the Reconnect button (2026-07-26): the recovery
    action a repair sends the user to must produce an attempt NOW, and must
    leave the device on its configured cadence afterwards rather than back
    under the brake it was under before anyone intervened.
    """
    entry = _entry(hass)
    controller = _controller(rounds=0)
    coordinator = _coordinator(hass, entry, controller)
    entry.runtime_data = {MAC: coordinator}
    await _brake(hass, coordinator)
    attempts = controller.start.await_count

    result = await entry.start_reauth_flow(hass)
    present, scanner = _patched_bluetooth()
    with present, scanner, patch(VALIDATE, return_value=("cannot_connect", None)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    # The re-pairing failed and is reported, so the flow stays open...
    assert result["errors"] == {"base": "cannot_connect"}
    # ...but the brake is gone and the coordinator tried again right away,
    # rather than at the next 900s tick.
    assert coordinator.pairing_backoff is False
    assert coordinator.update_interval == timedelta(seconds=60)
    assert controller.start.await_count > attempts

    await coordinator.async_shutdown()


async def test_reauth_success_leaves_no_brake_behind(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """A reload rebuilds the coordinator from the entry: it must be clean."""
    entry = _entry(hass)
    controller = _controller(rounds=0)
    coordinator = _coordinator(hass, entry, controller)
    entry.runtime_data = {MAC: coordinator}
    await _brake(hass, coordinator)

    result = await entry.start_reauth_flow(hass)
    with (
        patch(SETUP_ENTRY, return_value=True),
        patch(VALIDATE, return_value=(None, SOURCE)),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert CONF_PAIRING_STATE not in entry.data
    assert async_pairing_state(hass, MAC).backoff is False

    await coordinator.async_shutdown()


async def test_reauth_keeps_other_bonded_proxies(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """Re-pairing one proxy must not forget the ones that still work."""
    entry = _entry(hass, **{CONF_BONDED_SOURCES: [OTHER_SOURCE]})

    result = await entry.start_reauth_flow(hass)
    with (
        patch(SETUP_ENTRY, return_value=True),
        patch(VALIDATE, return_value=(None, SOURCE)),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert entry.data[CONF_BONDED_SOURCES] == [OTHER_SOURCE, SOURCE]


async def test_reauth_via_local_adapter_stores_no_source(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """None means the local adapter: there is no proxy bond to record."""
    entry = _entry(hass)

    result = await entry.start_reauth_flow(hass)
    with (
        patch(SETUP_ENTRY, return_value=True),
        patch(VALIDATE, return_value=(None, None)),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert CONF_PREFERRED_SOURCE not in entry.data
    assert CONF_BONDED_SOURCES not in entry.data


async def test_reauth_aborts_on_a_legacy_multi_device_entry(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """A legacy entry has no single thermostat to stand in front of."""
    from homeassistant.const import CONF_DEVICES

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICES: [MAC]})
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_legacy"


async def test_no_reauth_flow_for_an_entry_that_is_not_running(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """A prompt must not outlive the entry it belongs to.

    Since the unconditional degraded load a configured device is always
    LOADED (or SETUP_IN_PROGRESS during its first refresh), so any other state
    means the entry is on its way out - being unloaded, reloaded or removed -
    and asking the user to walk to the thermostat for it would be noise.
    """
    entry = _entry(hass, loaded=False)
    controller = _controller(rounds=0)
    controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE]))
    coordinator = _coordinator(hass, entry, controller)
    present, scanner = _patched_bluetooth()

    with present, scanner:
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _reauth_flows(hass) == []
    # The quarantine itself is still recorded: only the prompt is withheld.
    assert async_pairing_state(hass, MAC).suspended is True


async def test_reauth_reloads_without_the_deprecated_helper(
    hass: HomeAssistant,
    enable_bluetooth: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """async_update_reload_and_abort warns for an entry with an update listener
    since HA 2026.9 and is announced to break in 2026.12; the Fix button this
    flow sits behind is the one recovery path that needs no entities."""
    entry = _entry(hass)
    entry.add_update_listener(AsyncMock())
    result = await entry.start_reauth_flow(hass)

    with (
        patch(SETUP_ENTRY, return_value=True),
        patch(VALIDATE, return_value=(None, SOURCE)),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    reload.assert_called_once_with(entry.entry_id)
    assert "should use it for scheduling a reload" not in caplog.text
