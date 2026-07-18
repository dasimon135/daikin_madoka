"""Shared helpers for Daikin Madoka."""

import re

from bleak.backends.device import BLEDevice

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import format_mac

MAC_REGEX = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


def normalize_mac(mac: str) -> str | None:
    """Normalize any accepted MAC spelling to the canonical AA:BB:CC:DD:EE:FF.

    HA's BLE registry keys devices by the uppercase colon form; storing or
    using anything else makes the device permanently unreachable. Returns
    None when the input is not a MAC address.
    """
    normalized = format_mac(mac.strip()).upper()
    if not MAC_REGEX.match(normalized):
        return None
    return normalized


def build_candidates(
    hass: HomeAssistant, address: str, preferred_source: str | None
) -> list[BLEDevice]:
    """Ordered BLEDevice paths to the device: preferred proxy first, then RSSI.

    Feeds pymadoka's candidates_callback so the connection tries the proxy
    that last authenticated successfully before letting signal strength pick.
    Without the sticky ordering, an unbonded proxy that happens to win on RSSI
    is tried first and the BRC1H silently refuses it.
    """
    scanner_devices = bluetooth.async_scanner_devices_by_address(
        hass, address, connectable=True
    )

    def sort_key(sd: bluetooth.BluetoothScannerDevice) -> tuple[int, int]:
        # details is backend-specific: ESPHome proxies expose their source MAC
        # in a dict; other backends may carry something else (or nothing).
        source = None
        details = getattr(sd.ble_device, "details", None)
        if isinstance(details, dict):
            source = details.get("source")
        rssi = sd.advertisement.rssi if sd.advertisement else -127
        return (0 if preferred_source and source == preferred_source else 1, -rssi)

    return [sd.ble_device for sd in sorted(scanner_devices, key=sort_key)]
