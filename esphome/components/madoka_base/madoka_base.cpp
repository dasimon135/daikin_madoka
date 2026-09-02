#include "madoka_base.h"

#include "esphome/core/log.h"
#include <cinttypes>
#include <utility>

#ifdef USE_ESP32

namespace esphome {
namespace madoka_base {

void MadokaBase::setup() {
  this->receive_semaphore_ = xSemaphoreCreateMutex();
  this->on_setup_();
}

void MadokaBase::loop() {
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

void MadokaBase::dump_config() { LOG_CLIMATE(this->tag_(), this->label_(), this); }

void MadokaBase::gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) {
  switch (event) {
    case ESP_GAP_BLE_SEC_REQ_EVT:
      esp_ble_gap_security_rsp(param->ble_security.ble_req.bd_addr, true);
      break;
    case ESP_GAP_BLE_NC_REQ_EVT:
      esp_ble_confirm_reply(param->ble_security.ble_req.bd_addr, true);
      // passkey is uint32_t; %d is a -Wformat error waiting to happen on a
      // 32-bit target where uint32_t is `long unsigned int`.
      ESP_LOGI(this->tag_(), "ESP_GAP_BLE_NC_REQ_EVT, the passkey Notify number:%" PRIu32,
               param->ble_security.key_notif.passkey);
      break;
    case ESP_GAP_BLE_AUTH_CMPL_EVT: {
      if (!param->ble_security.auth_cmpl.success) {
        ESP_LOGE(this->tag_(), "Authentication failed, status: 0x%x", param->ble_security.auth_cmpl.fail_reason);
        break;
      }
      auto *nfy = this->parent_->get_characteristic(MADOKA_SERVICE_UUID, NOTIFY_CHARACTERISTIC_UUID);
      auto *wwr = this->parent_->get_characteristic(MADOKA_SERVICE_UUID, WWR_CHARACTERISTIC_UUID);
      if (nfy == nullptr || wwr == nullptr) {
        ESP_LOGW(this->tag_(), "[%s] No control service found at device, not a %s..?", this->get_name().c_str(),
                 this->label_());
        break;
      }
      this->notify_handle_ = nfy->handle;
      this->wwr_handle_ = wwr->handle;

      auto status = esp_ble_gattc_register_for_notify(this->parent_->get_gattc_if(), this->parent_->get_remote_bda(),
                                                      nfy->handle);
      if (status) {
        ESP_LOGW(this->tag_(), "[%s] esp_ble_gattc_register_for_notify failed, status=%d", this->get_name().c_str(),
                 status);
      }
      break;
    }
    default:
      break;
  }
}

void MadokaBase::gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                                     esp_ble_gattc_cb_param_t *param) {
  switch (event) {
    case ESP_GATTC_DISCONNECT_EVT: {
      this->node_state = espbt::ClientState::IDLE;  // ??
      this->current_temperature = NAN;
      this->on_disconnect_();
      this->publish_state();
      break;
    }
    case ESP_GATTC_WRITE_DESCR_EVT:
      if (param->write.status != ESP_GATT_OK) {
        if (param->write.status == ESP_GATT_INSUF_AUTHENTICATION) {
          ESP_LOGE(this->tag_(), "Insufficient authentication");
        } else {
          ESP_LOGE(this->tag_(), "Failed writing characteristic descriptor, status = 0x%x", param->write.status);
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
        ESP_LOGW(this->tag_(), "Different notify handle");
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

void MadokaBase::update() {
  ESP_LOGD(this->tag_(), "Got update request...");
  if (this->node_state != espbt::ClientState::ESTABLISHED) {
    ESP_LOGD(this->tag_(), "...but device is disconnected");
    return;
  }

  this->query_(CMD_GET_SETTING_STATUS, std::vector<uint8_t>{0x00, 0x00}, 50);
  this->query_(CMD_GET_OPERATION_MODE, std::vector<uint8_t>{0x00, 0x00}, 50);
  this->query_appliance_state_();
  this->query_(CMD_GET_SENSOR_INFORMATION, std::vector<uint8_t>{0x00, 0x00}, 50);
  this->query_(CMD_GET_CLEAN_FILTER, std::vector<uint8_t>{0x00, 0x00}, 50);
  this->query_(CMD_GET_VERSION, std::vector<uint8_t>{0x00, 0x00}, 50);
  this->query_(CMD_GET_EYE_BRIGHTNESS, std::vector<uint8_t>{0x33, 0x01, 0x00}, 50);
}

void MadokaBase::set_eye_brightness(uint8_t level) {
  if (this->node_state != espbt::ClientState::ESTABLISHED) {
    return;
  }
  this->query_(CMD_SET_EYE_BRIGHTNESS, std::vector<uint8_t>{0x33, 0x01, level}, 200);
  if (this->eye_brightness_number_ != nullptr) {
    this->eye_brightness_number_->publish_state(level);
  }
  this->should_update_ = true;
}

void MadokaBase::reset_filter() {
  if (this->node_state != espbt::ClientState::ESTABLISHED) {
    return;
  }
  this->query_(CMD_RESET_FILTER, std::vector<uint8_t>{0x51, 0x01, 0x01, 0xFE, 0x01, 0x01}, 200);
  if (this->clean_filter_binary_sensor_ != nullptr) {
    this->clean_filter_binary_sensor_->publish_state(false);
  }
  this->should_update_ = true;
}

bool validate_buffer(const std::vector<uint8_t> &buffer) { return buffer[0] == buffer.size(); }

void MadokaBase::process_incoming_chunk_(std::vector<uint8_t> chk) {
  if (chk.size() < 2) {
    ESP_LOGI(this->tag_(), "Chunk discarded: invalid length.");
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
      ESP_LOGW(this->tag_(), "New message detected, clearing incomplete buffer (chunk_id=0).");
      this->pending_chunks_.clear();
    } else {
      ESP_LOGE(this->tag_(), "Another packet with the same chunk ID is already in the buffer.");
      ESP_LOGD(this->tag_(), "Chunk ID: %d.", chunk_id);
      return;
    }
  }
  this->pending_chunks_[chunk_id] = chk;

  if (this->pending_chunks_.size() != this->pending_chunks_.rbegin()->first + 1) {
    ESP_LOGW(this->tag_(), "Buffer is missing packets");
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

std::vector<std::vector<uint8_t>> MadokaBase::split_payload_(std::vector<uint8_t> msg) {
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

std::vector<uint8_t> MadokaBase::prepare_message_(uint16_t cmd, std::vector<uint8_t> args) {
  std::vector<uint8_t> result({0x00, (uint8_t) ((cmd >> 8) & 0xFF), (uint8_t) (cmd & 0xFF)});
  result.insert(result.end(), args.begin(), args.end());
  return result;
}

void MadokaBase::query_(uint16_t cmd, std::vector<uint8_t> args, int t_d) {
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
      ESP_LOGD(this->tag_(), "[%s] esp_ble_gattc_write_char failed (%d of %d), status=%d",
               this->parent_->address_str(), j + 1, BLE_SEND_MAX_RETRIES, status);
    }
    if (status) {
      ESP_LOGE(this->tag_(), "[%s] Command could not be sent, last status=%d", this->parent_->address_str(), status);
      return;
    }
  }
  // Blocking, and known to be: it stalls the main loop for the sum of these
  // delays on every poll. Removing it means pacing the writes from loop()
  // instead, which changes when responses arrive, and neither CI nor any
  // hardware here can exercise a VAM. Left as it was until it can be measured
  // on a device -- see issue #61.
  esphome::delay(t_d);
}

}  // namespace madoka_base
}  // namespace esphome

#endif
