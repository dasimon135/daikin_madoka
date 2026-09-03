#pragma once

#include <vector>
#include <queue>
#include <map>
#include <string>

#include "esphome/core/component.h"
#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/ble_client/ble_client.h"
#include "esphome/components/button/button.h"
#include "esphome/components/esp32_ble_tracker/esp32_ble_tracker.h"
#include "esphome/components/climate/climate.h"
#include "esphome/components/number/number.h"
#include "esphome/components/text_sensor/text_sensor.h"

#ifdef USE_ESP32

#include <esp_gattc_api.h>

namespace esphome {
namespace madoka_base {

static const uint8_t MAX_CHUNK_SIZE = 20;
static const uint8_t BLE_SEND_MAX_RETRIES = 5;

// Commands every BRC1H answers, whatever the appliance behind it. The ones
// that only make sense for one appliance (setpoint, fan speed, ventilation)
// stay in the component that uses them, sent from query_appliance_state_().
static const uint16_t CMD_GET_SETTING_STATUS = 0x0020;
static const uint16_t CMD_SET_SETTING_STATUS = 0x4020;
static const uint16_t CMD_GET_OPERATION_MODE = 0x0030;
static const uint16_t CMD_SET_OPERATION_MODE = 0x4030;
static const uint16_t CMD_GET_SENSOR_INFORMATION = 0x0110;
static const uint16_t CMD_GET_CLEAN_FILTER = 0x0100;
static const uint16_t CMD_GET_VERSION = 0x0130;
static const uint16_t CMD_GET_EYE_BRIGHTNESS = 0x0302;
static const uint16_t CMD_RESET_FILTER = 0x4220;
static const uint16_t CMD_SET_EYE_BRIGHTNESS = 0x4302;

struct Status {
  bool status;
  uint8_t mode;
};

namespace espbt = esphome::esp32_ble_tracker;

static const espbt::ESPBTUUID MADOKA_SERVICE_UUID = espbt::ESPBTUUID::from_raw("2141e110-213a-11e6-b67b-9e71128cae77");
static const espbt::ESPBTUUID NOTIFY_CHARACTERISTIC_UUID =
    espbt::ESPBTUUID::from_raw("2141e111-213a-11e6-b67b-9e71128cae77");
static const espbt::ESPBTUUID WWR_CHARACTERISTIC_UUID =
    espbt::ESPBTUUID::from_raw("2141e112-213a-11e6-b67b-9e71128cae77");

bool validate_buffer(const std::vector<uint8_t> &buffer);

/// Everything the BRC1H protocol does that does not depend on what is wired
/// behind it: the GATT plumbing, the 20-byte chunking, the poll loop, and the
/// three features every unit exposes (clean filter, firmware version, eye
/// brightness).
///
/// A subclass supplies four things: what to call itself in logs, which extra
/// state to poll, how to decode a reassembled message, and how to answer a
/// climate call.
class MadokaBase : public climate::Climate, public esphome::ble_client::BLEClientNode, public PollingComponent {
 protected:
  bool should_update_ = false;
  std::queue<std::vector<uint8_t>> received_chunks_ = {};
  std::map<uint8_t, std::vector<uint8_t>> pending_chunks_ = {};
  uint16_t notify_handle_;
  uint16_t wwr_handle_;
  SemaphoreHandle_t receive_semaphore_ = nullptr;
  Status cur_status_;
  binary_sensor::BinarySensor *clean_filter_binary_sensor_{nullptr};
  text_sensor::TextSensor *firmware_version_text_sensor_{nullptr};
  number::Number *eye_brightness_number_{nullptr};
  button::Button *reset_filter_button_{nullptr};

  std::vector<std::vector<uint8_t>> split_payload_(std::vector<uint8_t> msg);
  std::vector<uint8_t> prepare_message_(uint16_t cmd, std::vector<uint8_t> args);
  void query_(uint16_t cmd, std::vector<uint8_t> args, int t_d);
  void process_incoming_chunk_(std::vector<uint8_t> chk);

  /// Log tag, so a shared code path still logs under the component the user
  /// configured rather than under this base.
  virtual const char *tag_() const = 0;
  /// Human name used by dump_config and by the "not a Daikin Madoka" warning.
  virtual const char *label_() const = 0;
  /// Decode one reassembled message. Called from the loop, never from an
  /// interrupt or a BLE callback.
  virtual void parse_cb_(std::vector<uint8_t> msg) = 0;
  /// Queries that only this appliance understands, sent in the middle of the
  /// poll cycle so the shared order around them is preserved.
  virtual void query_appliance_state_() = 0;
  /// Extra setup after the receive mutex exists.
  virtual void on_setup_() {}
  /// Reset appliance-specific published state when the link drops.
  virtual void on_disconnect_() {}

 public:
  void setup() override;
  void loop() override;
  void update() override;
  void gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                           esp_ble_gattc_cb_param_t *param) override;
  void gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) override;
  // dump_config() stays in each component: LOG_CLIMATE reads a TAG from the
  // enclosing scope and puts its label through LOG_STR_LITERAL, so neither can
  // come from a virtual here.
  void set_clean_filter_binary_sensor(binary_sensor::BinarySensor *sensor) {
    this->clean_filter_binary_sensor_ = sensor;
  }
  void set_firmware_version_text_sensor(text_sensor::TextSensor *sensor) {
    this->firmware_version_text_sensor_ = sensor;
  }
  void set_eye_brightness_number(number::Number *number) { this->eye_brightness_number_ = number; }
  void set_reset_filter_button(button::Button *button) { this->reset_filter_button_ = button; }
  void set_eye_brightness(uint8_t level);
  void reset_filter();
  float get_setup_priority() const override { return setup_priority::DATA; }
};

}  // namespace madoka_base
}  // namespace esphome

#endif
