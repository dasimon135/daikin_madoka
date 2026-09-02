#pragma once

#include <cmath>
#include <vector>
#include <string>

#include "esphome/components/madoka_base/madoka_base.h"
#include "esphome/components/sensor/sensor.h"

#ifdef USE_ESP32

namespace esphome {
namespace madoka {

class Madoka;

class MadokaEyeBrightnessNumber : public number::Number, public Parented<Madoka> {
 public:
  void control(float value) override;
};

class MadokaResetFilterButton : public button::Button, public Parented<Madoka> {
 public:
  void press_action() override;
};

struct Setpoint {
  uint16_t cooling;
  uint16_t heating;
};

struct FanSpeed {
  uint8_t cooling;
  uint8_t heating;
};

struct SensorReading {
  uint8_t indoor;
  uint8_t outdoor;
};

namespace espbt = esphome::esp32_ble_tracker;

class Madoka : public madoka_base::MadokaBase {
 protected:
  // Whether the entity advertises two setpoints (BRC1H "range" mode) or a
  // single one. ESPHome traits are static per API connection, so this is a
  // YAML option rather than something derived from the device at runtime.
  bool dual_setpoint_ = false;
  // Last setpoints read from the device (argument 0x20 = cooling,
  // 0x21 = heating), kept apart from the climate state fields so the
  // published state can be built to match the advertised traits.
  float cooling_setpoint_ = NAN;
  float heating_setpoint_ = NAN;
  // Minimum gap the controller keeps between the two setpoints (argument
  // 0x32). Zero on most units, which then store an equal pair as sent; a unit
  // reporting a non-zero gap corrects any pair that violates it, so the write
  // has to carry the gap. Defaults to 0 so a controller that does not report
  // the argument keeps the equal-pair behaviour.
  uint8_t min_differential_ = 0;
  sensor::Sensor *outdoor_temperature_sensor_{nullptr};

  const char *tag_() const override;
  const char *label_() const override { return "Daikin Madoka Climate Controller"; }
  void parse_cb_(std::vector<uint8_t> msg) override;
  void query_appliance_state_() override;
  void on_disconnect_() override;

  void apply_setpoints_();

  void control(const climate::ClimateCall &call) override;

 public:
  void set_outdoor_temperature_sensor(sensor::Sensor *sensor) { this->outdoor_temperature_sensor_ = sensor; }
  void set_dual_setpoint(bool dual_setpoint) { this->dual_setpoint_ = dual_setpoint; }
  climate::ClimateTraits traits() override {
    auto traits = climate::ClimateTraits();
    traits.set_supported_modes({
        climate::CLIMATE_MODE_OFF,
        climate::CLIMATE_MODE_HEAT_COOL,
        climate::CLIMATE_MODE_COOL,
        climate::CLIMATE_MODE_HEAT,
        climate::CLIMATE_MODE_FAN_ONLY,
        climate::CLIMATE_MODE_DRY,
    });
    traits.set_supported_fan_modes({
        climate::CLIMATE_FAN_LOW,
        climate::CLIMATE_FAN_MEDIUM,
        climate::CLIMATE_FAN_HIGH,
        climate::CLIMATE_FAN_AUTO,
    });
    traits.set_visual_min_temperature(16);
    traits.set_visual_max_temperature(32);
    traits.set_visual_temperature_step(1);
    uint32_t flags = climate::CLIMATE_SUPPORTS_CURRENT_TEMPERATURE;
    // Only advertise the dual setpoint when the YAML asked for it: the BRC1H
    // ships with range mode off, and a hardcoded two-point trait made the
    // entity contradict the thermostat (see the "dual_setpoint" option).
    if (this->dual_setpoint_) {
      flags |= climate::CLIMATE_REQUIRES_TWO_POINT_TARGET_TEMPERATURE;
    }
    traits.add_feature_flags(flags);
    return traits;
  }
};

}  // namespace madoka
}  // namespace esphome

#endif
