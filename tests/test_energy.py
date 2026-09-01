"""Tests for Madoka energy consumption polling."""

import asyncio
from datetime import UTC, date, datetime
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
    with (
        patch("custom_components.daikin_madoka.coordinator.monotonic", new=clock),
        patch(
            "custom_components.daikin_madoka.coordinator.dt_util.now",
            return_value=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        ),
    ):
        status = await feature.query()

        clock.now += ENERGY_SCAN_INTERVAL - 0.001
        cached = await feature.query()

        clock.now += 0.001
        refreshed = await feature.query()

    assert status.energy_today == (12.3,)
    assert cached is status
    assert refreshed is not status
    initial_query = [
        call(
            ENERGY_PRIVILEGE_COMMAND,
            bytearray((ENERGY_PRIVILEGE_PARAMETER, 1, 1)),
        ),
        *(
            call(ENERGY_CONSUMPTION_COMMAND, bytearray((parameter, 0)))
            for parameter in ENERGY_PARAMETERS.values()
        ),
    ]
    today_query = [
        call(
            ENERGY_PRIVILEGE_COMMAND,
            bytearray((ENERGY_PRIVILEGE_PARAMETER, 1, 1)),
        ),
        call(
            ENERGY_CONSUMPTION_COMMAND,
            bytearray((ENERGY_PARAMETERS["energy_today"], 0)),
        ),
    ]
    assert connection.send.await_args_list == initial_query + today_query
    assert refreshed.energy_yesterday == status.energy_yesterday
    assert feature._period_day == date(2026, 8, 25)


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


async def test_period_counters_refresh_without_rereading_today() -> None:
    """The five informational periods use their independent daily cadence."""
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
                bytearray((11, 0, 1, 32, parameter, 4, 123, 0, 0, 0))
            )
        return future

    connection.send = AsyncMock(side_effect=_response)
    feature = MadokaEnergyConsumption(connection)
    feature.status = MadokaEnergyStatus()
    feature.status.energy_today = (4.2,)
    feature._next_today_query = 200.0
    feature._period_day = date(2026, 8, 24)

    with (
        patch(
            "custom_components.daikin_madoka.coordinator.monotonic",
            return_value=100.0,
        ),
        patch(
            "custom_components.daikin_madoka.coordinator.dt_util.now",
            return_value=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        ),
    ):
        status = await feature.query()

    period_parameters = [
        parameter
        for period, parameter in ENERGY_PARAMETERS.items()
        if period != "energy_today"
    ]
    assert connection.send.await_args_list == [
        call(
            ENERGY_PRIVILEGE_COMMAND,
            bytearray((ENERGY_PRIVILEGE_PARAMETER, 1, 1)),
        ),
        *(
            call(ENERGY_CONSUMPTION_COMMAND, bytearray((parameter, 0)))
            for parameter in period_parameters
        ),
    ]
    assert status.energy_today == (4.2,)
    assert status.energy_yesterday == (12.3,)
    assert feature._next_today_query == 200.0
    assert feature._period_day == date(2026, 8, 25)


async def test_period_counters_refresh_after_midnight_grace() -> None:
    """A slow thermostat must reset before the new day is cached."""
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
                bytearray((11, 0, 1, 32, parameter, 4, 123, 0, 0, 0))
            )
        return future

    connection.send = AsyncMock(side_effect=_response)
    feature = MadokaEnergyConsumption(connection)
    feature.status = MadokaEnergyStatus()
    feature._next_today_query = 200.0
    feature._period_day = date(2026, 8, 24)

    with (
        patch(
            "custom_components.daikin_madoka.coordinator.monotonic",
            return_value=100.0,
        ),
        patch(
            "custom_components.daikin_madoka.coordinator.dt_util.now",
            side_effect=(
                datetime(2026, 8, 25, 0, 2, tzinfo=UTC),
                datetime(2026, 8, 25, 0, 5, tzinfo=UTC),
            ),
        ),
    ):
        cached = await feature.query()
        refreshed = await feature.query()

    assert cached is not refreshed
    assert connection.send.await_count == 6
    assert connection.send.await_args_list[0] == call(
        ENERGY_PRIVILEGE_COMMAND,
        bytearray((ENERGY_PRIVILEGE_PARAMETER, 1, 1)),
    )
    assert all(
        request.args[1][0] != ENERGY_PARAMETERS["energy_today"]
        for request in connection.send.await_args_list[1:]
    )
    assert feature._period_day == date(2026, 8, 25)


async def test_energy_response_accepts_missing_trailing_breakdown_slots() -> None:
    """The total remains usable when trailing period slots are omitted."""
    connection = MagicMock()
    connection.connection_status = ConnectionStatus.CONNECTED
    connection._operation_lock = asyncio.Lock()

    def _response(command, payload) -> asyncio.Future[bytearray]:
        future = asyncio.get_running_loop().create_future()
        if command == ENERGY_PRIVILEGE_COMMAND:
            future.set_result(bytearray())
        else:
            future.set_result(
                bytearray((58, 0, 1, 32, payload[0], 52, 123, 0, 0, 0))
            )
        return future

    connection.send = AsyncMock(side_effect=_response)
    feature = MadokaEnergyConsumption(connection)

    status = await feature.query()

    assert status.energy_today == (12.3,)
    assert feature.cache_is_fresh


async def test_energy_response_without_complete_total_is_not_cached() -> None:
    """A partial total must not become five minutes of invalid data."""
    connection = MagicMock()
    connection.connection_status = ConnectionStatus.CONNECTED
    connection._operation_lock = asyncio.Lock()

    def _response(command, payload) -> asyncio.Future[bytearray]:
        future = asyncio.get_running_loop().create_future()
        if command == ENERGY_PRIVILEGE_COMMAND:
            future.set_result(bytearray())
        else:
            future.set_result(
                bytearray((58, 0, 1, 32, payload[0], 52, 123, 0, 0))
            )
        return future

    connection.send = AsyncMock(side_effect=_response)
    feature = MadokaEnergyConsumption(connection)

    with pytest.raises(ValueError, match="omitted parameter"):
        await feature.query()

    assert feature.status is None
    assert not feature.cache_is_fresh


async def test_empty_today_disables_future_energy_queries(caplog) -> None:
    """A unit without counters is probed once, then left alone."""
    connection = MagicMock()
    connection.connection_status = ConnectionStatus.CONNECTED
    connection._operation_lock = asyncio.Lock()

    def _response(command, payload) -> asyncio.Future[bytearray]:
        future = asyncio.get_running_loop().create_future()
        if command == ENERGY_PRIVILEGE_COMMAND:
            future.set_result(bytearray())
        else:
            future.set_result(bytearray((6, 0, 1, 32, payload[0], 0)))
        return future

    connection.send = AsyncMock(side_effect=_response)
    feature = MadokaEnergyConsumption(connection)

    status = await feature.query()
    cached = await feature.query()

    assert feature.supported is False
    assert feature.cache_is_fresh
    assert cached is status
    assert connection.send.await_args_list == [
        call(
            ENERGY_PRIVILEGE_COMMAND,
            bytearray((ENERGY_PRIVILEGE_PARAMETER, 1, 1)),
        ),
        call(
            ENERGY_CONSUMPTION_COMMAND,
            bytearray((ENERGY_PARAMETERS["energy_today"], 0)),
        ),
    ]
    assert "disabling energy polling" in caplog.text


async def test_cached_energy_does_not_count_as_a_device_response() -> None:
    """A cache hit must not hide failure of every feature that touched BLE."""
    controller = MagicMock()
    controller.connection.connection_status = ConnectionStatus.CONNECTED
    controller.connection.address = "D0:CF:13:0F:11:F6"
    energy = MadokaEnergyConsumption(controller.connection)
    energy.status = MadokaEnergyStatus()
    energy._next_today_query = 200.0
    energy._period_day = date(2026, 8, 25)
    controller.energy_consumption = energy

    async def _no_feature_answered() -> None:
        assert controller.energy_consumption is None
        raise ConnectionException("No feature answered any query")

    controller.update = AsyncMock(side_effect=_no_feature_answered)
    coordinator = object.__new__(MadokaCoordinator)
    coordinator.controller = controller

    with (
        patch("custom_components.daikin_madoka.coordinator.monotonic", return_value=100.0),
        patch(
            "custom_components.daikin_madoka.coordinator.dt_util.now",
            return_value=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        ),
        pytest.raises(UpdateFailed, match="No feature answered any query"),
    ):
        await coordinator._async_poll()

    assert controller.energy_consumption is energy


def test_energy_polling_is_opt_in() -> None:
    """Energy commands are attached to the controller only when enabled."""
    controller = MagicMock()
    controller.energy_consumption = None
    coordinator = object.__new__(MadokaCoordinator)
    coordinator.controller = controller

    coordinator.async_apply_energy_enabled(False)
    assert controller.energy_consumption is None

    coordinator.async_apply_energy_enabled(True)
    assert isinstance(controller.energy_consumption, MadokaEnergyConsumption)

    coordinator.async_apply_energy_enabled(False)
    assert controller.energy_consumption is None
