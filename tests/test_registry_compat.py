"""The device registry calls must work on old cores and stay quiet on new ones.

Home Assistant 2026.9 deprecated two things this integration did, for removal
in 2027.8 and 2027.9: using `registry.devices` as a mapping, and
`async_get_device(identifiers=...)`. The replacements do not exist on the
oldest supported core, so both calls go through a helper that picks the
supported form. The fakes below raise on the deprecated access, which is what
core code already gets and what custom integrations will get at removal.
"""

from types import SimpleNamespace

import pytest

from custom_components.daikin_madoka.util import device_for_address, registry_devices

DOMAIN = "daikin_madoka"
MAC = "D0:CF:13:0F:11:F6"


class _NewDevicesView:
    """2026.9+: iterating yields entries, any mapping access is deprecated."""

    def __init__(self, entries):
        self._entries = entries

    def __iter__(self):
        return iter(self._entries)

    def values(self):
        raise AssertionError("deprecated mapping access on registry.devices")


def test_devices_are_enumerated_without_mapping_access_on_a_new_core() -> None:
    entries = [SimpleNamespace(id="a"), SimpleNamespace(id="b")]
    registry = SimpleNamespace(devices=_NewDevicesView(entries))

    assert registry_devices(registry) == entries


def test_devices_are_enumerated_from_the_mapping_on_an_old_core() -> None:
    entries = {"a": SimpleNamespace(id="a"), "b": SimpleNamespace(id="b")}
    registry = SimpleNamespace(devices=entries)

    assert registry_devices(registry) == list(entries.values())


def test_an_empty_registry_is_an_empty_list() -> None:
    assert registry_devices(SimpleNamespace(devices={})) == []


def test_the_device_lookup_uses_the_entry_scoped_api_when_it_exists() -> None:
    calls = []

    class _NewRegistry:
        def async_get_device_by_identifier(self, identifier, config_entry_id):
            calls.append((identifier, config_entry_id))
            return "device"

        def async_get_device(self, **_kwargs):
            raise AssertionError("deprecated async_get_device")

    entry = SimpleNamespace(entry_id="entry-1")

    assert device_for_address(_NewRegistry(), entry, DOMAIN, MAC) == "device"
    assert calls == [((DOMAIN, MAC), "entry-1")]


@pytest.mark.parametrize("entry", [None, SimpleNamespace(entry_id="entry-1")])
def test_the_device_lookup_falls_back_on_an_old_core(entry) -> None:
    class _OldRegistry:
        def async_get_device(self, identifiers):
            assert identifiers == {(DOMAIN, MAC)}
            return "device"

    assert device_for_address(_OldRegistry(), entry, DOMAIN, MAC) == "device"
