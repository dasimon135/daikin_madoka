"""Contract tests for the VAM ventilation feature (function 0x0031).

The feature itself moved into pymadoka-ng 0.4.0; what is pinned here is the
contract this integration depends on, against frames captured from a real
VAM350J8VEB. It is deliberately not deleted along with the local module: if a
library release ever changes the wire format or the merge-on-write behaviour,
this suite is what notices before a VAM owner does.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymadoka import (
    DEVICE_TYPE_THERMOSTAT as LIB_DEVICE_TYPE_THERMOSTAT,
)
from pymadoka import (
    DEVICE_TYPE_VENTILATION as LIB_DEVICE_TYPE_VENTILATION,
)
from pymadoka import (
    FanSpeedEnum,
    Ventilation,
    VentilationModeEnum,
    VentilationStatus,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.daikin_madoka import async_setup_entry
from custom_components.daikin_madoka.const import (
    CONF_DEVICE_TYPE,
    CONF_MAC,
    DEVICE_TYPE_THERMOSTAT,
    DEVICE_TYPE_VENTILATION,
    DOMAIN,
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


# --- The controller now picks its own features from the device type ---------


async def _device_type_passed_to_controller(
    hass: HomeAssistant, entry_device_type: str
) -> str:
    """Run the component's own async_setup_entry and capture the kwargs.

    Deliberately not through ``hass.config_entries.async_setup``: that starts
    the Bluetooth stack, which then fails at teardown with a scanner this test
    never wanted. The platforms are forwarded to a stub for the same reason.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: "D0:CF:13:0F:11:F6", CONF_DEVICE_TYPE: entry_device_type},
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.daikin_madoka.Controller", return_value=MagicMock()
        ) as controller_cls,
        patch(
            "custom_components.daikin_madoka.MadokaCoordinator"
        ) as coordinator_cls,
        patch.object(
            hass.config_entries, "async_forward_entry_setups", AsyncMock()
        ),
    ):
        coordinator_cls.return_value.async_config_entry_first_refresh = AsyncMock()
        assert await async_setup_entry(hass, entry) is True

    assert controller_cls.call_args is not None
    return controller_cls.call_args.kwargs["device_type"]


async def test_a_ventilation_entry_asks_for_a_ventilation_controller(
    hass: HomeAssistant,
) -> None:
    """0x0031 is the library's job since 0.4.0; the entry only says which
    kind of unit this is."""
    assert (
        await _device_type_passed_to_controller(hass, DEVICE_TYPE_VENTILATION)
        == LIB_DEVICE_TYPE_VENTILATION
    )


async def test_a_thermostat_entry_asks_for_a_thermostat_controller(
    hass: HomeAssistant,
) -> None:
    assert (
        await _device_type_passed_to_controller(hass, DEVICE_TYPE_THERMOSTAT)
        == LIB_DEVICE_TYPE_THERMOSTAT
    )
