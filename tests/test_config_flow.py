"""Tests for the daikin_madoka config flow."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.daikin_madoka.config_flow import FlowHandler
from custom_components.daikin_madoka.const import (
    CONF_FRIENDLY_NAME,
    CONF_MAC,
    CONF_PREFERRED_SOURCE,
    DOMAIN,
)

MAC = "AA:BB:CC:DD:EE:FF"
OTHER_MAC = "AA:BB:CC:DD:EE:01"
PROXY_SOURCE = "D0:CF:13:0F:11:F6"
OTHER_SOURCE = "D0:CF:13:0F:11:F7"

VALIDATE = "custom_components.daikin_madoka.config_flow.FlowHandler._async_validate_device"
SETUP_ENTRY = "custom_components.daikin_madoka.async_setup_entry"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Setting up the bluetooth dependency schedules a device-expiry timer
    that outlives the test; it is HA's scanner bookkeeping, not ours."""
    return True


def _discovery_info(rssi: int = -60) -> SimpleNamespace:
    """Minimal stand-in for BluetoothServiceInfoBleak (address/name/rssi)."""
    return SimpleNamespace(address=MAC, name="Daikin", rssi=rssi)


async def test_user_flow_takes_over_pending_discovery_flow(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """A manual user flow must win over a pending Bluetooth discovery flow.

    Regression test: async_step_user used the raise_on_progress=True default
    of async_set_unique_id, so it aborted with already_in_progress whenever a
    discovery card for the same device was pending — making manual setup
    impossible until the discovery flow was dismissed.
    """
    discovery = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_discovery_info(),
    )
    # Discovery flow is now pending on its confirmation form.
    assert discovery["type"] is FlowResultType.FORM

    with (
        patch(SETUP_ENTRY, return_value=True),
        patch(VALIDATE, return_value=(None, PROXY_SOURCE)),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_MAC: MAC, CONF_FRIENDLY_NAME: "Salon"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MAC] == MAC
    # The pending discovery flow must be gone once the entry exists.
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_discovery_below_rssi_floor_aborts(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """A discovery advert weaker than the floor must not raise a card."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_discovery_info(rssi=-95),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "weak_signal"


async def test_discovery_at_rssi_floor_shows_confirm(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """The floor is strict: exactly -90 dBm still raises a discovery card."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_discovery_info(rssi=-90),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"


async def test_discovery_above_rssi_floor_shows_confirm(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """A usable-signal discovery proceeds to the confirmation form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_discovery_info(rssi=-85),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"


async def test_bluetooth_confirm_survives_validation_error(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """A failed validation re-shows the confirm form; a retry can still succeed."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_discovery_info(),
    )
    assert result["type"] is FlowResultType.FORM

    with patch(VALIDATE, return_value=("pairing_failed", None)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_FRIENDLY_NAME: "Salon"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["errors"] == {"base": "pairing_failed"}
    assert not hass.config_entries.async_entries(DOMAIN)

    # Second submit after the user confirmed the pairing prompt: succeeds.
    with (
        patch(SETUP_ENTRY, return_value=True),
        patch(VALIDATE, return_value=(None, PROXY_SOURCE)),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_FRIENDLY_NAME: "Salon"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MAC] == MAC
    assert result["data"][CONF_PREFERRED_SOURCE] == PROXY_SOURCE


async def test_user_flow_cannot_connect_then_success(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """User flow shows cannot_connect without creating an entry; retry works."""
    with patch(VALIDATE, return_value=("cannot_connect", None)):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_MAC: MAC, CONF_FRIENDLY_NAME: "Salon"},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}
    assert not hass.config_entries.async_entries(DOMAIN)

    with (
        patch(SETUP_ENTRY, return_value=True),
        patch(VALIDATE, return_value=(None, PROXY_SOURCE)),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MAC: MAC, CONF_FRIENDLY_NAME: "Salon"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PREFERRED_SOURCE] == PROXY_SOURCE


async def test_user_flow_local_adapter_omits_preferred_source(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """A validation served by the local adapter stores no preferred_source."""
    with (
        patch(SETUP_ENTRY, return_value=True),
        patch(VALIDATE, return_value=(None, None)),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_MAC: MAC, CONF_FRIENDLY_NAME: "Salon"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MAC] == MAC
    assert CONF_PREFERRED_SOURCE not in result["data"]


# --- Reconfigure flow ------------------------------------------------------


def _add_configured_entry(
    hass: HomeAssistant, mac: str = MAC, name: str = "Salon"
) -> MockConfigEntry:
    """Add a single-device entry the way _create_entry shapes it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MAC: mac,
            CONF_FRIENDLY_NAME: name,
            CONF_PREFERRED_SOURCE: PROXY_SOURCE,
        },
        unique_id=mac,
        title=name,
    )
    entry.add_to_hass(hass)
    return entry


async def test_reconfigure_rename_only_skips_validation(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """A friendly-name change must not require the device to be reachable."""
    entry = _add_configured_entry(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    with (
        patch(SETUP_ENTRY, return_value=True),
        patch(VALIDATE) as mock_validate,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MAC: MAC, CONF_FRIENDLY_NAME: "Buanderie"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    mock_validate.assert_not_called()
    assert entry.data[CONF_MAC] == MAC
    assert entry.data[CONF_FRIENDLY_NAME] == "Buanderie"
    # The sticky proxy still belongs to the same device: it must survive.
    assert entry.data[CONF_PREFERRED_SOURCE] == PROXY_SOURCE
    assert entry.title == "Buanderie"


async def test_reconfigure_mac_change_validates_and_resets_source(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """A MAC change is validated and replaces the stored sticky proxy."""
    entry = _add_configured_entry(hass)
    result = await entry.start_reconfigure_flow(hass)

    with (
        patch(SETUP_ENTRY, return_value=True),
        patch(VALIDATE, return_value=(None, OTHER_SOURCE)) as mock_validate,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_MAC: OTHER_MAC, CONF_FRIENDLY_NAME: "Salon"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    mock_validate.assert_called_once()
    assert entry.data[CONF_MAC] == OTHER_MAC
    assert entry.data[CONF_PREFERRED_SOURCE] == OTHER_SOURCE
    assert entry.unique_id == OTHER_MAC


async def test_reconfigure_mac_change_validation_failure_keeps_entry(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """A failed validation re-shows the form and leaves the entry untouched."""
    entry = _add_configured_entry(hass)
    result = await entry.start_reconfigure_flow(hass)

    with patch(VALIDATE, return_value=("cannot_connect", None)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_MAC: OTHER_MAC, CONF_FRIENDLY_NAME: "Salon"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data[CONF_MAC] == MAC


async def test_reconfigure_to_other_entry_mac_aborts(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """Pointing the entry at another entry's thermostat must abort."""
    entry = _add_configured_entry(hass)
    _add_configured_entry(hass, mac=OTHER_MAC, name="Buanderie")
    result = await entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MAC: OTHER_MAC, CONF_FRIENDLY_NAME: "Salon"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_MAC] == MAC


async def test_reconfigure_invalid_mac_shows_error(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """A malformed MAC re-shows the form with a field error."""
    entry = _add_configured_entry(hass)
    result = await entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MAC: "nonsense", CONF_FRIENDLY_NAME: "Salon"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MAC: "not_a_mac"}


async def test_reconfigure_legacy_multi_device_entry_aborts(
    hass: HomeAssistant,
    enable_bluetooth: None,
) -> None:
    """Legacy multi-MAC entries have no single identity to rewrite."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICES: [MAC, OTHER_MAC]})
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_legacy"


# --- Direct unit tests for FlowHandler._async_validate_device -------------
# The flow tests above patch the validator (plumbing); these execute its
# error-mapping logic against a stubbed pymadoka Controller. The import in
# the validator is function-local (`from pymadoka import Controller`), so
# the patch target is pymadoka.Controller itself.


def _stub_controller(status: object, source: str | None = None) -> MagicMock:
    """Controller stand-in: async start/stop, a connection with status/source."""
    controller = MagicMock()
    controller.start = AsyncMock()
    controller.stop = AsyncMock()
    controller.connection = SimpleNamespace(
        connection_status=status, connected_source=source
    )
    return controller


async def _validate_with(controller: MagicMock) -> tuple[str | None, str | None]:
    """Run _async_validate_device against a stubbed Controller class."""
    handler = FlowHandler()
    # The stub never touches hass (candidates_callback is never invoked).
    handler.hass = None
    with patch("pymadoka.Controller", return_value=controller):
        return await handler._async_validate_device(MAC)


async def test_validator_maps_pairing_refusal() -> None:
    """PairingRequiredError from start() maps to pairing_failed."""
    from pymadoka import ConnectionStatus, PairingRequiredError

    controller = _stub_controller(ConnectionStatus.ABORTED)
    controller.start.side_effect = PairingRequiredError("pairing refused")

    assert await _validate_with(controller) == ("pairing_failed", None)
    assert controller.stop.await_count == 1


async def test_validator_times_out_as_cannot_connect() -> None:
    """A start() that never finishes is cut at VALIDATE_TIMEOUT."""
    from pymadoka import ConnectionStatus

    controller = _stub_controller(ConnectionStatus.DISCONNECTED)

    async def _hang() -> None:
        await asyncio.sleep(999)

    controller.start = _hang

    with patch(
        "custom_components.daikin_madoka.config_flow.VALIDATE_TIMEOUT", 0.05
    ):
        assert await _validate_with(controller) == ("cannot_connect", None)
    assert controller.stop.await_count == 1


async def test_validator_rejects_aborted_without_raise() -> None:
    """start() returning normally with status ABORTED is NOT a success.

    pymadoka's connect loop stamps ABORTED on unclassified errors instead of
    raising, so the validator must check connection_status itself.
    """
    from pymadoka import ConnectionStatus

    controller = _stub_controller(ConnectionStatus.ABORTED)

    assert await _validate_with(controller) == ("cannot_connect", None)
    assert controller.stop.await_count == 1


async def test_validator_returns_connected_source_on_success() -> None:
    """A CONNECTED validation returns the serving proxy's source MAC."""
    from pymadoka import ConnectionStatus

    controller = _stub_controller(ConnectionStatus.CONNECTED, source=PROXY_SOURCE)

    assert await _validate_with(controller) == (None, PROXY_SOURCE)
    assert controller.stop.await_count == 1
