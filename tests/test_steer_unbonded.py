"""steer_off_unbonded_paths: make habluetooth's own scorer pick a bonded proxy.

The fake scanner reproduces habluetooth's `_score_connection_paths` (6.26.x)
term for term, so these tests check the arithmetic the real wrapper will run:
a charge that is not enough to reorder the paths there fails here too.
"""

from types import SimpleNamespace
from unittest.mock import patch

from custom_components.daikin_madoka import util
from custom_components.daikin_madoka.util import (
    UNBONDED_STEER_MARGIN_DB,
    lift_unbonded_steering,
    steer_off_tried_paths,
    steer_off_unbonded_paths,
)

ADDRESS = "9C:AC:6D:D4:F9:FC"
BUANDERIE = "D0:CF:13:0F:05:5A"
VALENTINE = "D0:CF:13:0E:D1:9E"
SALON = "D0:CF:13:0F:11:F6"
PARENTS = "D0:CF:13:0E:C9:2A"

HASS = object()
PATCH_TARGET = "homeassistant.components.bluetooth.async_scanner_devices_by_address"


class _Scanner:
    def __init__(self, source: str, slots: int, free: int, failures: dict | None):
        self.source = source
        self._slots = (slots, free)
        self.allocated: list[str] = []
        if failures is not None:
            self._connect_failures = failures

    def get_allocations(self):
        slots, free = self._slots
        return SimpleNamespace(slots=slots, free=free, allocated=self.allocated)

    def score(self, rssi: int, address: str, rssi_diff: int) -> float:
        # habluetooth base_scanner._score_connection_paths, minus the
        # in-progress term (no connect is in flight when the veto runs).
        diff = max(rssi_diff, 1)
        score = float(rssi)
        failures = getattr(self, "_connect_failures", {}).get(address, 0)
        if failures:
            score -= diff * failures * 0.51
        slots, free = self._slots
        if slots > 0:
            if free == 0:
                return -127
            if free == 1:
                score -= diff * 0.76
        return score


def _path(source: str, rssi: int, slots: int = 2, free: int = 2, failures=None):
    scanner = _Scanner(source, slots, free, {} if failures is None else failures)
    if failures is False:
        del scanner._connect_failures
    device = SimpleNamespace(
        scanner=scanner,
        ble_device=SimpleNamespace(address=ADDRESS, details={"source": source}),
        advertisement=SimpleNamespace(rssi=rssi),
    )
    device.score_connection_path = lambda diff: scanner.score(rssi, ADDRESS, diff)
    return device


def _ranking(paths) -> list[str]:
    """The order habluetooth's wrapper would try them in."""
    by_rssi = sorted(paths, key=lambda p: p.advertisement.rssi, reverse=True)
    diff = by_rssi[0].advertisement.rssi - by_rssi[1].advertisement.rssi
    return [
        p.scanner.source
        for p in sorted(by_rssi, key=lambda p: p.score_connection_path(diff), reverse=True)
    ]


def _manon():
    """Readings logged on 2026-11-04 13:36 for Manon, bonded to Valentine only."""
    return [
        _path(BUANDERIE, -71, slots=4, free=3),
        _path(VALENTINE, -81),
        _path(SALON, -85),
        _path(PARENTS, -92),
    ]


def test_the_unbonded_favourite_drops_below_the_bonded_proxy() -> None:
    paths = _manon()
    assert _ranking(paths)[0] == BUANDERIE

    with patch(PATCH_TARGET, return_value=paths):
        # atomesalon (-85) sits within the margin of Valentine (-81), so it is
        # pushed down too; atom-s3-parents (-92) is already far enough below.
        assert steer_off_unbonded_paths(HASS, ADDRESS, [VALENTINE]) == [BUANDERIE, SALON]

    assert _ranking(paths)[0] == VALENTINE
    diff = 10
    gap = paths[1].score_connection_path(diff) - paths[0].score_connection_path(diff)
    assert gap >= UNBONDED_STEER_MARGIN_DB


def test_every_unbonded_path_ahead_is_charged() -> None:
    """Salon (2026-11-04): bonded to atomesalon and atom-s3-parents, heard
    best by buanderie, then Valentine within a few dB of the bonded proxy."""
    paths = [
        _path(BUANDERIE, -71, slots=4, free=3),
        _path(SALON, -84),
        _path(VALENTINE, -86),
        _path(PARENTS, -89),
    ]
    with patch(PATCH_TARGET, return_value=paths):
        charged = steer_off_unbonded_paths(HASS, ADDRESS, [SALON, PARENTS])

    assert set(charged) == {BUANDERIE, VALENTINE}
    assert _ranking(paths)[0] == SALON


def test_a_second_call_charges_nothing_more() -> None:
    paths = _manon()
    with patch(PATCH_TARGET, return_value=paths):
        steer_off_unbonded_paths(HASS, ADDRESS, [VALENTINE])
        charged = dict(paths[0].scanner._connect_failures)
        assert steer_off_unbonded_paths(HASS, ADDRESS, [VALENTINE]) == []
    assert paths[0].scanner._connect_failures == charged


def test_nothing_moves_when_no_bonded_proxy_hears_it() -> None:
    paths = [_path(BUANDERIE, -71, slots=4, free=3), _path(SALON, -85)]
    with patch(PATCH_TARGET, return_value=paths):
        assert steer_off_unbonded_paths(HASS, ADDRESS, [VALENTINE]) == []
    assert paths[0].scanner._connect_failures == {}


def test_nothing_moves_when_the_bonded_proxy_is_full() -> None:
    paths = [_path(BUANDERIE, -71, slots=4, free=3), _path(VALENTINE, -81, free=0)]
    with patch(PATCH_TARGET, return_value=paths):
        assert steer_off_unbonded_paths(HASS, ADDRESS, [VALENTINE]) == []


def test_an_unrestricted_attempt_is_left_alone() -> None:
    """None is a pairing window or a fresh install: any proxy may be used."""
    paths = _manon()
    with patch(PATCH_TARGET, return_value=paths) as scan:
        assert steer_off_unbonded_paths(HASS, ADDRESS, None) == []
    scan.assert_not_called()


def test_a_bonded_favourite_is_not_touched() -> None:
    paths = [_path(VALENTINE, -70), _path(BUANDERIE, -90, slots=4, free=3)]
    with patch(PATCH_TARGET, return_value=paths):
        assert steer_off_unbonded_paths(HASS, ADDRESS, [VALENTINE]) == []


def test_a_pairing_window_takes_the_charges_back() -> None:
    """Valentine (2026-11-04): its own proxy lost its bond and was charged; a
    user re-pairing must reach that proxy again, not the weak bonded one."""
    paths = _manon()
    with patch.object(util, "_charged", {}):
        with patch(PATCH_TARGET, return_value=paths):
            steer_off_unbonded_paths(HASS, ADDRESS, [VALENTINE])
        assert lift_unbonded_steering(ADDRESS) > 0
        assert paths[0].scanner._connect_failures == {}
        assert _ranking(paths)[0] == BUANDERIE
        assert lift_unbonded_steering(ADDRESS) == 0


def test_lifting_leaves_habluetooths_own_failures() -> None:
    paths = _manon()
    with patch.object(util, "_charged", {}):
        with patch(PATCH_TARGET, return_value=paths):
            steer_off_unbonded_paths(HASS, ADDRESS, [VALENTINE])
        ours = paths[0].scanner._connect_failures[ADDRESS]
        paths[0].scanner._connect_failures[ADDRESS] += 2  # a real failed connect
        assert lift_unbonded_steering(ADDRESS) >= ours
        assert paths[0].scanner._connect_failures == {ADDRESS: 2}


def test_lifting_skips_a_counter_habluetooth_reset() -> None:
    """A success through the proxy cleared the counter: nothing of ours is
    left, so nothing is subtracted from what came after."""
    paths = _manon()
    with patch.object(util, "_charged", {}):
        with patch(PATCH_TARGET, return_value=paths):
            steer_off_unbonded_paths(HASS, ADDRESS, [VALENTINE])
        paths[0].scanner._connect_failures[ADDRESS] = 1
        lift_unbonded_steering(ADDRESS)
        assert paths[0].scanner._connect_failures == {ADDRESS: 1}


def test_a_missing_counter_is_reported_once_and_changes_nothing(caplog) -> None:
    paths = [_path(BUANDERIE, -71, slots=4, free=3, failures=False), _path(VALENTINE, -81)]
    with (
        patch(PATCH_TARGET, return_value=paths),
        patch.object(util, "_steering_unavailable_warned", False),
    ):
        assert steer_off_unbonded_paths(HASS, ADDRESS, [VALENTINE]) == []
        assert steer_off_unbonded_paths(HASS, ADDRESS, [VALENTINE]) == []
    assert caplog.text.count("no longer exposes") == 1


def _land(paths, source: str) -> None:
    """The proxy's slot report: only `source` holds the thermostat now."""
    for p in paths:
        p.scanner.allocated = [ADDRESS] if p.scanner.source == source else []


def test_a_window_attempt_does_not_land_twice_on_the_same_proxy() -> None:
    """Manon (2026-11-04): three window attempts, three prompts, all through
    atomebuanderie. Each attempt must go somewhere new instead."""
    paths = _manon()
    with patch.object(util, "_charged", {}), patch.object(util, "_window_tried", {}):
        with patch(PATCH_TARGET, return_value=paths):
            _land(paths, BUANDERIE)
            assert steer_off_tried_paths(HASS, ADDRESS) == [BUANDERIE]
            assert _ranking(paths)[0] == VALENTINE

            _land(paths, VALENTINE)
            assert set(steer_off_tried_paths(HASS, ADDRESS)) >= {VALENTINE}
            assert _ranking(paths)[0] == SALON

            _land(paths, SALON)
            steer_off_tried_paths(HASS, ADDRESS)
            assert _ranking(paths)[0] == PARENTS

            # Every proxy tried: nothing left to steer towards.
            _land(paths, PARENTS)
            assert steer_off_tried_paths(HASS, ADDRESS) == []

        lift_unbonded_steering(ADDRESS)
        assert all(not p.scanner._connect_failures for p in paths)
        assert _ranking(paths)[0] == BUANDERIE
        assert util._window_tried == {}


def test_a_window_charges_nothing_when_no_proxy_reports_the_link() -> None:
    paths = _manon()
    with (
        patch.object(util, "_charged", {}),
        patch.object(util, "_window_tried", {}),
        patch(PATCH_TARGET, return_value=paths),
    ):
        assert steer_off_tried_paths(HASS, ADDRESS) == []
    assert all(not p.scanner._connect_failures for p in paths)


def test_a_proxy_full_only_because_of_this_attempt_is_still_moved() -> None:
    """AtomeValentine has two slots: Valentine holds one, this attempt the
    other, so the scorer sees it full. The slot frees before the next try."""
    paths = [
        _path(VALENTINE, -70, slots=2, free=0),
        _path(BUANDERIE, -75, slots=4, free=3),
    ]
    with patch.object(util, "_charged", {}), patch.object(util, "_window_tried", {}):
        with patch(PATCH_TARGET, return_value=paths):
            _land(paths, VALENTINE)
            assert steer_off_tried_paths(HASS, ADDRESS) == [VALENTINE]
        paths[0].scanner._slots = (2, 1)  # the failed attempt let go
        assert _ranking(paths)[0] == BUANDERIE


def test_an_unbonded_proxy_full_only_because_of_this_attempt_is_moved() -> None:
    """Manon (2026-11-06, 18:46): bonded to AtomeParents only. AtomeValentine
    has two slots, Valentine holds one and the vetoed attempt the other, so the
    scorer saw it full and nothing was charged: every attempt returned there."""
    paths = [
        _path(BUANDERIE, -69, slots=4, free=3, failures={ADDRESS: 55}),
        _path(VALENTINE, -75, slots=2, free=0),
        _path(PARENTS, -88),
    ]
    with patch.object(util, "_charged", {}), patch(PATCH_TARGET, return_value=paths):
        _land(paths, VALENTINE)
        assert steer_off_unbonded_paths(HASS, ADDRESS, [PARENTS]) == [VALENTINE]
    paths[1].scanner._slots = (2, 1)  # the vetoed link let go
    assert _ranking(paths)[0] == PARENTS


def test_an_unbonded_proxy_full_of_other_links_is_left_alone() -> None:
    """Never chosen while it is full, and nothing says when it will free up."""
    paths = [
        _path(VALENTINE, -75, slots=2, free=0),
        _path(PARENTS, -88),
    ]
    with patch.object(util, "_charged", {}), patch(PATCH_TARGET, return_value=paths):
        _land(paths, PARENTS)
        assert steer_off_unbonded_paths(HASS, ADDRESS, [PARENTS]) == []
    assert not paths[0].scanner._connect_failures
