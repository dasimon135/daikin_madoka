"""Tests for the daikin_madoka config flow."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.daikin_madoka.const import (
    CONF_FRIENDLY_NAME,
    CONF_MAC,
    CONF_PREFERRED_SOURCE,
    DOMAIN,
)

MAC = "AA:BB:CC:DD:EE:FF"
PROXY_SOURCE = "D0:CF:13:0F:11:F6"

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
