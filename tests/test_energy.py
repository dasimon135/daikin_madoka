"""Tests for Madoka energy consumption polling."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from pymadoka import ConnectionException
from pymadoka.connection import ConnectionStatus

from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.daikin_madoka.const import (
    ENERGY_CONSUMPTION_COMMAND,
    ENERGY_PARAMETERS,
    ENERGY_PRIVILEGE_COMMAND,
    ENERGY_PRIVILEGE_PARAMETER,
    ENERGY_SCAN_INTERVAL,
)
from custom_components.daikin_madoka.coordinator import (
    MadokaCoordinator,
    MadokaEnergyConsumption,
    MadokaEnergyStatus,
)


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


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


async def test_energy_query_uses_the_existing_authenticated_connection() -> None:
    """Energy access is enabled and read on pymadoka's existing connection."""
    connection = MagicMock()
    connection.connection_status = ConnectionStatus.CONNECTED
    connection._operation_lock = asyncio.Lock()

    def _response(command, payload) -> asyncio.Future[bytearray]:
        future = asyncio.get_running_loop().create_future()
        if command == ENERGY_PRIVILEGE_COMMAND:
            future.set_result(bytearray())
        else:
            parameter = payload[0]
            future.set_result(
                # Real Madoka energy frames overstate their rebuilt length.
                bytearray((11, 0, 1, 32, parameter, 4, 123, 0, 0, 0))
            )
        return future

    connection.send = AsyncMock(side_effect=_response)
    feature = MadokaEnergyConsumption(connection)
    clock = _Clock(100.0)
    with patch("custom_components.daikin_madoka.coordinator.monotonic", new=clock):
        status = await feature.query()

        clock.now += ENERGY_SCAN_INTERVAL - 0.001
        cached = await feature.query()

        clock.now += 0.001
        refreshed = await feature.query()

    assert status.energy_today == (12.3,)
    assert cached is status
    assert refreshed is not status
    expected_query = [
        call(
            ENERGY_PRIVILEGE_COMMAND,
            bytearray((ENERGY_PRIVILEGE_PARAMETER, 1, 1)),
        ),
        *(
            call(ENERGY_CONSUMPTION_COMMAND, bytearray((parameter, 0)))
            for parameter in ENERGY_PARAMETERS.values()
        ),
    ]
    assert connection.send.await_args_list == expected_query * 2


async def test_energy_timeout_discards_the_pending_pymadoka_request() -> None:
    """Energy reads use pymadoka's timeout-safe request queue path."""
    connection = MagicMock()
    connection.connection_status = ConnectionStatus.CONNECTED
    connection._operation_lock = asyncio.Lock()
    privilege_response = asyncio.get_running_loop().create_future()
    privilege_response.set_result(bytearray())
    energy_response = asyncio.get_running_loop().create_future()
    connection.send = AsyncMock(side_effect=(privilege_response, energy_response))

    with (
        patch(
            "pymadoka.feature.asyncio.wait_for",
            new=AsyncMock(side_effect=(None, TimeoutError())),
        ),
        pytest.raises(TimeoutError),
    ):
        await MadokaEnergyConsumption(connection).query()

    connection.discard_request.assert_called_once_with(
        ENERGY_CONSUMPTION_COMMAND, energy_response
    )


async def test_malformed_energy_response_is_not_cached() -> None:
    """An incomplete protocol frame must not become five minutes of empty data."""
    connection = MagicMock()
    connection.connection_status = ConnectionStatus.CONNECTED
    connection._operation_lock = asyncio.Lock()

    def _response(command, _payload) -> asyncio.Future[bytearray]:
        future = asyncio.get_running_loop().create_future()
        if command == ENERGY_PRIVILEGE_COMMAND:
            future.set_result(bytearray())
        else:
            future.set_result(bytearray((10, 0, 1, 32, 64, 4, 123, 0, 0)))
        return future

    connection.send = AsyncMock(side_effect=_response)
    feature = MadokaEnergyConsumption(connection)

    with pytest.raises(ValueError, match="truncated"):
        await feature.query()

    assert feature.status is None
    assert not feature.cache_is_fresh


async def test_cached_energy_does_not_count_as_a_device_response() -> None:
    """A cache hit must not hide failure of every feature that touched BLE."""
    controller = MagicMock()
    controller.connection.connection_status = ConnectionStatus.CONNECTED
    controller.connection.address = "D0:CF:13:0F:11:F6"
    energy = MadokaEnergyConsumption(controller.connection)
    energy.status = MadokaEnergyStatus()
    energy._next_query = 200.0
    controller.energy_consumption = energy

    async def _no_feature_answered() -> None:
        assert controller.energy_consumption is None
        raise ConnectionException("No feature answered any query")

    controller.update = AsyncMock(side_effect=_no_feature_answered)
    coordinator = object.__new__(MadokaCoordinator)
    coordinator.controller = controller

    with (
        patch("custom_components.daikin_madoka.coordinator.monotonic", return_value=100.0),
        pytest.raises(UpdateFailed, match="No feature answered any query"),
    ):
        await coordinator._async_poll()

    assert controller.energy_consumption is energy
