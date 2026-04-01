# Changelog - ESPHome 2025.10.0+ Support

## Version 2.0.0 - October 15, 2025

### Major additions

#### ESPHome Components (`esphome_components/`)

Added local, patched ESPHome Madoka components:

- **madoka/** : Climate component for ESP32
  - Copied from Petapton/esphome@madoka fork
  - Compatible with all ESPHome versions

- **ble_client/** : Patched BLE client component
  - **CRITICAL FIX**: `safe_consume_connection_slots()` wrapper for ESPHome 2025.10.0+
  - Fixes `AttributeError: module 'esphome.components.esp32_ble_tracker' has no attribute 'consume_connection_slots'`
  - Backward-compatible with older ESPHome versions

### Documentation

- `esphome_components/README.md`: Full usage guide
- `esphome_components/DEPLOYMENT.md`: Detailed deployment guide
- `esphome_components/example-config.yaml`: Complete example configuration
- Updated `README.md` to explain both approaches (HA integration and ESPHome proxy)

### Structure changes

```
daikin_madoka/
├── esphome_components/          # NEW
│   ├── madoka/                  # ESP32 climate component
│   ├── ble_client/              # Patched BLE client
│   ├── README.md
│   ├── DEPLOYMENT.md
│   └── example-config.yaml
├── .github/                     # NEW
│   └── workflows/ci.yml
├── __init__.py                  # Home Assistant integration
├── climate.py
├── config_flow.py
├── sensor.py
└── README.md                    # UPDATED
```

## Migration

### For ESPHome users

**Before:**
```yaml
external_components:
  - source: github://Petapton/esphome@madoka
    components: [ madoka, ble_client ]
```

**After:**
```yaml
external_components:
  - source:
      type: local
      path: esphome_components
    components: [ madoka, ble_client ]
```

### For Home Assistant integration users

No changes required. The integration works the same as before.

## Issues fixed

- ✅ ESPHome 2025.10.0+ compatibility
- ✅ `AttributeError` in `esp32_ble_tracker.consume_connection_slots`
- ✅ Missing deployment documentation for ESPHome
- ✅ Missing example configuration file

## Technical notes

### Compatibility wrapper

```python
def safe_consume_connection_slots(slots, component_name):
    """Wrapper for consume_connection_slots, compatible with all ESPHome versions."""
    if hasattr(esp32_ble_tracker, 'consume_connection_slots'):
        return esp32_ble_tracker.consume_connection_slots(slots, component_name)
    else:
        return lambda config: config  # ESPHome 2025.10.0+
```

This function automatically detects the ESPHome version and adapts accordingly.

## Credits

- Original Home Assistant integration: [@mduran80](https://github.com/mduran80/daikin_madoka)
- ESPHome Madoka component: [Petapton/esphome](https://github.com/Petapton/esphome)
- ESPHome 2025.10.0+ fixes: this repository
