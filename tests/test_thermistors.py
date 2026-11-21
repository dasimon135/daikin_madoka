"""Unit status thermistors: read only when enabled, at most every 300 s (#162)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pymadoka import ConnectionException

from custom_components.daikin_madoka.const import (
    THERMISTOR_MAX_AGE,
    THERMISTOR_SCAN_INTERVAL,
)
from custom_components.daikin_madoka.coordinator import MadokaCoordinator

MONO = "custom_components.daikin_madoka.coordinator.monotonic"


def _coordinator(query: AsyncMock | None) -> MadokaCoordinator:
    coordinator = object.__new__(MadokaCoordinator)
    coordinator.controller = MagicMock()
    coordinator.controller.connection.address = "D0:CF:13:0F:11:F6"
    if query is not None:
        coordinator.thermistors = MagicMock(
            query=query, status=SimpleNamespace(outdoor_air=10)
        )
    return coordinator


async def test_nothing_is_read_without_an_enabled_entity() -> None:
    coordinator = _coordinator(None)
    await coordinator._async_read_thermistors()
    assert coordinator.thermistor_status is None


async def test_a_read_runs_at_most_every_scan_interval() -> None:
    query = AsyncMock()
    coordinator = _coordinator(query)
    with patch(MONO, return_value=1000.0):
        await coordinator._async_read_thermistors()
    with patch(MONO, return_value=1000.0 + THERMISTOR_SCAN_INTERVAL - 1):
        await coordinator._async_read_thermistors()
    assert query.await_count == 1
    with patch(MONO, return_value=1000.0 + THERMISTOR_SCAN_INTERVAL):
        await coordinator._async_read_thermistors()
    assert query.await_count == 2


async def test_a_failed_read_is_retried_next_poll_and_does_not_raise() -> None:
    query = AsyncMock(side_effect=[TimeoutError(), ConnectionException("x"), None])
    coordinator = _coordinator(query)
    with patch(MONO, return_value=1000.0):
        await coordinator._async_read_thermistors()
        assert coordinator.thermistor_status is None
        await coordinator._async_read_thermistors()
        await coordinator._async_read_thermistors()
        assert query.await_count == 3
        assert coordinator.thermistor_status is not None


async def test_values_expire_after_max_age() -> None:
    coordinator = _coordinator(AsyncMock())
    with patch(MONO, return_value=1000.0):
        await coordinator._async_read_thermistors()
    with patch(MONO, return_value=1000.0 + THERMISTOR_MAX_AGE):
        assert coordinator.thermistor_status.outdoor_air == 10
    with patch(MONO, return_value=1000.0 + THERMISTOR_MAX_AGE + 1):
        assert coordinator.thermistor_status is None
