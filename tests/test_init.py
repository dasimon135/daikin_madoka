"""Tests for registry hygiene: orphan-device purge and per-device removal."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.daikin_madoka import (
    _async_purge_orphan_devices,
    async_remove_config_entry_device,
)
from custom_components.daikin_madoka.const import DOMAIN

MAC = "AA:BB:CC:DD:EE:FF"
OTHER_MAC = "AA:BB:CC:DD:EE:00"


def _add_entry(hass: HomeAssistant, domain: str = DOMAIN) -> MockConfigEntry:
    entry = MockConfigEntry(domain=domain)
    entry.add_to_hass(hass)
    return entry


def _make_orphan(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Turn ``entry`` into a dangling reference, as seen in the field.

    The registry helper refuses to create a device linked to a nonexistent
    entry id ("Can't link device to unknown config entry"), so the orphan
    state cannot be built directly. Reproduce it the way it happens in the
    wild instead: the device is created while the entry exists, then the
    entry disappears without the registry being cleaned up. Dropping the
    entry from the manager's internal store (rather than
    ``async_remove``) skips HA's cascade cleanup, leaving the device's
    ``config_entries`` pointing at an id that no longer resolves — exactly
    the leftover produced by an interrupted delete/recreate cycle.
    """
    del hass.config_entries._entries[entry.entry_id]
    assert hass.config_entries.async_get_entry(entry.entry_id) is None


async def test_purge_removes_orphan_device(hass: HomeAssistant) -> None:
    """A device whose only linked entry id is dangling is purged."""
    entry = _add_entry(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, MAC)}
    )
    _make_orphan(hass, entry)

    _async_purge_orphan_devices(hass)

    assert dev_reg.async_get(device.id) is None


async def test_purge_keeps_device_of_live_entry(hass: HomeAssistant) -> None:
    """A device linked to a live daikin_madoka entry survives."""
    entry = _add_entry(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, MAC)}
    )

    _async_purge_orphan_devices(hass)

    assert dev_reg.async_get(device.id) is not None


async def test_purge_keeps_device_shared_with_live_foreign_entry(
    hass: HomeAssistant,
) -> None:
    """A device with a dangling id but also a live foreign entry survives."""
    ours = _add_entry(hass)
    foreign = _add_entry(hass, domain="other_domain")
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=ours.entry_id, identifiers={(DOMAIN, MAC)}
    )
    # Link the same registry device to the foreign integration's entry.
    dev_reg.async_get_or_create(
        config_entry_id=foreign.entry_id, identifiers={(DOMAIN, MAC)}
    )
    _make_orphan(hass, ours)

    _async_purge_orphan_devices(hass)

    device = dev_reg.async_get(device.id)
    assert device is not None
    assert device.config_entries == {ours.entry_id, foreign.entry_id}


async def test_purge_ignores_foreign_domain_device(hass: HomeAssistant) -> None:
    """An orphaned device of another domain is not ours to purge."""
    foreign = _add_entry(hass, domain="other_domain")
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=foreign.entry_id, identifiers={("other_domain", MAC)}
    )
    _make_orphan(hass, foreign)

    _async_purge_orphan_devices(hass)

    assert dev_reg.async_get(device.id) is not None


async def test_remove_device_allowed_for_stale_mac(hass: HomeAssistant) -> None:
    """Removal is allowed when no coordinator serves the device's MAC."""
    entry = _add_entry(hass)
    entry.runtime_data = {OTHER_MAC: MagicMock()}
    device_entry = SimpleNamespace(identifiers={(DOMAIN, MAC)})

    assert await async_remove_config_entry_device(hass, entry, device_entry) is True


async def test_remove_device_refused_for_active_mac(hass: HomeAssistant) -> None:
    """Removal is refused while a live coordinator serves the device's MAC."""
    entry = _add_entry(hass)
    entry.runtime_data = {MAC: MagicMock()}
    device_entry = SimpleNamespace(identifiers={(DOMAIN, MAC), ("other", "x")})

    assert await async_remove_config_entry_device(hass, entry, device_entry) is False
