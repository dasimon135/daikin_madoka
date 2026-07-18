"""Tests for the coordinator: sticky-proxy persistence, repairs, stale grace."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka import ConnectionException, PairingRequiredError
from pymadoka.connection import ConnectionStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.daikin_madoka.const import (
    CONF_FRIENDLY_NAME,
    CONF_MAC,
    CONF_PREFERRED_SOURCE,
    DOMAIN,
)
from custom_components.daikin_madoka.coordinator import MadokaCoordinator

MAC = "D0:CF:13:0F:11:F6"
OTHER_MAC = "D0:CF:13:0F:11:F7"
SOURCE = "AA:BB:CC:11:22:33"

BLUETOOTH = "homeassistant.components.bluetooth"


def _mock_controller(source: str | None = SOURCE) -> MagicMock:
    """Controller stub whose poll succeeds without touching BLE."""
    controller = MagicMock()
    controller.connection.address = MAC
    controller.connection.name = "Daikin"
    controller.connection.connection_status = ConnectionStatus.CONNECTED
    controller.connection.connected_source = source
    controller.update = AsyncMock()
    controller.refresh_status.return_value = {"set_point": {"cooling_set_point": 25}}
    return controller


def _coordinator(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    controller: MagicMock,
    friendly_name: str | None = None,
) -> MadokaCoordinator:
    """Build a coordinator bound to the entry the way HA does during setup.

    Production code relies on DataUpdateCoordinator picking the entry up from
    the current_entry ContextVar that HA sets around async_setup_entry.
    """
    token = config_entries.current_entry.set(entry)
    try:
        return MadokaCoordinator(
            hass, controller, scan_interval=60, friendly_name=friendly_name
        )
    finally:
        config_entries.current_entry.reset(token)


async def test_successful_poll_persists_preferred_source(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_MAC: MAC, CONF_FRIENDLY_NAME: "Salon"}
    )
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass, entry, _mock_controller())

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert entry.data[CONF_PREFERRED_SOURCE] == SOURCE


async def test_unchanged_source_does_not_rewrite_entry(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass, entry, _mock_controller())

    with patch.object(
        hass.config_entries,
        "async_update_entry",
        wraps=hass.config_entries.async_update_entry,
    ) as mock_update:
        await coordinator.async_refresh()
        assert mock_update.call_count == 1

        await coordinator.async_refresh()
        # Same winning proxy: rewriting the entry would only churn storage
        # and re-fire the update listener for nothing.
        assert mock_update.call_count == 1

    assert entry.data[CONF_PREFERRED_SOURCE] == SOURCE


async def test_legacy_multi_device_entry_is_not_persisted(
    hass: HomeAssistant,
) -> None:
    # Legacy entries share one entry between several thermostats; a single
    # preferred_source cannot be right for all of them, so none is stored.
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICES: [MAC, OTHER_MAC]})
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass, entry, _mock_controller())

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert CONF_PREFERRED_SOURCE not in entry.data


async def test_unknown_source_is_not_persisted(hass: HomeAssistant) -> None:
    # connected_source is None for the local adapter / unknown backends;
    # storing it would clobber a valid sticky proxy with nothing.
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_MAC: MAC, CONF_PREFERRED_SOURCE: SOURCE}
    )
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass, entry, _mock_controller(source=None))

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert entry.data[CONF_PREFERRED_SOURCE] == SOURCE


async def test_pairing_refusal_raises_repair_issue(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_MAC: MAC, CONF_FRIENDLY_NAME: "Salon"}
    )
    entry.add_to_hass(hass)
    controller = _mock_controller()
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE, None]))
    # friendly_name is what production setup passes from the entry; without it
    # device_name falls back to the advertised connection name ("Daikin").
    coordinator = _coordinator(hass, entry, controller, friendly_name="Salon")

    with (
        patch(f"{BLUETOOTH}.async_address_present", return_value=True),
        patch(
            f"{BLUETOOTH}.async_scanner_by_source",
            return_value=SimpleNamespace(name="Proxy Salon"),
        ),
    ):
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"pairing_required_{MAC}")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_placeholders == {
        "device": "Salon",
        "proxies": "Proxy Salon, local adapter",
    }


async def test_successful_poll_clears_issues(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    controller = _mock_controller()
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE]))
    coordinator = _coordinator(hass, entry, controller)

    with (
        patch(f"{BLUETOOTH}.async_address_present", return_value=True),
        patch(
            f"{BLUETOOTH}.async_scanner_by_source",
            return_value=SimpleNamespace(name="Proxy Salon"),
        ),
    ):
        await coordinator.async_refresh()
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"pairing_required_{MAC}") is not None

    # An unreachable issue persisted from before a restart must clear too,
    # even though this coordinator instance never raised it.
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"unreachable_{MAC}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="device_unreachable",
    )

    controller.connection.connection_status = ConnectionStatus.CONNECTED
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert registry.async_get_issue(DOMAIN, f"pairing_required_{MAC}") is None
    assert registry.async_get_issue(DOMAIN, f"unreachable_{MAC}") is None


async def test_stale_grace_masks_transient_failures(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    controller = _mock_controller()
    coordinator = _coordinator(hass, entry, controller)

    await coordinator.async_refresh()
    assert coordinator.last_update_success
    good_data = coordinator.data

    controller.update = AsyncMock(side_effect=ConnectionException("transient drop"))

    # Failures 1 and 2 are masked: entities keep the last good data.
    for _ in range(2):
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert coordinator.data == good_data

    # Failure 3 exceeds the grace and propagates.
    await coordinator.async_refresh()
    assert not coordinator.last_update_success


async def test_pairing_failure_is_never_masked_by_grace(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    controller = _mock_controller()
    coordinator = _coordinator(hass, entry, controller)

    await coordinator.async_refresh()
    # Grace precondition holds (existing data, first failure), yet a pairing
    # refusal must still surface immediately: it never heals on its own.
    assert coordinator.data

    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE]))

    with (
        patch(f"{BLUETOOTH}.async_address_present", return_value=True),
        patch(
            f"{BLUETOOTH}.async_scanner_by_source",
            return_value=SimpleNamespace(name="Proxy Salon"),
        ),
    ):
        await coordinator.async_refresh()

    assert not coordinator.last_update_success
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"pairing_required_{MAC}")
    assert issue is not None


async def test_sustained_pairing_refusal_suppresses_unreachable_issue(
    hass: HomeAssistant,
) -> None:
    # A refused bond also trips the failure threshold; showing the
    # unreachable WARNING (power/range advice) next to the pairing ERROR
    # would be contradictory, so only the pairing repair may appear.
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    controller = _mock_controller()
    controller.connection.connection_status = ConnectionStatus.DISCONNECTED
    controller.start = AsyncMock(side_effect=PairingRequiredError(MAC, [SOURCE]))
    coordinator = _coordinator(hass, entry, controller)

    with (
        patch(f"{BLUETOOTH}.async_address_present", return_value=True),
        patch(
            f"{BLUETOOTH}.async_scanner_by_source",
            return_value=SimpleNamespace(name="Proxy Salon"),
        ),
    ):
        for _ in range(5):
            await coordinator.async_refresh()

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"pairing_required_{MAC}") is not None
    assert registry.async_get_issue(DOMAIN, f"unreachable_{MAC}") is None


async def test_grace_then_threshold_state_machine(hass: HomeAssistant) -> None:
    # The full walk from healthy to unreachable proves the grace did not
    # break the threshold: failures 1-2 masked, 3-4 unavailable, 5 raises
    # the unreachable repair.
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    controller = _mock_controller()
    coordinator = _coordinator(hass, entry, controller)

    await coordinator.async_refresh()
    assert coordinator.last_update_success
    good_data = coordinator.data

    controller.update = AsyncMock(side_effect=ConnectionException("link lost"))
    registry = ir.async_get(hass)

    for _ in range(2):
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert coordinator.data == good_data

    for _ in range(2):
        await coordinator.async_refresh()
        assert not coordinator.last_update_success
        assert registry.async_get_issue(DOMAIN, f"unreachable_{MAC}") is None

    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert registry.async_get_issue(DOMAIN, f"unreachable_{MAC}") is not None
