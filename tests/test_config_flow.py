"""Tests for the daikin_madoka config flow."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.daikin_madoka.const import CONF_FRIENDLY_NAME, CONF_MAC, DOMAIN

MAC = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Setting up the bluetooth dependency schedules a device-expiry timer
    that outlives the test; it is HA's scanner bookkeeping, not ours."""
    return True


def _discovery_info() -> SimpleNamespace:
    """Minimal stand-in for BluetoothServiceInfoBleak (address + name only)."""
    return SimpleNamespace(address=MAC, name="Daikin")


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

    with patch(
        "custom_components.daikin_madoka.async_setup_entry", return_value=True
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
