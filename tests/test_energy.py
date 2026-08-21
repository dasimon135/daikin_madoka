"""Tests for Madoka energy consumption polling."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, call

from pymadoka.connection import ConnectionStatus

from custom_components.daikin_madoka.const import (
    ENERGY_CONSUMPTION_COMMAND,
    ENERGY_PARAMETERS,
    ENERGY_PRIVILEGE_COMMAND,
    ENERGY_PRIVILEGE_PARAMETER,
)
from custom_components.daikin_madoka.coordinator import (
    MadokaEnergyConsumption,
    MadokaEnergyStatus,
)


def test_energy_status_decodes_totals_and_breakdowns() -> None:
    """Madoka reports little-endian counters in tenths of a kWh."""
    status = MadokaEnergyStatus()
    status.set_values(
        {
            ENERGY_PARAMETERS["energy_today"]: bytearray(
                b"{\x00\x00\x00\n\x00\x00\x00"
            ),
            ENERGY_PARAMETERS["energy_this_week"]: bytearray(b"\xc8\x01\x00\x00"),
        }
    )

    assert status.energy_today == (12.3, 1.0)
    assert status.energy_this_week == (45.6,)
    assert status.energy_yesterday is None


def test_energy_status_requests_all_periods() -> None:
    """One query asks the thermostat for every available counter period."""
    assert MadokaEnergyStatus().serialize() == bytearray(
        parameter
        for parameter_id in ENERGY_PARAMETERS.values()
        for parameter in (parameter_id, 0)
    )


async def test_energy_query_uses_the_existing_authenticated_connection() -> None:
    """Energy access is enabled and read on pymadoka's existing connection."""
    connection = MagicMock()
    connection.connection_status = ConnectionStatus.CONNECTED
    connection._operation_lock = asyncio.Lock()

    def _response(*_args) -> asyncio.Future[bytearray]:
        future = asyncio.get_running_loop().create_future()
        future.set_result(bytearray(b"\n\x00\x01 \x40\x04{\x00\x00\x00"))
        return future

    connection.send = AsyncMock(side_effect=_response)
    status = await MadokaEnergyConsumption(connection).query()

    assert status.energy_today == (12.3,)
    assert connection.send.await_args_list == [
        call(
            ENERGY_PRIVILEGE_COMMAND,
            bytearray((ENERGY_PRIVILEGE_PARAMETER, 1, 1)),
        ),
        call(ENERGY_CONSUMPTION_COMMAND, MadokaEnergyStatus().serialize()),
    ]
