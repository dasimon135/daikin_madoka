"""A feature that stops answering must not freeze its entities forever.

pymadoka's Controller.update swallows a failing feature and counts the poll
as good as long as any other feature answered, and the coordinator only fails
a poll when NOTHING answered. A feature that kept failing therefore froze its
entities at the last value it ever reported, displayed as current and
"available", for as long as the rest of the thermostat kept talking.

After FEATURE_MISS_LIMIT consecutive polls without an answer, the feature is
dropped: its status goes to None, so every entity reading it shows unknown,
and it leaves coordinator.data. The next answer brings it back.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pymadoka import Controller
from pymadoka.connection import ConnectionStatus
from pymadoka.feature import Feature, NotImplementedException
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.daikin_madoka.const import CONF_MAC, DOMAIN
from custom_components.daikin_madoka.coordinator import (
    FEATURE_MISS_LIMIT,
    MadokaCoordinator,
)

MAC = "D0:CF:13:0F:11:F6"


def _controller() -> Controller:
    """A real Controller whose features answer (or not) without any BLE."""
    controller = Controller(MAC, reconnect=False)
    controller.connection.connection_status = ConnectionStatus.CONNECTED
    controller.connection.connected_source = None
    # Skip the GATT device-information read: nothing to read it from.
    controller.info = {"Model Number String": "0.1"}
    for name, feature in vars(controller).items():
        if isinstance(feature, Feature):
            _answer(feature, name)
    return controller


def _answer(feature: Feature, value: object) -> None:
    async def _query():
        # A fresh status object per answer, exactly as Feature.query() does.
        feature.status = SimpleNamespace(value=value)
        return feature.status

    feature.query = AsyncMock(side_effect=_query)


def _fail(feature: Feature) -> None:
    feature.query = AsyncMock(side_effect=ValueError("garbled response"))


def _coordinator(hass: HomeAssistant, controller: Controller) -> MadokaCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    entry.add_to_hass(hass)
    token = config_entries.current_entry.set(entry)
    try:
        return MadokaCoordinator(hass, controller, scan_interval=60)
    finally:
        config_entries.current_entry.reset(token)


async def _poll(coordinator: MadokaCoordinator) -> None:
    with patch(
        "homeassistant.components.bluetooth.async_address_present", return_value=True
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success


async def test_a_feature_that_stops_answering_goes_unknown(
    hass: HomeAssistant,
) -> None:
    controller = _controller()
    coordinator = _coordinator(hass, controller)

    await _poll(coordinator)
    assert coordinator.data["temperatures"] == {"value": "temperatures"}

    _fail(controller.temperatures)

    # A miss or two is served from the last answer: one garbled response must
    # not blank a sensor.
    for _ in range(FEATURE_MISS_LIMIT - 1):
        await _poll(coordinator)
        assert coordinator.data["temperatures"] == {"value": "temperatures"}
        assert controller.temperatures.status is not None

    # Past the limit the value is no longer presented as current.
    await _poll(coordinator)
    assert "temperatures" not in coordinator.data
    assert controller.temperatures.status is None
    # Everything that did answer is untouched.
    assert coordinator.data["set_point"] == {"value": "set_point"}

    # And it comes straight back with the next answer.
    _answer(controller.temperatures, "back")
    await _poll(coordinator)
    assert coordinator.data["temperatures"] == {"value": "back"}


async def test_an_intermittent_feature_is_never_dropped(hass: HomeAssistant) -> None:
    """Only CONSECUTIVE misses count: any answer resets the streak."""
    controller = _controller()
    coordinator = _coordinator(hass, controller)
    await _poll(coordinator)

    for _ in range(3):
        _fail(controller.temperatures)
        for _ in range(FEATURE_MISS_LIMIT - 1):
            await _poll(coordinator)
        _answer(controller.temperatures, "temperatures")
        await _poll(coordinator)

    assert coordinator.data["temperatures"] == {"value": "temperatures"}


async def test_a_fresh_energy_cache_is_not_a_miss(hass: HomeAssistant) -> None:
    """The energy feature is deliberately not queried while its cache is fresh."""
    controller = _controller()
    coordinator = _coordinator(hass, controller)
    coordinator.async_apply_energy_enabled(True)
    energy = controller.energy_consumption
    energy.status = SimpleNamespace(energy_today=(1.0,))
    energy.supported = True

    with patch.object(
        type(energy), "cache_is_fresh", new_callable=lambda: property(lambda _: True)
    ):
        for _ in range(FEATURE_MISS_LIMIT + 1):
            await _poll(coordinator)

    assert energy.status is not None
    assert coordinator.data["energy_consumption"] == {"energy_today": (1.0,)}


async def test_a_write_only_feature_is_not_a_silent_one(
    hass: HomeAssistant, caplog
) -> None:
    """The filter-reset feature cannot be queried at all, by design.

    A reset press leaves a status on it (Feature.update sets one), and every
    poll then "misses" it. That is not a device failing to answer.
    """
    controller = _controller()
    reset = controller.reset_clean_filter_timer
    reset.query = AsyncMock(side_effect=NotImplementedException("write only"))
    coordinator = _coordinator(hass, controller)
    reset.status = SimpleNamespace(reset=True)

    for _ in range(FEATURE_MISS_LIMIT + 1):
        await _poll(coordinator)

    assert reset.status is not None
    assert "has not answered" not in caplog.text