"""Support for Daikin Madoka sensors."""

from dataclasses import dataclass
from datetime import datetime
from typing import Final

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
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_PREFERRED_SOURCE, ENERGY_PARAMETERS
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
        entities.extend(
            MadokaEnergySensor(coordinator, description)
            for description in ENERGY_SENSORS
        )
        entities.append(MadokaConnectionSourceSensor(coordinator))
        entities.append(MadokaConnectionStatusSensor(coordinator))
    async_add_entities(entities)


class MadokaLinkSensor(MadokaEntity, SensorEntity):
    """A diagnostic sensor that does not need the BLE link to answer.

    Signal strength, serving proxy and connection status all come from Home
    Assistant's own Bluetooth bookkeeping or from the coordinator's state, so
    none of them needs a device round-trip. Inheriting the default
    ``available = last_update_success`` made them go unavailable together with
    everything else exactly when they were the only things worth reading — the
    user was left with a wall of "unavailable" and no way to tell "out of
    range" from "bond refused" without opening the logs.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Always: this entity reports ABOUT the link, it does not use it."""
        return True


class MadokaTemperatureSensor(MadokaEntity, SensorEntity):
    """Base representation of a Madoka temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS


@dataclass(frozen=True)
class MadokaEnergySensorDescription:
    """Description of one period reported by the Madoka energy counter."""

    key: str
    translation_key: str


ENERGY_SENSORS: Final = tuple(
    MadokaEnergySensorDescription(key, key) for key in ENERGY_PARAMETERS
)


class MadokaEnergySensor(MadokaEntity, SensorEntity):
    """A consumption total retained by the thermostat itself."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: MadokaCoordinator,
        description: MadokaEnergySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self._attr_translation_key = description.translation_key
        self._period = description.key

    @property
    def native_value(self) -> float | None:
        """Return the period total, if the controller supports energy data."""
        energy = (self.coordinator.data or {}).get("energy_consumption", {})
        values = energy.get(self._period)
        return values[0] if values else None


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


class MadokaRssiSensor(MadokaLinkSensor):
    """Bluetooth signal strength of the thermostat, from HA's BLE tracker."""

    _attr_translation_key = "rssi"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
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


class MadokaConnectionSourceSensor(MadokaLinkSensor):
    """Which BLE path (ESPHome proxy or local adapter) serves the thermostat.

    While connected, reports the live connection's source scanner; otherwise
    falls back to the persisted preferred source (the proxy that last
    authenticated, primed for the next reconnect). Useful in multi-proxy
    homes to see which proxy each thermostat is bonded through.

    Enabled by default: a bond belongs to ONE proxy, so "which proxy" is half
    of every pairing diagnosis, and an entity the user has to go and enable
    first is not available at the moment they need it.
    """

    _attr_translation_key = "connection_source"

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


class MadokaConnectionStatusSensor(MadokaLinkSensor):
    """Why the thermostat is (not) reachable, in one always-readable state.

    Every other signal in the integration collapses to "unavailable" when the
    link is down, which is precisely when the user needs to know WHICH failure
    they are looking at: a thermostat out of range and a thermostat whose bond
    was refused look identical on the dashboard but need opposite remedies
    (move a proxy / stand at the device and re-pair). This is that
    distinction, without opening the logs.
    """

    _attr_translation_key = "connection_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        "connected",
        "retrying",
        "pairing_slow",
        "needs_pairing",
        "not_advertising",
    ]

    def __init__(self, coordinator: MadokaCoordinator) -> None:
        super().__init__(coordinator, "connection_status")

    @property
    def native_value(self) -> str:
        """Return the most specific diagnosis that currently applies."""
        coordinator = self.coordinator
        if (
            self.controller.connection.connection_status is ConnectionStatus.CONNECTED
            and coordinator.last_update_success
        ):
            return "connected"
        # Ordered by how much they are known, not by how bad they are: a
        # proven refusal outranks an inference, which outranks an absence.
        if coordinator.pairing_suspended:
            return "needs_pairing"
        # Only the pairing-timeout verdict says "pairing_slow". The cadence
        # brake also engages on a plain streak of failed polls, and telling the
        # owner of a powered-off thermostat that pairing is slow would send them
        # to re-pair a device that only needs its power back.
        if coordinator.pairing_slow_suspected:
            return "pairing_slow"
        # A BRC1H stops advertising while it is connected, so HA's tracker ages
        # its advert out within minutes of a healthy session. Checking presence
        # before the link state made a CONNECTED device whose poll was failing
        # report "not_advertising" — the exact opposite of the truth, in the one
        # sensor built to tell those two apart.
        if self.controller.connection.connection_status is ConnectionStatus.CONNECTED:
            return "retrying"
        if not bluetooth.async_address_present(
            self.hass, coordinator.address, connectable=True
        ):
            return "not_advertising"
        return "retrying"


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
