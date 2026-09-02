#include "madoka.h"

#include "esphome/core/log.h"
#include <cinttypes>
#include <utility>

#ifdef USE_ESP32

namespace esphome {
namespace madoka {

static const char *const TAG = "madoka";

using namespace esphome::climate;
// The GATT plumbing, the chunking and every command a BRC1H answers whatever
// is behind it live in MadokaBase; only what is specific to a thermostat is
// below.
using namespace esphome::madoka_base;

static const uint16_t CMD_GET_SETPOINT = 0x0040;
static const uint16_t CMD_SET_SETPOINT = 0x4040;
static const uint16_t CMD_GET_FAN_SPEED = 0x0050;
static const uint16_t CMD_SET_FAN_SPEED = 0x4050;

void MadokaEyeBrightnessNumber::control(float value) {
  int level = static_cast<int>(value + 0.5f);
  if (level < 0) {
    level = 0;
  }
  if (level > 19) {
    level = 19;
  }
  this->parent_->set_eye_brightness(level);
}

void MadokaResetFilterButton::press_action() { this->parent_->reset_filter(); }

const char *Madoka::tag_() const { return TAG; }

void Madoka::query_appliance_state_() {
  this->query_(CMD_GET_SETPOINT, std::vector<uint8_t>{0x00, 0x00}, 50);
  this->query_(CMD_GET_FAN_SPEED, std::vector<uint8_t>{0x00, 0x00}, 50);
}

void Madoka::on_disconnect_() {
  // A VAM has no setpoint, so clearing it is the thermostat's business rather
  // than the base's.
  this->target_temperature = NAN;
}

void Madoka::control(const ClimateCall &call) {
  if (this->node_state != espbt::ClientState::ESTABLISHED)
    return;
  if (call.get_mode().has_value()) {
    ClimateMode mode = *call.get_mode();
    uint8_t mode_out = 255, status_out = 0;
    switch (mode) {
      case climate::CLIMATE_MODE_OFF:
        status_out = 0;
        break;
      case climate::CLIMATE_MODE_HEAT_COOL:
        status_out = 1;
        mode_out = 2;
        break;
      case climate::CLIMATE_MODE_COOL:
        status_out = 1;
        mode_out = 3;
        break;
      case climate::CLIMATE_MODE_HEAT:
        status_out = 1;
        mode_out = 4;
        break;
      case climate::CLIMATE_MODE_FAN_ONLY:
        status_out = 1;
        mode_out = 0;
        break;
      case climate::CLIMATE_MODE_DRY:
        status_out = 1;
        mode_out = 1;
        break;
      default:
        ESP_LOGW(TAG, "Unsupported mode: %d", mode);
        break;
    }
    ESP_LOGD(TAG, "status: %d, mode: %d", status_out, mode_out);
    if (mode_out != 255) {
      this->query_(CMD_SET_OPERATION_MODE, std::vector<uint8_t>{0x20, 0x01, (uint8_t) mode_out}, 600);
    }
    this->query_(CMD_SET_SETTING_STATUS, std::vector<uint8_t>{0x20, 0x01, (uint8_t) status_out}, 200);
  }
  if (this->dual_setpoint_ && call.get_target_temperature_low().has_value() &&
      call.get_target_temperature_high().has_value()) {
    uint16_t target_low = *call.get_target_temperature_low() * 128;
    uint16_t target_high = *call.get_target_temperature_high() * 128;
    this->query_(CMD_SET_SETPOINT,
                 std::vector<uint8_t>{0x20, 0x02, (uint8_t) ((target_high >> 8) & 0xFF), (uint8_t) (target_high & 0xFF),
                                      0x21, 0x02, (uint8_t) ((target_low >> 8) & 0xFF), (uint8_t) (target_low & 0xFF)},
                 400);
  }
  if (!this->dual_setpoint_ && call.get_target_temperature().has_value()) {
    float target = *call.get_target_temperature();
    // The setpoint frame always carries both registers (0x20 cooling,
    // 0x21 heating): see the dual branch above and CMD_GET_SETPOINT in
    // parse_cb_. Both slots have to carry a value the unit can accept.
    //
    // Carrying the other register over from the last poll -- which this used
    // to do, keyed on the active mode -- produces a mismatched pair, and a
    // BRC1H that is not in range mode rejects that pair silently: the frame is
    // acknowledged and the setpoint never moves. dual_setpoint is documented
    // as "only if range mode is enabled on the BRC1H", so reaching this branch
    // means the unit holds a single setpoint.
    //
    // The pair is not always "both equal". min_differential_ (argument 0x32)
    // is the minimum gap the unit keeps between the two registers. Units
    // reporting 0 store an equal pair as sent. A unit reporting 1 cannot hold
    // one: the frame applies cooling first, so heating then breaks the gap and
    // the firmware restores it by pushing cooling up -- a degree high on every
    // change, and a one-degree decrease that does nothing. Writing the gap
    // leaves it nothing to correct.
    // Same rule as the native integration's async_set_temperature().
    float differential = (float) this->min_differential_;
    float cooling = target;
    float heating = target;
    // A single call can carry both a mode and a setpoint; the mode being asked
    // for is the one the pair has to suit.
    ClimateMode effective_mode = call.get_mode().value_or(this->mode);
    if (effective_mode == climate::CLIMATE_MODE_HEAT) {
      cooling = target + differential;
    } else {
      heating = target - differential;
    }
    uint16_t target_cooling = cooling * 128;
    uint16_t target_heating = heating * 128;
    this->query_(
        CMD_SET_SETPOINT,
        std::vector<uint8_t>{0x20, 0x02, (uint8_t) ((target_cooling >> 8) & 0xFF), (uint8_t) (target_cooling & 0xFF),
                             0x21, 0x02, (uint8_t) ((target_heating >> 8) & 0xFF), (uint8_t) (target_heating & 0xFF)},
        400);
  }
  if (call.get_fan_mode().has_value()) {
    uint8_t fan_mode = call.get_fan_mode().value();
    uint8_t fan_mode_out = 255;
    switch (fan_mode) {
      case climate::CLIMATE_FAN_AUTO:
        fan_mode_out = 0;
        break;
      case climate::CLIMATE_FAN_LOW:
        fan_mode_out = 1;
        break;
      case climate::CLIMATE_FAN_MEDIUM:
        fan_mode_out = 3;
        break;
      case climate::CLIMATE_FAN_HIGH:
        fan_mode_out = 5;
        break;
      default:
        ESP_LOGW(TAG, "Unsupported fan mode: %d", fan_mode);
        break;
    }
    if (fan_mode_out != 255) {
      this->query_(CMD_SET_FAN_SPEED,
                   std::vector<uint8_t>{0x20, 0x01, (uint8_t) fan_mode_out, 0x21, 0x01, (uint8_t) fan_mode_out}, 200);
    }
  }
  this->should_update_ = true;
}


void Madoka::parse_cb_(std::vector<uint8_t> msg) {
  uint16_t function_id = msg[2] << 8 | msg[3];
  uint8_t i = 4;
  uint8_t message_size = msg.size();

  switch (function_id) {
    case CMD_GET_SETTING_STATUS:
      while (i < message_size) {
        uint8_t argument_id = msg[i++];
        uint8_t len = msg[i++];
        if (argument_id == 0x20) {
          std::vector<uint8_t> val(msg.begin() + i, msg.begin() + i + len);
          this->cur_status_.status = val[0];
        }
        i += len;
      }
      break;
    case CMD_GET_OPERATION_MODE:
      while (i < message_size) {
        uint8_t argument_id = msg[i++];
        uint8_t len = msg[i++];
        if (argument_id == 0x20) {
          std::vector<uint8_t> val(msg.begin() + i, msg.begin() + i + len);
          this->cur_status_.mode = val[0];
        }
        i += len;
      }
      break;
    default:
      break;
  }
  switch (function_id) {
    case CMD_GET_SETTING_STATUS:
    case CMD_GET_OPERATION_MODE:
      // ESP_LOGI(TAG, "status: %d, mode: %d", this->cur_status_.status, this->cur_status_.mode);
      if (this->cur_status_.status) {
        switch (this->cur_status_.mode) {
          case 0:
            this->mode = climate::CLIMATE_MODE_FAN_ONLY;
            break;
          case 1:
            this->mode = climate::CLIMATE_MODE_DRY;
            break;
          case 2:
            this->mode = climate::CLIMATE_MODE_HEAT_COOL;
            break;
          case 3:
            this->mode = climate::CLIMATE_MODE_COOL;
            break;
          case 4:
            this->mode = climate::CLIMATE_MODE_HEAT;
            break;
        }
      } else {
        this->mode = climate::CLIMATE_MODE_OFF;
      }
      break;
    case CMD_GET_SETPOINT:
      while (i < message_size) {
        uint8_t argument_id = msg[i++];
        uint8_t len = msg[i++];
        switch (argument_id) {
          case 0x20: {
            std::vector<uint8_t> val(msg.begin() + i, msg.begin() + i + len);
            this->cooling_setpoint_ = (float) (val[0] << 8 | val[1]) / 128;
            break;
          }
          case 0x21: {
            std::vector<uint8_t> val(msg.begin() + i, msg.begin() + i + len);
            this->heating_setpoint_ = (float) (val[0] << 8 | val[1]) / 128;
            break;
          }
          case 0x32: {
            // Minimum differential between the two setpoints. Needed when
            // writing a single setpoint: see control(). Not every controller
            // reports it; the 0 default is the historical behaviour.
            if (len >= 1 && msg[i] != this->min_differential_) {
              this->min_differential_ = msg[i];
              ESP_LOGD(TAG, "Minimum setpoint differential: %u", this->min_differential_);
            }
            break;
          }
        }
        i += len;
      }
      break;
    case CMD_GET_FAN_SPEED: {
      uint8_t fan_mode = 255;
      while (i < message_size) {
        uint8_t argument_id = msg[i++];
        uint8_t len = msg[i++];
        if (this->cur_status_.mode == 1) {
        } else if ((argument_id == 0x21 && len == 1 && this->cur_status_.mode == 4) ||
                   (argument_id == 0x20 && len == 1 && this->cur_status_.mode != 4)) {
          fan_mode = msg[i];
        }
        i += len;
      }
      switch (fan_mode) {
        case 0:
          this->fan_mode = climate::CLIMATE_FAN_AUTO;
          break;
        case 1:
          this->fan_mode = climate::CLIMATE_FAN_LOW;
          break;
        case 2:
        case 3:
        case 4:
          this->fan_mode = climate::CLIMATE_FAN_MEDIUM;
          break;
        case 5:
          this->fan_mode = climate::CLIMATE_FAN_HIGH;
          break;
        default:
          break;
      }
      break;
    }
    case CMD_GET_SENSOR_INFORMATION:
      while (i < message_size) {
        uint8_t argument_id = msg[i++];
        uint8_t len = msg[i++];
        if (argument_id == 0x40) {
          std::vector<uint8_t> val(msg.begin() + i, msg.begin() + i + len);
          this->current_temperature = val[0];
        } else if (argument_id == 0x41 && this->outdoor_temperature_sensor_ != nullptr && len >= 1) {
          uint8_t value = msg[i];
          if (value != 0xFF) {
            this->outdoor_temperature_sensor_->publish_state(value);
          }
        }
        i += len;
      }
      break;
    case CMD_GET_CLEAN_FILTER:
      while (i < message_size) {
        uint8_t argument_id = msg[i++];
        uint8_t len = msg[i++];
        if (argument_id == 0x62 && this->clean_filter_binary_sensor_ != nullptr && len >= 1) {
          this->clean_filter_binary_sensor_->publish_state((msg[i] & 0x01) == 0x01);
        }
        i += len;
      }
      break;
    case CMD_GET_VERSION: {
      std::string rc_version;
      std::string ble_version;
      while (i < message_size) {
        uint8_t argument_id = msg[i++];
        uint8_t len = msg[i++];
        if (argument_id == 0x45 && len >= 3) {
          rc_version = std::to_string(msg[i]) + "." + std::to_string(msg[i + 1]) + "." + std::to_string(msg[i + 2]);
        } else if (argument_id == 0x46 && len >= 2) {
          ble_version = std::to_string(msg[i]) + "." + std::to_string(msg[i + 1]);
        }
        i += len;
      }
      if (this->firmware_version_text_sensor_ != nullptr) {
        if (!rc_version.empty() && !ble_version.empty()) {
          this->firmware_version_text_sensor_->publish_state("RC " + rc_version + " / BLE " + ble_version);
        } else if (!rc_version.empty()) {
          this->firmware_version_text_sensor_->publish_state(rc_version);
        } else if (!ble_version.empty()) {
          this->firmware_version_text_sensor_->publish_state("BLE " + ble_version);
        }
      }
      break;
    }
    case CMD_GET_EYE_BRIGHTNESS:
      while (i < message_size) {
        uint8_t argument_id = msg[i++];
        uint8_t len = msg[i++];
        if (argument_id == 0x33 && this->eye_brightness_number_ != nullptr && len >= 1) {
          this->eye_brightness_number_->publish_state(msg[i]);
        }
        i += len;
      }
      break;
    default:
      break;
  }

  this->apply_setpoints_();
  this->publish_state();
}

void Madoka::apply_setpoints_() {
  // Keep the published state aligned with the advertised traits: ESPHome only
  // sends target_temperature_low/high for a two-point entity and only
  // target_temperature for a single-point one, so filling the wrong pair
  // leaves the frontend without a setpoint at all.
  // Beware: in climate.h target_temperature is a *union* with the
  // low/high pair (target_temperature aliases target_temperature_low), which
  // is why the values read from the device are cached in our own members and
  // only one of the two shapes is written here.
  if (this->dual_setpoint_) {
    this->target_temperature_high = this->cooling_setpoint_;
    this->target_temperature_low = this->heating_setpoint_;
    return;
  }
  // Single setpoint: report the register the active mode drives. Heating uses
  // 0x21, everything else (cool, dry, fan, auto and off) uses 0x20, the same
  // rule the native integration applies in its target_temperature property.
  this->target_temperature =
      this->mode == climate::CLIMATE_MODE_HEAT ? this->heating_setpoint_ : this->cooling_setpoint_;
}

}  // namespace madoka
}  // namespace esphome

#endif
