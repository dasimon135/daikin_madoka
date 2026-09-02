"""Tests for the VAM ventilation feature (function 0x0031).

pymadoka-ng has no class for this function, so the wire format is pinned here
against frames captured from a real VAM350J8VEB.
"""

from unittest.mock import AsyncMock

import pytest
from pymadoka import FanSpeedEnum

from custom_components.daikin_madoka.ventilation import (
    Ventilation,
    VentilationModeEnum,
    VentilationStatus,
)

# Captured from the unit while it was off, in auto, running its fan on low.
BASELINE = bytes.fromhex("16000031120107130108150110160110200100210101")


def _feature() -> Ventilation:
    """Feature whose transport is stubbed; only serialization is under test."""
    feature = Ventilation(connection=None)
    feature._send_command = AsyncMock(return_value=BASELINE)
    return feature


def test_command_ids_are_the_captured_ones() -> None:
    feature = Ventilation(connection=None)

    assert feature.query_cmd_id() == 0x0031
    assert feature.update_cmd_id() == 0x4031


def test_parses_a_real_frame() -> None:
    status = VentilationStatus()

    status.parse(bytearray(BASELINE))

    assert status.ventilation_mode is VentilationModeEnum.AUTO
    assert status.fan_speed is FanSpeedEnum.LOW
    assert status.supported_modes == 0x07


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0x00, VentilationModeEnum.AUTO),
        (0x01, VentilationModeEnum.HEAT_EXCHANGE),
        (0x02, VentilationModeEnum.BYPASS),
    ],
)
def test_ventilation_mode_encoding(raw: int, expected: VentilationModeEnum) -> None:
    status = VentilationStatus()

    status.set_values({VentilationStatus.MODE_IDX: bytes([raw])})

    assert status.ventilation_mode is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0x01, FanSpeedEnum.LOW),
        (0x03, FanSpeedEnum.MID),
        (0x05, FanSpeedEnum.HIGH),
        (0x00, FanSpeedEnum.AUTO),
        # pymadoka widens 2..4 to MID; 0x0031 uses the same encoding.
        (0x04, FanSpeedEnum.MID),
    ],
)
def test_fan_speed_encoding(raw: int, expected: FanSpeedEnum) -> None:
    status = VentilationStatus()

    status.set_values({VentilationStatus.FAN_SPEED_IDX: bytes([raw])})

    assert status.fan_speed is expected


def test_unknown_values_do_not_raise() -> None:
    """One odd byte must not abort the poll for every other feature."""
    status = VentilationStatus()

    status.set_values(
        {
            VentilationStatus.MODE_IDX: bytes([0x09]),
            VentilationStatus.FAN_SPEED_IDX: bytes([0x42]),
        }
    )

    assert status.ventilation_mode is None
    assert status.fan_speed is None


def test_a_query_carries_no_arguments() -> None:
    """new_status() doubles as the query payload; it must not assert a state."""
    assert Ventilation(connection=None).new_status().serialize() == bytearray(
        [0x00, 0x00]
    )


def test_a_write_carries_only_the_argument_it_changes() -> None:
    """The unit applies whatever it is sent, so never send a stale companion."""
    assert VentilationStatus(fan_speed=FanSpeedEnum.HIGH).serialize() == bytearray(
        [0x21, 0x01, 0x05]
    )
    assert VentilationStatus(
        ventilation_mode=VentilationModeEnum.BYPASS
    ).serialize() == bytearray([0x20, 0x01, 0x02])


async def test_update_keeps_the_field_it_did_not_write() -> None:
    """A single-argument write must not blank the rest until the next poll."""
    feature = _feature()
    await feature.query()
    assert feature.status.ventilation_mode is VentilationModeEnum.AUTO

    await feature.update(VentilationStatus(fan_speed=FanSpeedEnum.HIGH))

    assert feature.status.fan_speed is FanSpeedEnum.HIGH
    assert feature.status.ventilation_mode is VentilationModeEnum.AUTO
    assert feature.status.supported_modes == 0x07


async def test_query_parses_into_status() -> None:
    feature = _feature()

    status = await feature.query()

    assert status.fan_speed is FanSpeedEnum.LOW
    assert feature.status is status


@pytest.mark.parametrize(
    ("supported", "expected"),
    [
        (0x07, [True, True, True]),
        (0x03, [True, True, False]),
        (None, [True, True, True]),
    ],
)
def test_supports_mode_reads_argument_0x12(
    supported: int | None, expected: list[bool]
) -> None:
    """Argument 0x12 is a bitmask; an unreported one means 'assume supported'."""
    status = VentilationStatus()
    status.supported_modes = supported

    assert [
        status.supports_mode(mode)
        for mode in (
            VentilationModeEnum.AUTO,
            VentilationModeEnum.HEAT_EXCHANGE,
            VentilationModeEnum.BYPASS,
        )
    ] == expected
