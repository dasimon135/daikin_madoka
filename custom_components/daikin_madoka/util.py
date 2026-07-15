"""Shared helpers for Daikin Madoka."""

import re

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
