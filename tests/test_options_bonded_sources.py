"""The options let a user take a proxy off a thermostat's bonded list.

CONF_BONDED_SOURCES records the proxies a session once authenticated
through, and the pairing veto trusts it. When a proxy loses the key (an
ESP-IDF bug wiped them on 2026-11-05, a reflash does it too), the entry still
vouches for it, and every unattended attempt through it puts a pairing code
on the thermostat screen until the eviction streak concludes. On 2026-11-11
that cost Parents about ten prompts in ten minutes, while BlueSight showed
plainly which proxy held which key. The user knew; nothing let them say so.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.daikin_madoka.const import (
    CONF_BONDED_SOURCES,
    CONF_ENABLE_ENERGY,
    CONF_FRIENDLY_NAME,
    CONF_MAC,
    CONF_PREFERRED_SOURCE,
    DOMAIN,
)

MAC = "AA:BB:CC:DD:EE:FF"
KEPT = "D0:CF:13:0F:05:5A"
STALE = "D0:CF:13:0E:C9:2A"

SCANNER = "custom_components.daikin_madoka.config_flow.async_scanner_by_source"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Setting up the bluetooth dependency schedules a device-expiry timer
    that outlives the test; it is HA's scanner bookkeeping, not ours."""
    return True


def _entry(hass: HomeAssistant, bonded: list[str], preferred: str = KEPT):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MAC: MAC,
            CONF_FRIENDLY_NAME: "Parents",
            CONF_PREFERRED_SOURCE: preferred,
            CONF_BONDED_SOURCES: bonded,
        },
        options={CONF_SCAN_INTERVAL: 120},
        unique_id=MAC,
        title="Parents",
    )
    entry.add_to_hass(hass)
    return entry


def _named(_hass, source):
    return SimpleNamespace(name={KEPT: "atomebuanderie", STALE: "atomeparents"}[source])


async def _submit(hass, entry, bonded):
    with patch(SCANNER, side_effect=_named):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        return result, await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_SCAN_INTERVAL: 120, CONF_ENABLE_ENERGY: False, CONF_BONDED_SOURCES: bonded},
        )


async def test_the_form_offers_every_listed_proxy_ticked(hass: HomeAssistant) -> None:
    entry = _entry(hass, [KEPT, STALE])

    with patch(SCANNER, side_effect=_named):
        result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["data_schema"]({})[CONF_BONDED_SOURCES] == [KEPT, STALE]


async def test_unticking_a_proxy_takes_it_off_the_list(hass: HomeAssistant) -> None:
    entry = _entry(hass, [KEPT, STALE])

    _, result = await _submit(hass, entry, [KEPT])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_BONDED_SOURCES] == [KEPT]
    # The list lives in the entry data, where the veto reads it, not in options.
    assert CONF_BONDED_SOURCES not in entry.options
    assert entry.options[CONF_SCAN_INTERVAL] == 120


async def test_unticking_the_preferred_proxy_drops_the_preference(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass, [KEPT, STALE], preferred=STALE)

    await _submit(hass, entry, [KEPT])

    assert CONF_PREFERRED_SOURCE not in entry.data


async def test_the_list_can_never_be_emptied(hass: HomeAssistant) -> None:
    """Empty reads as "unrestricted": it would switch the veto off."""
    entry = _entry(hass, [KEPT, STALE])

    _, result = await _submit(hass, entry, [])

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_BONDED_SOURCES: "keep_one_proxy"}
    assert entry.data[CONF_BONDED_SOURCES] == [KEPT, STALE]


async def test_a_single_listed_proxy_offers_nothing_to_untick(
    hass: HomeAssistant,
) -> None:
    """With one proxy the only choice would be the forbidden one."""
    entry = _entry(hass, [KEPT])

    with patch(SCANNER, side_effect=_named):
        result = await hass.config_entries.options.async_init(entry.entry_id)

    assert CONF_BONDED_SOURCES not in result["data_schema"]({})
