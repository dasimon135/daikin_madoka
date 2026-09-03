#include "madoka_vam.h"

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


void MadokaVam::parse_cb_(std::vector<uint8_t> msg) {
  if (msg.size() < 4) {
    ESP_LOGW(TAG, "Discarding a frame that is too short to carry a function id");
    return;
  }
  uint16_t function_id = msg[2] << 8 | msg[3];
  uint8_t i = 4;
  uint8_t message_size = msg.size();

  switch (function_id) {
    case CMD_GET_SETTING_STATUS:
      while (i < message_size) {
        uint8_t argument_id = msg[i++];
        uint8_t len = msg[i++];
        if (argument_id == 0x20 && len >= 1) {
          this->cur_status_.status = msg[i];
        }
        i += len;
      }
      break;
    case CMD_GET_OPERATION_MODE:
      while (i < message_size) {
        uint8_t argument_id = msg[i++];
        uint8_t len = msg[i++];
        if (argument_id == 0x20 && len >= 1) {
          this->cur_status_.mode = msg[i];
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
          case 5:
            this->mode = climate::CLIMATE_MODE_FAN_ONLY;
            break;
        }
      } else {
        this->mode = climate::CLIMATE_MODE_OFF;
      }
      break;
    case CMD_GET_VENTILATION: {
      while (i < message_size) {
        uint8_t argument_id = msg[i++];
        uint8_t len = msg[i++];
        if (argument_id == ARG_VENTILATION_FAN_SPEED && len >= 1) {
          switch (msg[i]) {
            case FAN_SPEED_LOW:
              this->fan_mode = climate::CLIMATE_FAN_LOW;
              break;
            case FAN_SPEED_HIGH:
              this->fan_mode = climate::CLIMATE_FAN_HIGH;
              break;
            default:
              ESP_LOGW(TAG, "Unknown ventilation fan speed: 0x%02X", msg[i]);
              break;
          }
        } else if (argument_id == ARG_VENTILATION_MODE && len >= 1) {
          switch (msg[i]) {
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
              ESP_LOGW(TAG, "Unknown ventilation mode: 0x%02X", msg[i]);
              break;
          }
        }
        i += len;
      }
      break;
    }
    case CMD_GET_SENSOR_INFORMATION:
      while (i < message_size) {
        uint8_t argument_id = msg[i++];
        uint8_t len = msg[i++];
        // Only argument 0x40 (indoor temperature) is read: a VAM is an
        // indoor-only unit, it has no outdoor probe behind argument 0x41.
        if (argument_id == 0x40 && len >= 1) {
          this->current_temperature = msg[i];
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

  this->publish_state();
}

}  // namespace madoka_vam
}  // namespace esphome

#endif
