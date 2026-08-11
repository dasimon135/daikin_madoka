#include "madoka_vam.h"

#include "esphome/core/log.h"
#include <cinttypes>
#include <utility>

#ifdef USE_ESP32

namespace esphome {
namespace madoka_vam {

static const char *const TAG = "madoka_vam";

using namespace esphome::climate;

static const uint16_t CMD_GET_SETTING_STATUS = 0x0020;
static const uint16_t CMD_SET_SETTING_STATUS = 0x4020;
static const uint16_t CMD_GET_OPERATION_MODE = 0x0030;
static const uint16_t CMD_SET_OPERATION_MODE = 0x4030;
// The VAM carries its airflow on the ventilation function, not on the regular
// fan speed function (0x0050): 0x0050 answers, but every argument comes back
// with length 0 and none of them ever change when the unit is driven from its
// own wall controller.
static const uint16_t CMD_GET_VENTILATION = 0x0031;
static const uint16_t CMD_SET_VENTILATION = 0x4031;
static const uint16_t CMD_GET_SENSOR_INFORMATION = 0x0110;
static const uint16_t CMD_GET_CLEAN_FILTER = 0x0100;
static const uint16_t CMD_GET_VERSION = 0x0130;
static const uint16_t CMD_GET_EYE_BRIGHTNESS = 0x0302;
static const uint16_t CMD_RESET_FILTER = 0x4220;
static const uint16_t CMD_SET_EYE_BRIGHTNESS = 0x4302;

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

void MadokaVam::dump_config() { LOG_CLIMATE(TAG, "Daikin Madoka VAM Climate Controller", this); }

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

void MadokaVam::setup() {
  this->receive_semaphore_ = xSemaphoreCreateMutex();
  this->set_supported_custom_presets({PRESET_VENTILATION_AUTO, PRESET_HEAT_EXCHANGE, PRESET_BYPASS});
}

void MadokaVam::loop() {
  std::vector<uint8_t> chk = {};
  if (xSemaphoreTake(this->receive_semaphore_, 0L)) {
    if (!this->received_chunks_.empty()) {
      chk = this->received_chunks_.front();
      this->received_chunks_.pop();
    }
    xSemaphoreGive(this->receive_semaphore_);
    if (!chk.empty()) {
      this->process_incoming_chunk_(chk);
    }
  }
  if (this->should_update_) {
    this->should_update_ = false;
    this->update();
  }
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

void MadokaVam::gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) {
  switch (event) {
    case ESP_GAP_BLE_SEC_REQ_EVT:
      esp_ble_gap_security_rsp(param->ble_security.ble_req.bd_addr, true);
      break;
    case ESP_GAP_BLE_NC_REQ_EVT:
      esp_ble_confirm_reply(param->ble_security.ble_req.bd_addr, true);
      ESP_LOGI(TAG, "ESP_GAP_BLE_NC_REQ_EVT, the passkey Notify number:%" PRIu32,
               param->ble_security.key_notif.passkey);
      break;
    case ESP_GAP_BLE_AUTH_CMPL_EVT: {
      if (!param->ble_security.auth_cmpl.success) {
        ESP_LOGE(TAG, "Authentication failed, status: 0x%x", param->ble_security.auth_cmpl.fail_reason);
        break;
      }
      auto *nfy = this->parent_->get_characteristic(MADOKA_SERVICE_UUID, NOTIFY_CHARACTERISTIC_UUID);
      auto *wwr = this->parent_->get_characteristic(MADOKA_SERVICE_UUID, WWR_CHARACTERISTIC_UUID);
      if (nfy == nullptr || wwr == nullptr) {
        ESP_LOGW(TAG, "[%s] No control service found at device, not a Daikin Madoka VAM..?", this->get_name().c_str());
        break;
      }
      this->notify_handle_ = nfy->handle;
      this->wwr_handle_ = wwr->handle;

      auto status = esp_ble_gattc_register_for_notify(this->parent_->get_gattc_if(), this->parent_->get_remote_bda(),
                                                      nfy->handle);
      if (status) {
        ESP_LOGW(TAG, "[%s] esp_ble_gattc_register_for_notify failed, status=%d", this->get_name().c_str(), status);
      }
      break;
    }
    default:
      break;
  }
}

void MadokaVam::gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if, esp_ble_gattc_cb_param_t *param) {
  switch (event) {
    case ESP_GATTC_DISCONNECT_EVT: {
      this->node_state = espbt::ClientState::IDLE;  // ??
      this->current_temperature = NAN;
      this->publish_state();
      break;
    }
    case ESP_GATTC_WRITE_DESCR_EVT:
      if (param->write.status != ESP_GATT_OK) {
        if (param->write.status == ESP_GATT_INSUF_AUTHENTICATION) {
          ESP_LOGE(TAG, "Insufficient authentication");
        } else {
          ESP_LOGE(TAG, "Failed writing characteristic descriptor, status = 0x%x", param->write.status);
        }
      }
      break;
    case ESP_GATTC_SEARCH_CMPL_EVT: {
      esp_ble_set_encryption(this->parent_->get_remote_bda(), ESP_BLE_SEC_ENCRYPT_MITM);
      break;
    }
    case ESP_GATTC_REG_FOR_NOTIFY_EVT: {
      this->node_state = espbt::ClientState::ESTABLISHED;  // ??
      break;
    }
    case ESP_GATTC_NOTIFY_EVT: {
      if (param->notify.handle != this->notify_handle_) {
        ESP_LOGW(TAG, "Different notify handle");
        break;
      }
      std::vector<uint8_t> chk =
          std::vector<uint8_t>{param->notify.value, param->notify.value + param->notify.value_len};
      xSemaphoreTake(this->receive_semaphore_, portMAX_DELAY);
      this->received_chunks_.push(chk);
      xSemaphoreGive(this->receive_semaphore_);
      break;
    }
    default:
      break;
  }
}

void MadokaVam::update() {
  ESP_LOGD(TAG, "Got update request...");
  if (this->node_state != espbt::ClientState::ESTABLISHED) {
    ESP_LOGD(TAG, "...but device is disconnected");
    return;
  }

  this->query_(CMD_GET_SETTING_STATUS, std::vector<uint8_t>{0x00, 0x00}, 50);
  this->query_(CMD_GET_OPERATION_MODE, std::vector<uint8_t>{0x00, 0x00}, 50);
  this->query_(CMD_GET_VENTILATION, std::vector<uint8_t>{0x00, 0x00}, 50);
  this->query_(CMD_GET_SENSOR_INFORMATION, std::vector<uint8_t>{0x00, 0x00}, 50);
  this->query_(CMD_GET_CLEAN_FILTER, std::vector<uint8_t>{0x00, 0x00}, 50);
  this->query_(CMD_GET_VERSION, std::vector<uint8_t>{0x00, 0x00}, 50);
  this->query_(CMD_GET_EYE_BRIGHTNESS, std::vector<uint8_t>{0x33, 0x01, 0x00}, 50);
}

void MadokaVam::set_eye_brightness(uint8_t level) {
  if (this->node_state != espbt::ClientState::ESTABLISHED) {
    return;
  }
  this->query_(CMD_SET_EYE_BRIGHTNESS, std::vector<uint8_t>{0x33, 0x01, level}, 200);
  if (this->eye_brightness_number_ != nullptr) {
    this->eye_brightness_number_->publish_state(level);
  }
  this->should_update_ = true;
}

void MadokaVam::reset_filter() {
  if (this->node_state != espbt::ClientState::ESTABLISHED) {
    return;
  }
  this->query_(CMD_RESET_FILTER, std::vector<uint8_t>{0x51, 0x01, 0x01, 0xFE, 0x01, 0x01}, 200);
  if (this->clean_filter_binary_sensor_ != nullptr) {
    this->clean_filter_binary_sensor_->publish_state(false);
  }
  this->should_update_ = true;
}

bool validate_buffer(std::vector<uint8_t> buffer) { return buffer[0] == buffer.size(); }

void MadokaVam::process_incoming_chunk_(std::vector<uint8_t> chk) {
  if (chk.size() < 2) {
    ESP_LOGI(TAG, "Chunk discarded: invalid length.");
    return;
  }
  uint8_t chunk_id = chk[0];
  std::vector<uint8_t> stripped{chk.begin() + 1, chk.end()};
  if (chunk_id == 0 && validate_buffer(stripped)) {
    this->parse_cb_(stripped);
    return;
  }
  if (this->pending_chunks_.count(chunk_id)) {
    if (chunk_id == 0) {
      ESP_LOGW(TAG, "New message detected, clearing incomplete buffer (chunk_id=0).");
      this->pending_chunks_.clear();
    } else {
      ESP_LOGE(TAG, "Another packet with the same chunk ID is already in the buffer.");
      ESP_LOGD(TAG, "Chunk ID: %d.", chunk_id);
      return;
    }
  }
  this->pending_chunks_[chunk_id] = chk;

  if (this->pending_chunks_.size() != this->pending_chunks_.rbegin()->first + 1) {
    ESP_LOGW(TAG, "Buffer is missing packets");
    return;
  }

  std::vector<uint8_t> msg;
  int lim = this->pending_chunks_.size();
  for (int i = 0; i < lim; i++) {
    msg.insert(msg.end(), this->pending_chunks_[i].begin() + 1, this->pending_chunks_[i].end());
  }
  if (validate_buffer(msg)) {
    this->pending_chunks_.clear();
    this->parse_cb_(msg);
  }
}

std::vector<std::vector<uint8_t>> MadokaVam::split_payload_(std::vector<uint8_t> msg) {
  std::vector<std::vector<uint8_t>> result;
  size_t len = msg.size();

  // Add leading length byte
  std::vector<uint8_t> buf{(uint8_t) (len + 1)};
  buf.insert(buf.end(), msg.begin(), msg.end());

  for (size_t i = 0; i <= len / (MAX_CHUNK_SIZE - 1); i++) {
    std::vector<uint8_t> chunk{(uint8_t) i};
    chunk.insert(chunk.end(), buf.begin() + (i * (MAX_CHUNK_SIZE - 1)),
                 std::min(buf.end(), buf.begin() + ((i + 1) * (MAX_CHUNK_SIZE - 1))));

    result.push_back(chunk);
  }

  return result;
}

std::vector<uint8_t> MadokaVam::prepare_message_(uint16_t cmd, std::vector<uint8_t> args) {
  std::vector<uint8_t> result({0x00, (uint8_t) ((cmd >> 8) & 0xFF), (uint8_t) (cmd & 0xFF)});
  result.insert(result.end(), args.begin(), args.end());
  return result;
}

void MadokaVam::query_(uint16_t cmd, std::vector<uint8_t> args, int t_d) {
  std::vector<uint8_t> payload = this->prepare_message_(cmd, std::move(args));

  if (this->node_state != espbt::ClientState::ESTABLISHED) {
    return;
  }
  std::vector<std::vector<uint8_t>> chunks = this->split_payload_(payload);

  for (auto chk : chunks) {
    esp_err_t status;
    for (int j = 0; j < BLE_SEND_MAX_RETRIES; j++) {
      status = esp_ble_gattc_write_char(this->parent_->get_gattc_if(), this->parent_->get_conn_id(), this->wwr_handle_,
                                        chk.size(), chk.data(), ESP_GATT_WRITE_TYPE_NO_RSP, ESP_GATT_AUTH_REQ_NONE);
      if (!status) {
        break;
      }
      ESP_LOGD(TAG, "[%s] esp_ble_gattc_write_char failed (%d of %d), status=%d", this->parent_->address_str(),
               j + 1, BLE_SEND_MAX_RETRIES, status);
    }
    if (status) {
      ESP_LOGE(TAG, "[%s] Command could not be sent, last status=%d", this->parent_->address_str(), status);
      return;
    }
  }
  esphome::delay(t_d);
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
