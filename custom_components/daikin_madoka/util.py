"""Shared helpers for Daikin Madoka."""

import re

from bleak.backends.device import BLEDevice

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import format_mac

from .const import CONF_MAC

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


def entry_macs(entry: ConfigEntry) -> list[str]:
    """Normalized MACs an entry covers, whether or not it ever loaded.

    New-style entries carry one MAC, legacy entries a list; both shapes are
    read straight from entry.data so this works for an entry stuck in
    setup_retry, one being unloaded, and one being removed.
    """
    if CONF_MAC in entry.data:
        raw_macs = [entry.data[CONF_MAC]]
    else:
        raw_macs = list(entry.data.get(CONF_DEVICES, []))
    return [normalize_mac(mac) or mac for mac in raw_macs]


def _has_free_slot(scanner_device: "bluetooth.BluetoothScannerDevice") -> bool:
    """True unless the scanner positively reports zero free connection slots.

    An ESPHome proxy serves a fixed number of simultaneous connections (3 by
    default) and refuses every connect once they are taken — and HA core issue
    #176516 can leave those slots allocated to connections that no longer exist.
    A saturated proxy therefore fails instantly and repeatedly, which is exactly
    what happened in the field: one proxy at 3/3 blocked a thermostat while
    three idle proxies were never tried.

    Read defensively and default to True. Local adapters, older habluetooth
    releases and any scanner that simply does not track allocations return
    nothing here; "unknown" must mean "assume usable", or a backend that never
    reports would see all of its paths demoted for no reason.
    """
    scanner = getattr(scanner_device, "scanner", None)
    get_allocations = getattr(scanner, "get_allocations", None)
    if not callable(get_allocations):
        return True
    try:
        allocations = get_allocations()
    except Exception:  # noqa: BLE001 - a diagnostics read must never break connecting
        return True
    free = getattr(allocations, "free", None)
    if not isinstance(free, int) or isinstance(free, bool):
        return True
    return free > 0


def _path_source(scanner_device: "bluetooth.BluetoothScannerDevice") -> str | None:
    """The source a path is recorded under once a connect through it succeeds.

    pymadoka records the scanner that actually carried the link
    (``client._connected_scanner.source``), so the bonded list holds scanner
    sources and this must read the same thing, or a recorded bond can never be
    matched again. For an ESPHome proxy the scanner source and
    ``details["source"]`` are the same MAC. A local adapter's BLEDevice comes
    straight from BlueZ, whose details carry no ``source`` at all, so reading
    details alone emptied the candidate list at every restart once the adapter
    had been recorded (daikin_madoka#105). ``details`` stays as the fallback
    for a scanner that does not name itself.
    """
    source = getattr(getattr(scanner_device, "scanner", None), "source", None)
    if isinstance(source, str) and source:
        return source
    details = getattr(scanner_device.ble_device, "details", None)
    if isinstance(details, dict):
        return details.get("source")
    return None


def build_candidates(
    hass: HomeAssistant,
    address: str,
    preferred_source: str | None,
    allowed_sources: list[str] | None = None,
) -> list[BLEDevice]:
    """Ordered BLEDevice paths: free slots first, then preferred proxy, then RSSI.

    Feeds pymadoka's candidates_callback so the connection tries the proxy
    that last authenticated successfully before letting signal strength pick.
    Without the sticky ordering, an unbonded proxy that happens to win on RSSI
    is tried first and the BRC1H silently refuses it.

    A proxy with no free connection slot is demoted ahead of that, sticky or
    not: it cannot accept a connection at all, so offering it first burns the
    per-candidate attempt (and, through a proxy, ~20s of the connect budget)
    before the paths that could have worked are even reached. Demoted, never
    excluded — the slot count is a hint that some backends do not provide, and a
    saturated proxy may free a slot by the time we reach it.

    ``allowed_sources`` restricts the result to proxies known to hold a bond.
    Reaching an unbonded proxy starts a real pairing, which needs a human at
    the thermostat to accept a numeric code — so an unattended reconnect must
    never get there. None means unrestricted, for a fresh install that has no
    bond recorded yet and for a user-opened pairing window.
    """
    scanner_devices = bluetooth.async_scanner_devices_by_address(
        hass, address, connectable=True
    )

    if allowed_sources:
        allowed = set(allowed_sources)
        scanner_devices = [
            sd for sd in scanner_devices if _path_source(sd) in allowed
        ]

    def sort_key(sd: bluetooth.BluetoothScannerDevice) -> tuple[int, int, int]:
        source = _path_source(sd)
        # Some backends deliver an advertisement without an RSSI; a None here
        # would TypeError inside the sort and silently drop the candidates.
        rssi = (
            sd.advertisement.rssi
            if sd.advertisement and sd.advertisement.rssi is not None
            else -127
        )
        return (
            0 if _has_free_slot(sd) else 1,
            0 if preferred_source and source == preferred_source else 1,
            -rssi,
        )

    return [sd.ble_device for sd in sorted(scanner_devices, key=sort_key)]
