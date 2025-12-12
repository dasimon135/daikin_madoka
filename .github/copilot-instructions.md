# Copilot Instructions: Daikin Madoka

## Project Overview
This repository contains **two distinct components** for controlling Daikin Madoka BRC1H Bluetooth thermostats:

1. **Home Assistant Custom Integration** (root directory) - Direct Bluetooth control via `pymadoka`
2. **ESPHome Components** (`esphome_components/`) - ESP32 Bluetooth proxy components for remote control

## Architecture & Key Components

### Home Assistant Integration (Root Directory)

#### Core Files Structure
- `__init__.py` - Main integration setup, controller initialization, and platform forwarding
- `config_flow.py` - Configuration UI flow with Bluetooth device discovery and validation
- `climate.py` - Main climate entity with HVAC control (temperature, mode, fan speed)
- `sensor.py` - Temperature sensor entity for indoor readings
- `const.py` - Domain constants and temperature limits
- `manifest.json` - Integration metadata and dependencies

### ESPHome Components (`esphome_components/`)

#### Structure
- `madoka/` - Climate platform component for Madoka thermostats
  - `__init__.py` - Python configuration validation
  - `climate.py` - Climate entity configuration
  - `madoka.cpp/h` - C++ implementation for ESP32
- `ble_client/` - **Modified** BLE client with ESPHome 2025.10.0+ compatibility
  - `__init__.py` - **Contains `safe_consume_connection_slots()` wrapper**

#### Critical ESPHome Fix
The `ble_client/__init__.py` contains a compatibility wrapper:
```python
def safe_consume_connection_slots(slots, component_name):
    if hasattr(esp32_ble_tracker, 'consume_connection_slots'):
        return esp32_ble_tracker.consume_connection_slots(slots, component_name)
    else:
        return lambda config: config  # ESPHome 2025.10.0+
```
This resolves the `AttributeError` in ESPHome 2025.10.0+ where `consume_connection_slots` was removed/changed.

### Critical Bluetooth Architecture
**Device Connection Flow:**
1. Manual pairing required using `bluetoothctl` (documented in README.md)
2. `force_device_disconnect()` called before connection to ensure availability
3. Controllers instantiated per device with specific adapter (usually `hci0`)
4. Async connection with 10-second timeout in `async_setup_entry()`

**Connection State Management:**
- Controllers stored in `hass.data[DOMAIN][entry.entry_id][CONTROLLERS]`
- Connection status tracked via `ConnectionStatus.CONNECTED`
- All operations wrapped in `ConnectionAbortedError`/`ConnectionException` handling

### HVAC Mode Mapping Patterns
```python
# Critical bidirectional mappings between HA and Daikin enums
HA_MODE_TO_DAIKIN = {HVACMode.COOL: OperationModeEnum.COOL, ...}
DAIKIN_TO_HA_MODE = {OperationModeEnum.COOL: HVACMode.COOL, ...}
```

## Development Conventions

### Error Handling Pattern
**Always wrap Bluetooth operations:**
```python
try:
    await self.controller.operation_mode.update(...)
except ConnectionAbortedError:
    _LOGGER.warning("Connection not available, reload integration")
except ConnectionException:
    pass  # Silent fail for transient issues
```

### Configuration Validation
- MAC addresses validated with regex: `[0-9a-f]{2}([-:]?)[0-9a-f]{2}(\\1[0-9a-f]{2}){4}$`
- Bluetooth adapter validated by attempting device discovery
- Device discovery timeout configurable (default 5s)

### State Management
- Temperature setpoints differ by mode (heating vs cooling)
- AUTO mode determines current action based on `target_temperature >= current_temperature`
- Fan speeds maintained separately for heating/cooling modes

## Integration Points

### Home Assistant Integration

#### External Dependencies
- `pymadoka==0.2.12` - Core Bluetooth communication library
- Home Assistant's climate/sensor platforms
- System Bluetooth stack (requires DBUS access in containers)

#### Home Assistant Entities
- **Climate Entity**: Full HVAC control with temperature, mode, fan speed
- **Sensor Entity**: Indoor temperature readings
- Both entities share the same controller instance

#### Docker/Container Requirements
Critical for deployment:
```yaml
volumes:
  - /var/run/dbus/system_bus_socket:/var/run/dbus/system_bus_socket
privileged: true
```

### ESPHome Components

#### Usage in ESPHome YAML
```yaml
external_components:
  - source:
      type: local
      path: esphome_components
    components: [ madoka, ble_client ]

esp32_ble_tracker:
  max_connections: 2

ble_client:
  - mac_address: "F0:B3:1E:87:AF:FE"
    id: madoka_device

climate:
  - platform: madoka
    name: "Madoka Thermostat"
    ble_client_id: madoka_device
    update_interval: 15s
```

#### Key Configuration Notes
- `bluetooth_proxy: active: false` - Disable proxy when using ble_client directly
- `max_connections: 2` - Limit BLE connections (ESP32 constraint)
- Always include reconnection logic in `on_disconnect` triggers

## Testing & Debugging

### Bluetooth Connectivity Testing
```bash
# Test adapter availability
bleak-lescan -i hci0

# Validate device pairing
bluetoothctl paired-devices | grep BRC1H
```

### Common Issues
- **"device_not_found"**: Device connected to mobile app or not paired
- **"cannot_connect"**: DBUS not available or adapter missing
- **ConnectionAbortedError**: Bluetooth connection lost, requires integration reload

## File Editing Guidelines

### Adding New Features
- Extend controller capabilities in `climate.py` following existing async patterns
- Add new config options in `config_flow.py` with proper validation
- Update `strings.json` for new UI text

### Constants and Mappings
- Add new enums to mapping dictionaries in `climate.py`
- Temperature limits defined in `const.py` (MIN_TEMP=16, MAX_TEMP=32)
- Use existing timeout patterns (TIMEOUT=60, connection timeout=10s)

### Entity Properties
- Always check `controller.*.status is None` before accessing
- Return `None` for unavailable states rather than raising exceptions
- Use `async_schedule_update_ha_state()` after successful operations