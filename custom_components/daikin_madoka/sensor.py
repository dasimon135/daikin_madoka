"""Support for Daikin Madoka sensors."""

from datetime import datetime

from pymadoka.connection import ConnectionStatus

from homeassistant.components import bluetooth
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_PREFERRED_SOURCE
from .coordinator import MadokaConfigEntry, MadokaCoordinator
from .entity import MadokaEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MadokaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Daikin sensors based on config_entry."""
    entities: list[MadokaEntity] = []
    for coordinator in entry.runtime_data.values():
        entities.append(MadokaIndoorSensor(coordinator))
        entities.append(MadokaOutdoorSensor(coordinator))
        entities.append(MadokaRssiSensor(coordinator))
        entities.append(MadokaRuntimeSensor(coordinator))
        entities.append(MadokaConnectionSourceSensor(coordinator))
    async_add_entities(entities)


class MadokaTemperatureSensor(MadokaEntity, SensorEntity):
    """Base representation of a Madoka temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS


class MadokaIndoorSensor(MadokaTemperatureSensor):
    """Indoor temperature sensor."""

    _attr_translation_key = "indoor_temperature"

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "indoor_temperature")

    @property
    def native_value(self) -> float | None:
        """Return the indoor temperature."""
        if self.controller.temperatures.status is None:
            return None
        return self.controller.temperatures.status.indoor


class MadokaOutdoorSensor(MadokaTemperatureSensor):
    """Outdoor temperature sensor."""

    _attr_translation_key = "outdoor_temperature"

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "outdoor_temperature")

    @property
    def native_value(self) -> float | None:
        """Return the outdoor temperature."""
        if self.controller.temperatures.status is None:
            return None
        return self.controller.temperatures.status.outdoor


class MadokaRssiSensor(MadokaEntity, SensorEntity):
    """Bluetooth signal strength of the thermostat, from HA's BLE tracker."""

    _attr_translation_key = "rssi"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "rssi")

    @property
    def native_value(self) -> int | None:
        """Return the RSSI of the last advertisement seen by HA."""
        service_info = bluetooth.async_last_service_info(
            self.hass, self.coordinator.address, connectable=True
        )
        if service_info is None:
            return None
        return service_info.rssi


class MadokaConnectionSourceSensor(MadokaEntity, SensorEntity):
    """Which BLE path (ESPHome proxy or local adapter) serves the thermostat.

    While connected, reports the live connection's source scanner; otherwise
    falls back to the persisted preferred source (the proxy that last
    authenticated, primed for the next reconnect). Useful in multi-proxy
    homes to see which proxy each thermostat is bonded through.
    """

    _attr_translation_key = "connection_source"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "connection_source")

    def _display_name(self, source: str) -> str:
        """Resolve a proxy source MAC to its scanner name when known."""
        scanner = bluetooth.async_scanner_by_source(self.hass, source)
        return getattr(scanner, "name", None) or source

    @property
    def native_value(self) -> str | None:
        """Return the active or preferred BLE source."""
        connection = self.controller.connection
        if connection.connection_status is ConnectionStatus.CONNECTED:
            source = connection.connected_source
            # None means the local adapter (or a backend that does not
            # report a source).
            return self._display_name(source) if source else "Local adapter"
        entry = self.coordinator.config_entry
        preferred = entry.data.get(CONF_PREFERRED_SOURCE) if entry else None
        return self._display_name(preferred) if preferred else None


class MadokaRuntimeSensor(MadokaEntity, RestoreSensor):
    """Estimated time the unit has been running (powered on).

    A cumulative counter derived from the poll cadence: each interval the unit
    was on is added. Coarse (poll-interval granularity) but useful for a
    rough usage / energy proxy. Persisted across restarts.
    """

    _attr_translation_key = "operating_time"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "operating_time")
        self._hours = 0.0
        self._last_ts: datetime | None = None
        self._prev_on = False

    async def async_added_to_hass(self) -> None:
        """Restore the accumulated value and start timing."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                self._hours = float(str(last.native_value))
            except (TypeError, ValueError):
                self._hours = 0.0
        self._last_ts = dt_util.utcnow()
        self._prev_on = self._is_on()

    def _is_on(self) -> bool:
        ps = self.controller.power_state.status
        return bool(
            self.coordinator.last_update_success and ps is not None and ps.turn_on
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        now = dt_util.utcnow()
        # Attribute the elapsed interval to the state it held at its start.
        if self._last_ts is not None and self._prev_on:
            self._hours += (now - self._last_ts).total_seconds() / 3600
        self._prev_on = self._is_on()
        self._last_ts = now
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """A cumulative counter stays available through transient failures."""
        return True

    @property
    def native_value(self) -> float:
        """Return accumulated operating hours."""
        return round(self._hours, 3)
