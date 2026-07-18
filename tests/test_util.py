"""Unit tests for build_candidates ordering (no hass instance required)."""

from types import SimpleNamespace
from unittest.mock import patch

from custom_components.daikin_madoka.util import build_candidates

ADDRESS = "D0:CF:13:0F:11:F6"
PROXY_A = "11:11:11:11:11:11"
PROXY_B = "22:22:22:22:22:22"

# build_candidates only forwards hass to the bluetooth API, which is patched.
HASS = object()

PATCH_TARGET = "homeassistant.components.bluetooth.async_scanner_devices_by_address"


def _scanner_device(source: str | None, rssi: int | None) -> SimpleNamespace:
    """Fake BluetoothScannerDevice exposing just what sort_key reads."""
    return SimpleNamespace(
        ble_device=SimpleNamespace(details={"source": source}),
        advertisement=SimpleNamespace(rssi=rssi) if rssi is not None else None,
    )


def test_preferred_source_beats_stronger_rssi() -> None:
    strong_other = _scanner_device(PROXY_B, -40)
    weak_preferred = _scanner_device(PROXY_A, -90)

    with patch(
        PATCH_TARGET, return_value=[strong_other, weak_preferred]
    ) as mock_scan:
        result = build_candidates(HASS, ADDRESS, PROXY_A)

    assert result == [weak_preferred.ble_device, strong_other.ble_device]
    mock_scan.assert_called_once_with(HASS, ADDRESS, connectable=True)


def test_rssi_descending_when_no_preferred_source() -> None:
    weak = _scanner_device(PROXY_A, -80)
    strong = _scanner_device(PROXY_B, -45)
    # No advertisement at all: treated as weakest, sorted last.
    silent = _scanner_device(None, None)

    with patch(PATCH_TARGET, return_value=[weak, silent, strong]):
        result = build_candidates(HASS, ADDRESS, None)

    assert result == [strong.ble_device, weak.ble_device, silent.ble_device]


def test_non_dict_details_tolerated() -> None:
    # Local-adapter BLEDevices carry backend-specific details (not a dict);
    # they must remain usable candidates, just never preferred-matched.
    local = SimpleNamespace(
        ble_device=SimpleNamespace(details=None),
        advertisement=SimpleNamespace(rssi=-30),
    )
    proxy = _scanner_device(PROXY_A, -70)

    with patch(PATCH_TARGET, return_value=[local, proxy]):
        result = build_candidates(HASS, ADDRESS, PROXY_A)

    assert result == [proxy.ble_device, local.ble_device]


def test_no_scanner_devices_returns_empty_list() -> None:
    with patch(PATCH_TARGET, return_value=[]):
        assert build_candidates(HASS, ADDRESS, PROXY_A) == []
