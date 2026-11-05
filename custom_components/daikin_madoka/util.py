"""Shared helpers for Daikin Madoka."""

import logging
import math
import re
from typing import Any

from bleak.backends.device import BLEDevice

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import format_mac

from .const import CONF_MAC

_LOGGER = logging.getLogger(__name__)

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


def registry_devices(dev_reg: Any) -> list[Any]:
    """Every entry of the device registry, on old and new cores alike.

    Since Home Assistant 2026.9 iterating `registry.devices` yields the entries
    and any mapping access (`.values()` included) is deprecated, for removal in
    2027.9. Older cores expose a plain mapping, whose iteration yields ids.
    """
    devices = list(dev_reg.devices)
    if devices and isinstance(devices[0], str):
        return list(dev_reg.devices.values())
    return devices


def device_for_address(
    registry: Any, entry: ConfigEntry | None, domain: str, address: str
) -> Any:
    """The registry device of one thermostat.

    `async_get_device(identifiers=...)` is deprecated since 2026.9 (removal in
    2027.8) because identifiers are no longer unique across config entries; the
    entry-scoped replacement does not exist before that release.
    """
    lookup = getattr(registry, "async_get_device_by_identifier", None)
    if lookup is not None and entry is not None:
        return lookup((domain, address), entry.entry_id)
    return registry.async_get_device(identifiers={(domain, address)})


def _path_source(scanner_device: "bluetooth.BluetoothScannerDevice") -> str | None:
    """The source this path is recorded under once a connection succeeds.

    pymadoka records the scanner's own source (connected_path_source), so the
    scanner is the key that matches the bonded list. details["source"] is only
    a fallback: a local adapter's BLEDevice comes straight from bleak with
    BlueZ details and has no "source" at all.
    """
    source = getattr(getattr(scanner_device, "scanner", None), "source", None)
    if isinstance(source, str) and source:
        return source
    details = getattr(scanner_device.ble_device, "details", None)
    if isinstance(details, dict):
        return details.get("source")
    return None


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


# habluetooth's score for a path with no free slot: such a path is never
# chosen, so it can be neither a target nor something to steer away from.
_NO_RSSI_VALUE = -127
# How far below the best bonded path an unbonded one is pushed. RSSI moves
# by a few dB between two adverts; without headroom the next connect could
# land on the unbonded proxy again on a single strong reading.
UNBONDED_STEER_MARGIN_DB = 5.0
# habluetooth subtracts rssi_diff * 0.51 per recorded failure.
_FAILURE_WEIGHT = 0.51
# Upper bound per charge, so a pathological reading cannot bury a proxy.
UNBONDED_STEER_MAX_FAILURES = 20

_steering_unavailable_warned = False
# What steer_off_unbonded_paths added, per (address, scanner), so it can be
# taken back without touching the failures habluetooth counted itself.
_charged: dict[tuple[str, Any], int] = {}


def steer_off_unbonded_paths(
    hass: HomeAssistant, address: str, allowed_sources: list[str] | None
) -> list[str]:
    """Make habluetooth prefer a bonded proxy over the unbonded ones it favours.

    Home Assistant picks the proxy for every connect itself (the BLEDevice we
    offer is reduced to its address and every path is re-scored), and nothing
    lets an integration choose. When the unbonded proxy hears the thermostat
    best, it wins every time: pymadoka lands there, refuses to pair (a prompt
    nobody answers would jam the thermostat) and drops the link. habluetooth
    counts that link as a SUCCESS, which clears the very failure counter that
    could have moved it, so the next round lands on the same proxy and the
    thermostat never comes back (2026-11-04: Manon and Salon stuck on
    atomebuanderie for good after a two-second API blip on their own proxies).

    The only input the scorer takes from outside is that per-proxy, per-address
    failure counter. This charges each unbonded path that outscores the best
    bonded one with just enough failures to fall MARGIN dB below it. It runs
    from the veto, i.e. right after habluetooth recorded the success, so the
    charge is what the next connect sees.

    Never moves anything when no bonded proxy hears the device or none has a
    free slot: then there is nowhere better to go, and the veto alone keeps the
    thermostat from being prompted. The counter clears itself the next time a
    connection through that proxy succeeds, which only an unrestricted pairing
    window allows. Returns the sources charged, for logging and tests.
    """
    if not allowed_sources:
        return []
    allowed = set(allowed_sources)
    devices = sorted(
        bluetooth.async_scanner_devices_by_address(hass, address, connectable=True),
        key=_rssi,
        reverse=True,
    )
    if len(devices) < 2:
        return []
    # Same rssi_diff the wrapper computes: the two strongest raw readings.
    rssi_diff = _rssi(devices[0]) - _rssi(devices[1])
    step = max(rssi_diff, 1) * _FAILURE_WEIGHT
    bonded_scores = [
        sd.score_connection_path(rssi_diff)
        for sd in devices
        if _path_source(sd) in allowed
    ]
    if not bonded_scores:
        return []
    best_bonded = max(bonded_scores)
    if best_bonded <= _NO_RSSI_VALUE:
        return []
    charged: list[str] = []
    for sd in devices:
        source = _path_source(sd)
        if not source or source in allowed:
            continue
        gap = sd.score_connection_path(rssi_diff) - best_bonded + UNBONDED_STEER_MARGIN_DB
        if gap <= 0:
            continue
        if not _charge(sd, address, gap, step):
            return charged
        charged.append(source)
    return charged


# Proxies a pairing window has already landed on, per address. Cleared with
# the charges whenever a window opens or closes.
_window_tried: dict[str, set[Any]] = {}


def steer_off_tried_paths(hass: HomeAssistant, address: str) -> list[str]:
    """Send the next attempt of a pairing window to a proxy it has not tried.

    A window lifts the bonded-proxy restriction, so habluetooth sends every
    attempt to whatever scores best, and nothing in a failed pairing moves
    that score: the link itself came up, which habluetooth records as a
    success. Each candidate of the round therefore landed on the same proxy
    and put the same prompt on the thermostat (Manon, 2026-11-04: three
    prompts, all through atomebuanderie, which cannot answer them).

    Called from the veto, i.e. after the link is up and before pair(). The
    proxy holding the link is the one whose slot allocation lists the
    address; it joins the tried set, and every tried proxy is charged to sit
    MARGIN dB below the best one not yet tried. A pairing that then succeeds
    closes the window, which takes the charges back; one that fails leaves
    them, so the next candidate goes elsewhere.

    Does nothing when no proxy lists the address (the proxy's allocation
    report has not arrived, or the backend does not report one): charging the
    best-scoring path instead could hit a proxy the attempt never touched.
    Also nothing once every proxy has been tried. Returns the sources of the
    proxies charged.
    """
    devices = sorted(
        bluetooth.async_scanner_devices_by_address(hass, address, connectable=True),
        key=_rssi,
        reverse=True,
    )
    if len(devices) < 2:
        return []
    tried = _window_tried.setdefault(address, set())
    tried.update(sd.scanner for sd in devices if _holds(sd.scanner, address))
    rssi_diff = _rssi(devices[0]) - _rssi(devices[1])
    step = max(rssi_diff, 1) * _FAILURE_WEIGHT
    untried = [
        score
        for sd in devices
        if sd.scanner not in tried
        and (score := sd.score_connection_path(rssi_diff)) > _NO_RSSI_VALUE
    ]
    if not tried or not untried:
        return []
    best_untried = max(untried)
    charged: list[str] = []
    for sd in devices:
        if sd.scanner not in tried:
            continue
        score = sd.score_connection_path(rssi_diff)
        if score <= _NO_RSSI_VALUE:
            # Full only because this very attempt holds a slot; the slot is
            # released before the next candidate, so score it on its RSSI and
            # the failures it already carries.
            failures = getattr(sd.scanner, "_connect_failures", None)
            held = failures.get(address, 0) if isinstance(failures, dict) else 0
            score = _rssi(sd) - step * held
        gap = score - best_untried + UNBONDED_STEER_MARGIN_DB
        if gap <= 0:
            continue
        if not _charge(sd, address, gap, step):
            return charged
        charged.append(_path_source(sd) or "?")
    return charged


def _holds(scanner: Any, address: str) -> bool:
    """Whether the proxy reports a live connection to `address`."""
    try:
        allocations = scanner.get_allocations()
    except Exception:  # noqa: BLE001 - a scanner that cannot say holds nothing
        return False
    allocated = getattr(allocations, "allocated", None) or ()
    return address.upper() in {str(a).upper() for a in allocated}


def _charge(sd: Any, address: str, gap: float, step: float) -> bool:
    """Add enough failures to `sd`'s proxy to lower its score by `gap`.

    False when habluetooth no longer exposes the counter, warned once.
    """
    failures = getattr(sd.scanner, "_connect_failures", None)
    if not isinstance(failures, dict):
        global _steering_unavailable_warned
        if not _steering_unavailable_warned:
            _steering_unavailable_warned = True
            _LOGGER.warning(
                "habluetooth no longer exposes its per-proxy failure "
                "counter; a thermostat that hears an unbonded proxy best "
                "will keep landing on it"
            )
        return False
    extra = min(math.ceil(gap / step), UNBONDED_STEER_MAX_FAILURES)
    failures[address] = failures.get(address, 0) + extra
    key = (address, sd.scanner)
    _charged[key] = _charged.get(key, 0) + extra
    return True


def lift_unbonded_steering(address: str) -> int:
    """Take back every failure this module charged for `address`.

    Called when a pairing window opens and when it closes. On opening: the
    penalties exist to keep unattended reconnects off proxies without a bond,
    and pairing such a proxy is precisely what the window is for; left in
    place, they would send the pairing to the weakest bonded proxy instead of
    the one by the thermostat (Valentine, 2026-11-04, whose own proxy had just
    lost its bond). On closing: the window's own charges (see
    steer_off_tried_paths) only made sense for that window.

    Only what we added is removed. A counter now below that amount was reset
    by habluetooth after a success through the proxy, so whatever it holds is
    its own count and stays. Returns the number of failures removed.
    """
    _window_tried.pop(address, None)
    lifted = 0
    for key in [key for key in _charged if key[0] == address]:
        ours = _charged.pop(key)
        failures = getattr(key[1], "_connect_failures", None)
        if not isinstance(failures, dict):
            continue
        current = failures.get(address, 0)
        if current < ours:
            continue
        if current == ours:
            failures.pop(address, None)
        else:
            failures[address] = current - ours
        lifted += ours
    return lifted


def _rssi(scanner_device: Any) -> int:
    advertisement = getattr(scanner_device, "advertisement", None)
    rssi = getattr(advertisement, "rssi", None)
    return rssi if rssi is not None else _NO_RSSI_VALUE
