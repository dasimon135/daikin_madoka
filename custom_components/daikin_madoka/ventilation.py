"""Ventilation feature (function 0x0031) for VAM units.

pymadoka-ng has no class for this function: it models fan speed with
`FanSpeed`, which is function 0x0050 — the function a *thermostat* uses. A VAM
answers 0x0050, but every argument comes back with length 0 and none of them
ever change, so on a VAM that feature can neither read nor write anything.

Live capture against a VAM350J8VEB behind a BRC1H (firmware 1.10.3) showed the
unit keeps its ventilation state on function 0x0031 instead, written with
0x4031:

    16 00 00 31 12 01 07 13 01 08 15 01 10 16 01 10 20 01 00 21 01 01
    |     |     `- arguments, each <id><len><value...>
    |     `- function id
    `- frame length, counting this byte

    arg 0x12  bitmask of the ventilation modes the unit supports (0x07 here)
    arg 0x20  ventilation mode: 0 auto, 1 heat exchange, 2 bypass
    arg 0x21  fan speed, same 0/1/3/5 encoding as function 0x0050

See docs/reverse-engineering-vam.md for the full capture and the write tests.
"""

from enum import Enum

from pymadoka.connection import Connection
from pymadoka.feature import Feature, FeatureStatus
from pymadoka.features.fanspeed import FanSpeedEnum


class VentilationModeEnum(Enum):
    """How the VAM routes air: through the exchanger, around it, or its choice."""

    AUTO = 0
    HEAT_EXCHANGE = 1
    BYPASS = 2

    def __str__(self) -> str:
        """Return the member name, as pymadoka's own enums do."""
        return self.name


def _decode_fan_speed(raw: int) -> FanSpeedEnum | None:
    """Map a raw 0x21 value, mirroring how pymadoka widens MID."""
    if 2 <= raw <= 4:
        return FanSpeedEnum.MID
    try:
        return FanSpeedEnum(raw)
    except ValueError:
        return None


class VentilationStatus(FeatureStatus):
    """Ventilation mode and fan speed of a VAM.

    Both fields are optional and only the ones that are set are serialized, so
    a write touches exactly the argument it means to change. That matters: the
    unit applies whatever it is sent and never reports a rejection, so sending
    a stale companion argument would quietly overwrite it.
    """

    SUPPORTED_MODES_IDX = 0x12
    MODE_IDX = 0x20
    FAN_SPEED_IDX = 0x21

    def __init__(
        self,
        ventilation_mode: VentilationModeEnum | None = None,
        fan_speed: FanSpeedEnum | None = None,
    ) -> None:
        """Init with the values to write, or none of them to build a query."""
        self.ventilation_mode = ventilation_mode
        self.fan_speed = fan_speed
        self.supported_modes: int | None = None

    def set_values(self, values: dict[int, bytearray]) -> None:
        """See base class.

        Unknown values are stored as None rather than raised: a single
        unrecognised byte must not abort the poll for every other feature.
        """
        if (raw := values.get(self.MODE_IDX)) is not None:
            try:
                self.ventilation_mode = VentilationModeEnum(
                    int.from_bytes(raw, "big")
                )
            except ValueError:
                self.ventilation_mode = None

        if (raw := values.get(self.FAN_SPEED_IDX)) is not None:
            self.fan_speed = _decode_fan_speed(int.from_bytes(raw, "big"))

        if (raw := values.get(self.SUPPORTED_MODES_IDX)) is not None:
            self.supported_modes = int.from_bytes(raw, "big")

    def get_values(self) -> dict[int, bytearray]:
        """See base class."""
        values: dict[int, bytearray] = {}
        if self.ventilation_mode is not None:
            values[self.MODE_IDX] = bytearray(
                self.ventilation_mode.value.to_bytes(1, "big")
            )
        if self.fan_speed is not None:
            values[self.FAN_SPEED_IDX] = bytearray(
                self.fan_speed.value.to_bytes(1, "big")
            )
        return values

    def supports_mode(self, mode: VentilationModeEnum) -> bool:
        """Whether argument 0x12 advertises `mode`; assume yes if unreported."""
        if self.supported_modes is None:
            return True
        return bool(self.supported_modes & (1 << mode.value))


class Ventilation(Feature):
    """Read and write the ventilation state of a VAM."""

    def __init__(self, connection: Connection) -> None:
        """See base class."""
        self.status: VentilationStatus | None = None
        super().__init__(connection)

    def query_cmd_id(self) -> int:
        """See base class."""
        return 0x0031

    def update_cmd_id(self) -> int:
        """See base class."""
        return 0x4031

    def new_status(self) -> FeatureStatus:
        """See base class."""
        return VentilationStatus()

    async def update(self, update_status: FeatureStatus) -> FeatureStatus:
        """Write the given arguments, then merge them into the cached status.

        The base class replaces `status` wholesale with what was written. A
        write here deliberately carries a single argument, so that would blank
        the field it did not touch until the next poll and the entity would
        flap back to unknown in between.
        """
        previous = self.status
        await super().update(update_status)
        if previous is not None and isinstance(update_status, VentilationStatus):
            if update_status.ventilation_mode is None:
                update_status.ventilation_mode = previous.ventilation_mode
            if update_status.fan_speed is None:
                update_status.fan_speed = previous.fan_speed
            if update_status.supported_modes is None:
                update_status.supported_modes = previous.supported_modes
        return self.status
