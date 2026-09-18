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


def _scanner_device(
    source: str | None, rssi: int | None, free_slots: int | None = None
) -> SimpleNamespace:
    """Fake BluetoothScannerDevice exposing just what sort_key reads.

    free_slots=None models every backend that does not report allocations at
    all (local adapters, older habluetooth); the scanner attribute is then
    absent entirely, which is also what the pre-slot-awareness fixtures look
    like.
    """
    device = SimpleNamespace(
        ble_device=SimpleNamespace(details={"source": source}),
        advertisement=SimpleNamespace(rssi=rssi) if rssi is not None else None,
    )
    if free_slots is not None:
        device.scanner = SimpleNamespace(
            get_allocations=lambda: SimpleNamespace(
                source=source, slots=3, free=free_slots, allocated=[]
            )
        )
    return device


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


def test_absent_preferred_source_falls_back_to_rssi_order() -> None:
    # The sticky proxy may be offline (reflash, power cut); its absence must
    # not disturb the pure RSSI ordering of the remaining paths.
    weak = _scanner_device(PROXY_A, -80)
    strong = _scanner_device(PROXY_B, -45)

    with patch(PATCH_TARGET, return_value=[weak, strong]):
        result = build_candidates(HASS, ADDRESS, "33:33:33:33:33:33")

    assert result == [strong.ble_device, weak.ble_device]


def test_rssi_tie_preserves_input_order() -> None:
    # sorted() is stable: equal keys keep the scanner-reported order rather
    # than reshuffling candidates between polls.
    first = _scanner_device(PROXY_A, -60)
    second = _scanner_device(PROXY_B, -60)

    with patch(PATCH_TARGET, return_value=[first, second]):
        result = build_candidates(HASS, ADDRESS, None)

    assert result == [first.ble_device, second.ble_device]


def test_none_rssi_in_advertisement_sorted_last() -> None:
    # Some backends deliver an advertisement without an RSSI; a None must not
    # TypeError inside the sort key (which would silently downgrade every
    # connect to the legacy path) and sorts as the weakest candidate.
    ghost = SimpleNamespace(
        ble_device=SimpleNamespace(details={"source": PROXY_A}),
        advertisement=SimpleNamespace(rssi=None),
    )
    strong = _scanner_device(PROXY_B, -45)

    with patch(PATCH_TARGET, return_value=[ghost, strong]):
        result = build_candidates(HASS, ADDRESS, None)

    assert result == [strong.ble_device, ghost.ble_device]


# --------------------------------------------------------------------------
# Slot awareness
#
# An ESPHome proxy serves a fixed number of simultaneous connections and
# refuses every connect once they are taken; HA core issue #176516 can also
# leave slots allocated to connections that no longer exist. In the field one
# proxy saturated at 3/3 kept being offered first to a thermostat while three
# idle proxies were never tried, so every attempt failed instantly, forever.
# --------------------------------------------------------------------------


def test_a_saturated_preferred_proxy_is_demoted() -> None:
    """The field case: sticky ordering must not outrank "can accept at all"."""
    saturated_preferred = _scanner_device(PROXY_A, -50, free_slots=0)
    free_other = _scanner_device(PROXY_B, -80, free_slots=2)

    with patch(PATCH_TARGET, return_value=[saturated_preferred, free_other]):
        result = build_candidates(HASS, ADDRESS, PROXY_A)

    assert result == [free_other.ble_device, saturated_preferred.ble_device]


def test_a_saturated_proxy_is_demoted_not_excluded() -> None:
    """A slot may free up before we get there; never throw a path away."""
    saturated = _scanner_device(PROXY_A, -40, free_slots=0)

    with patch(PATCH_TARGET, return_value=[saturated]):
        result = build_candidates(HASS, ADDRESS, None)

    assert result == [saturated.ble_device]


def test_free_slots_beat_a_much_stronger_signal() -> None:
    saturated_close = _scanner_device(PROXY_A, -35, free_slots=0)
    free_far = _scanner_device(PROXY_B, -92, free_slots=1)

    with patch(PATCH_TARGET, return_value=[saturated_close, free_far]):
        result = build_candidates(HASS, ADDRESS, None)

    assert result == [free_far.ble_device, saturated_close.ble_device]


def test_preferred_still_wins_when_both_have_slots() -> None:
    """Slot awareness must not undo the sticky-proxy ordering it extends."""
    free_other = _scanner_device(PROXY_B, -40, free_slots=3)
    free_preferred = _scanner_device(PROXY_A, -90, free_slots=1)

    with patch(PATCH_TARGET, return_value=[free_other, free_preferred]):
        result = build_candidates(HASS, ADDRESS, PROXY_A)

    assert result == [free_preferred.ble_device, free_other.ble_device]


def test_a_scanner_without_allocation_info_is_assumed_free() -> None:
    """Unknown must mean usable, or a whole backend would be demoted for free."""
    unknown = _scanner_device(PROXY_A, -80)
    free = _scanner_device(PROXY_B, -85, free_slots=2)

    with patch(PATCH_TARGET, return_value=[free, unknown]):
        result = build_candidates(HASS, ADDRESS, None)

    # Same slot rank, so plain RSSI order decides.
    assert result == [unknown.ble_device, free.ble_device]


def test_a_scanner_returning_none_allocations_is_assumed_free() -> None:
    """BaseHaScanner.get_allocations() returns None unless a subclass tracks it."""
    silent = _scanner_device(PROXY_A, -80)
    silent.scanner = SimpleNamespace(get_allocations=lambda: None)
    saturated = _scanner_device(PROXY_B, -30, free_slots=0)

    with patch(PATCH_TARGET, return_value=[saturated, silent]):
        result = build_candidates(HASS, ADDRESS, None)

    assert result == [silent.ble_device, saturated.ble_device]


def test_a_raising_allocation_read_is_assumed_free() -> None:
    """A diagnostics read must never be able to break connecting."""

    def _boom():
        raise RuntimeError("scanner went away")

    grumpy = _scanner_device(PROXY_A, -80)
    grumpy.scanner = SimpleNamespace(get_allocations=_boom)
    saturated = _scanner_device(PROXY_B, -30, free_slots=0)

    with patch(PATCH_TARGET, return_value=[saturated, grumpy]):
        result = build_candidates(HASS, ADDRESS, None)

    assert result == [grumpy.ble_device, saturated.ble_device]


# --------------------------------------------------------------------------
# Local adapter (daikin_madoka#105)
#
# habluetooth hands over a local adapter's BLEDevice exactly as BlueZ built it:
# its details name a D-Bus path and carry no "source". pymadoka (0.3.11+)
# records the scanner that carried the link, i.e. the adapter's own MAC, as the
# bonded source. Matching on details alone therefore filtered the adapter out
# of its own bonded list, and every reconnect after a restart failed in a few
# milliseconds with an empty candidate list until the user pressed Reconnect.
# --------------------------------------------------------------------------

LOCAL_ADAPTER = "44:44:44:44:44:44"


def _local_adapter_device(rssi: int) -> SimpleNamespace:
    return SimpleNamespace(
        ble_device=SimpleNamespace(
            details={"path": "/org/bluez/hci0/dev_D0_CF_13_0F_11_F6", "props": {}}
        ),
        advertisement=SimpleNamespace(rssi=rssi),
        scanner=SimpleNamespace(source=LOCAL_ADAPTER),
    )


def test_a_bonded_local_adapter_passes_the_bonded_filter() -> None:
    local = _local_adapter_device(-60)

    with patch(PATCH_TARGET, return_value=[local]):
        result = build_candidates(HASS, ADDRESS, LOCAL_ADAPTER, [LOCAL_ADAPTER])

    assert result == [local.ble_device]


def test_the_bonded_filter_still_drops_an_unbonded_proxy_beside_the_adapter() -> None:
    local = _local_adapter_device(-85)
    unbonded = _scanner_device(PROXY_B, -40)

    with patch(PATCH_TARGET, return_value=[unbonded, local]):
        result = build_candidates(HASS, ADDRESS, LOCAL_ADAPTER, [LOCAL_ADAPTER])

    assert result == [local.ble_device]


def test_a_preferred_local_adapter_beats_a_stronger_proxy() -> None:
    local = _local_adapter_device(-85)
    proxy = _scanner_device(PROXY_B, -40)

    with patch(PATCH_TARGET, return_value=[proxy, local]):
        result = build_candidates(HASS, ADDRESS, LOCAL_ADAPTER)

    assert result == [local.ble_device, proxy.ble_device]


def test_the_source_pymadoka_records_is_the_one_the_filter_matches() -> None:
    """The contract itself: record, restart, match.

    What the library stores after a connect through a path must be what the
    builder reads from that same path, or a recorded bond is never matched.
    """
    from pymadoka.connection import connected_path_source

    local = _local_adapter_device(-60)
    client = SimpleNamespace(_connected_scanner=local.scanner)
    recorded = connected_path_source(client)

    with patch(PATCH_TARGET, return_value=[local]):
        result = build_candidates(HASS, ADDRESS, recorded, [recorded])

    assert recorded == LOCAL_ADAPTER
    assert result == [local.ble_device]
