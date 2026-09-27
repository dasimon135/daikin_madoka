#include "madoka_vam.h"

#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#include <cinttypes>
#include <utility>

#ifdef USE_ESP32

namespace esphome {
namespace madoka_vam {

static const char *const TAG = "madoka_vam";

using namespace esphome::climate;
// The GATT plumbing, the chunking and every command a BRC1H answers whatever
// is behind it live in MadokaBase; only what is specific to a ventilation unit
// is below.
using namespace esphome::madoka_base;

// The VAM carries its airflow on the ventilation function, not on the regular
// fan speed function (0x0050): 0x0050 answers, but every argument comes back
// with length 0 and none of them ever change when the unit is driven from its
// own wall controller.
static const uint16_t CMD_GET_VENTILATION = 0x0031;
static const uint16_t CMD_SET_VENTILATION = 0x4031;

// Argument of CMD_GET_VENTILATION / CMD_SET_VENTILATION holding the airflow.
static const uint8_t ARG_VENTILATION_FAN_SPEED = 0x21;
// ...and the one holding how the unit routes that airflow.
static const uint8_t ARG_VENTILATION_MODE = 0x20;
// The VAM reuses the Madoka fan speed encoding. Values the unit does not
// support are ignored silently, so a two-speed VAM simply stays where it was
// when asked for 0x03.
static const uint8_t FAN_SPEED_LOW = 0x01;
static const uint8_t FAN_SPEED_HIGH = 0x05;

// Ventilation mode has no ClimateMode equivalent, so it rides on the custom
// preset. Values of ARG_VENTILATION_MODE, confirmed by writing 0x4031 and
// reading the result back.
static const uint8_t VENTILATION_MODE_AUTO = 0x00;
static const uint8_t VENTILATION_MODE_HEAT_EXCHANGE = 0x01;
static const uint8_t VENTILATION_MODE_BYPASS = 0x02;

static const char *const PRESET_VENTILATION_AUTO = "Auto";
static const char *const PRESET_HEAT_EXCHANGE = "Heat exchange";
static const char *const PRESET_BYPASS = "Bypass";

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

const char *MadokaVam::tag_() const { return TAG; }

void MadokaVam::dump_config() { LOG_CLIMATE(TAG, "Daikin Madoka VAM Climate Controller", this); }

void MadokaVam::on_setup_() {
  this->set_supported_custom_presets({PRESET_VENTILATION_AUTO, PRESET_HEAT_EXCHANGE, PRESET_BYPASS});
}

void MadokaVam::query_appliance_state_() {
  // A VAM has no setpoint and answers 0x0050 with empty arguments, so the
  // thermostat's setpoint and fan-speed reads are replaced by this one.
  this->query_(CMD_GET_VENTILATION, std::vector<uint8_t>{0x00, 0x00}, 50);
}

void MadokaVam::control(const ClimateCall &call) {
  if (this->node_state != espbt::ClientState::ESTABLISHED)
    return;
  if (call.get_mode().has_value()) {
    ClimateMode mode = *call.get_mode();
    uint8_t mode_out = 255, status_out = 0;
    switch (mode) {
      case climate::CLIMATE_MODE_OFF:
        status_out = 0;
        break;
      case climate::CLIMATE_MODE_FAN_ONLY:
        status_out = 1;
        mode_out = 5;
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
  if (call.get_fan_mode().has_value()) {
    uint8_t fan_mode = call.get_fan_mode().value();
    uint8_t fan_speed_out = 255;
    switch (fan_mode) {
      case climate::CLIMATE_FAN_LOW:
        fan_speed_out = FAN_SPEED_LOW;
        break;
      case climate::CLIMATE_FAN_HIGH:
        fan_speed_out = FAN_SPEED_HIGH;
        break;
      default:
        ESP_LOGW(TAG, "Unsupported fan mode: %d", fan_mode);
        break;
    }
    if (fan_speed_out != 255) {
      this->query_(CMD_SET_VENTILATION, std::vector<uint8_t>{ARG_VENTILATION_FAN_SPEED, 0x01, fan_speed_out}, 200);
    }
  }
  if (call.has_custom_preset()) {
    const StringRef preset = call.get_custom_preset();
    uint8_t vent_mode_out = 255;
    if (preset == PRESET_VENTILATION_AUTO) {
      vent_mode_out = VENTILATION_MODE_AUTO;
    } else if (preset == PRESET_HEAT_EXCHANGE) {
      vent_mode_out = VENTILATION_MODE_HEAT_EXCHANGE;
    } else if (preset == PRESET_BYPASS) {
      vent_mode_out = VENTILATION_MODE_BYPASS;
    } else {
      ESP_LOGW(TAG, "Unsupported ventilation mode: %s", preset.c_str());
    }
    // One argument per write on purpose: the unit applies whatever it is sent
    // and never reports a rejection, so sending a stale fan speed alongside
    // would quietly overwrite it.
    if (vent_mode_out != 255) {
      this->query_(CMD_SET_VENTILATION, std::vector<uint8_t>{ARG_VENTILATION_MODE, 0x01, vent_mode_out}, 200);
    }
  }
  this->should_update_ = true;
}


void MadokaVam::log_frame_(const char *what, const std::vector<uint8_t> &msg) {
  // A frame is at most 255 bytes: its first byte is its own size.
  char hex[format_hex_pretty_size(255)];
  ESP_LOGI(TAG, "%s 0x%04X: %s", what, frame_function_id(msg), format_hex_pretty_to(hex, msg.data(), msg.size()));
}

void MadokaVam::parse_cb_(std::vector<uint8_t> msg) {
  // Every argument goes through for_each_argument_, which never reads past
  // the frame; each case below still checks arg.len before reading, because
  // real units send zero-length values.
  const uint16_t function_id = frame_function_id(msg);
  if (this->dump_raw_) {
    this->log_frame_("Received function", msg);
  }

  switch (function_id) {
    case CMD_GET_SETTING_STATUS:
      this->for_each_argument_(msg, [this](const FrameArgument &arg) {
        if (arg.id == 0x20 && arg.len >= 1) {
          this->cur_status_.status = arg.value[0];
        }
      });
      break;
    case CMD_GET_OPERATION_MODE:
      this->for_each_argument_(msg, [this](const FrameArgument &arg) {
        if (arg.id == 0x20 && arg.len >= 1) {
          this->cur_status_.mode = arg.value[0];
        }
      });
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
          case 5:
            this->mode = climate::CLIMATE_MODE_FAN_ONLY;
            break;
        }
      } else {
        this->mode = climate::CLIMATE_MODE_OFF;
      }
      break;
    case CMD_GET_VENTILATION:
      this->for_each_argument_(msg, [this](const FrameArgument &arg) {
        if (arg.id == ARG_VENTILATION_FAN_SPEED && arg.len >= 1) {
          switch (arg.value[0]) {
            case FAN_SPEED_LOW:
              this->fan_mode = climate::CLIMATE_FAN_LOW;
              break;
            case FAN_SPEED_HIGH:
              this->fan_mode = climate::CLIMATE_FAN_HIGH;
              break;
            default:
              ESP_LOGW(TAG, "Unknown ventilation fan speed: 0x%02X", arg.value[0]);
              break;
          }
        } else if (arg.id == ARG_VENTILATION_MODE && arg.len >= 1) {
          switch (arg.value[0]) {
            case VENTILATION_MODE_AUTO:
              this->set_custom_preset_(PRESET_VENTILATION_AUTO);
              break;
            case VENTILATION_MODE_HEAT_EXCHANGE:
              this->set_custom_preset_(PRESET_HEAT_EXCHANGE);
              break;
            case VENTILATION_MODE_BYPASS:
              this->set_custom_preset_(PRESET_BYPASS);
              break;
            default:
              ESP_LOGW(TAG, "Unknown ventilation mode: 0x%02X", arg.value[0]);
              break;
          }
        }
      });
      break;
    case CMD_GET_SENSOR_INFORMATION:
      this->for_each_argument_(msg, [this](const FrameArgument &arg) {
        // Only argument 0x40 (indoor temperature) is read: a VAM is an
        // indoor-only unit, it has no outdoor probe behind argument 0x41.
        if (arg.id == 0x40 && arg.len >= 1) {
          this->current_temperature = arg.value[0];
        }
      });
      break;
    case CMD_GET_CLEAN_FILTER:
      this->for_each_argument_(msg, [this](const FrameArgument &arg) {
        if (arg.id == 0x62 && this->clean_filter_binary_sensor_ != nullptr && arg.len >= 1) {
          this->clean_filter_binary_sensor_->publish_state((arg.value[0] & 0x01) == 0x01);
        }
      });
      break;
    case CMD_GET_VERSION: {
      std::string rc_version;
      std::string ble_version;
      this->for_each_argument_(msg, [&rc_version, &ble_version](const FrameArgument &arg) {
        if (arg.id == 0x45 && arg.len >= 3) {
          rc_version = std::to_string(arg.value[0]) + "." + std::to_string(arg.value[1]) + "." +
                       std::to_string(arg.value[2]);
        } else if (arg.id == 0x46 && arg.len >= 2) {
          ble_version = std::to_string(arg.value[0]) + "." + std::to_string(arg.value[1]);
        }
      });
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
      this->for_each_argument_(msg, [this](const FrameArgument &arg) {
        if (arg.id == 0x33 && this->eye_brightness_number_ != nullptr && arg.len >= 1) {
          this->eye_brightness_number_->publish_state(arg.value[0]);
        }
      });
      break;
    // Acknowledgements of the writes this component sends itself: nothing to
    // decode, the next poll reads the result back.
    case CMD_SET_SETTING_STATUS:
    case CMD_SET_OPERATION_MODE:
    case CMD_SET_VENTILATION:
    case CMD_SET_EYE_BRIGHTNESS:
    case CMD_RESET_FILTER:
      break;
    default:
      // Nothing here decodes it: most likely the answer to a
      // send_raw_command() probe, so it is logged whole whatever dump_raw
      // says (and only once when dump_raw already printed it above).
      if (!this->dump_raw_) {
        this->log_frame_("Unhandled function", msg);
      }
      break;
  }

  this->publish_state();
}

}  // namespace madoka_vam
}  // namespace esphome

#endif
