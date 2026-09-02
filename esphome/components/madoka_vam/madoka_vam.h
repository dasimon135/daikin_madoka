#pragma once

#include <vector>
#include <string>

#include "esphome/components/madoka_base/madoka_base.h"

#ifdef USE_ESP32

namespace esphome {
namespace madoka_vam {

class MadokaVam;

class MadokaEyeBrightnessNumber : public number::Number, public Parented<MadokaVam> {
 public:
  void control(float value) override;
};

class MadokaResetFilterButton : public button::Button, public Parented<MadokaVam> {
 public:
  void press_action() override;
};

namespace espbt = esphome::esp32_ble_tracker;

/// A VAM (Ventilation Air Management / heat-recovery unit) behind a BRC1H.
/// It only ventilates: no setpoint, no outdoor probe, and the air routing is
/// exposed as a custom preset rather than an HVAC mode.
class MadokaVam : public madoka_base::MadokaBase {
 protected:
  bool dump_raw_ = false;

  const char *tag_() const override;
  const char *label_() const override { return "Daikin Madoka VAM Climate Controller"; }
  void parse_cb_(std::vector<uint8_t> msg) override;
  void query_appliance_state_() override;
  void on_setup_() override;

  void control(const climate::ClimateCall &call) override;

 public:
  void set_dump_raw(bool dump_raw) { this->dump_raw_ = dump_raw; }
  // Send a raw command (function id plus arguments): useful for probing the
  // VAM's undocumented functions from an ESPHome lambda. Responses land in the
  // logs; enable dump_raw for the hex dump.
  void send_raw_command(uint16_t cmd, std::vector<uint8_t> args) { this->query_(cmd, std::move(args), 200); }
  climate::ClimateTraits traits() override {
    auto traits = climate::ClimateTraits();
    traits.set_supported_modes({
        climate::CLIMATE_MODE_OFF,
        climate::CLIMATE_MODE_FAN_ONLY,
    });
    traits.set_supported_fan_modes({
        climate::CLIMATE_FAN_LOW,
        climate::CLIMATE_FAN_HIGH,
    });
    traits.add_feature_flags(climate::CLIMATE_SUPPORTS_CURRENT_TEMPERATURE);
    return traits;
  }
};

}  // namespace madoka_vam
}  // namespace esphome

#endif
