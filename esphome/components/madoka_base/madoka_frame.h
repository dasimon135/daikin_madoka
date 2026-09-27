#pragma once

// Argument walker for a reassembled BRC1H frame. Plain C++ with no ESPHome or
// ESP-IDF dependency on purpose, so esphome/tests/host/ can compile and run it
// on a build machine: a malformed frame reaching this code on an ESP32 is a
// crash and a reboot loop, not a failed poll.

#include <cstddef>
#include <cstdint>
#include <vector>

namespace esphome {
namespace madoka_base {

// A reassembled frame, chunk ids already stripped:
//   byte 0      total frame size (the reassembler checks it matches)
//   byte 1      0x00 (high byte of the 24-bit command id)
//   bytes 2..3  function id, big-endian (e.g. 0x0110 sensor information)
//   bytes 4..   arguments, each <id:1><len:1><value:len>
// Same layout pymadoka-ng parses in FeatureStatus.parse().
static const size_t FRAME_HEADER_SIZE = 4;

// A length byte of 0xFF means "no value": pymadoka-ng reads it as 0, and so
// does this walker. Real units also send a plain 0 length: a VAM answers
// function 0x0050 with every argument empty, and a BRC1H without energy
// counters answers 0x0120 with an empty value.
static const uint8_t ARGUMENT_LENGTH_NONE = 0xFF;

/// One argument of a frame. `value` points into the frame and is only valid
/// while the frame is; `len` bytes may be read from it and no more. A
/// zero-length argument is reported with len == 0: callers check `len` before
/// reading, pymadoka-ng's substitution of a single 0x00 is not applied here
/// so a missing value never overwrites a real one.
struct FrameArgument {
  uint8_t id{0};
  const uint8_t *value{nullptr};
  size_t len{0};

  /// Big-endian 16-bit value. Only call when len >= 2.
  uint16_t u16() const { return static_cast<uint16_t>(value[0] << 8 | value[1]); }
};

enum class FrameArgumentResult {
  OK,         // `arg` holds the next argument, `pos` moved past it
  END,        // no more arguments
  TRUNCATED,  // the frame ends inside an argument; nothing more can be read
};

/// Read the argument starting at `pos` (FRAME_HEADER_SIZE for the first one)
/// and advance `pos` past it. Never reads outside `frame`.
inline FrameArgumentResult next_frame_argument(const std::vector<uint8_t> &frame, size_t &pos, FrameArgument &arg) {
  const size_t size = frame.size();
  if (pos >= size) {
    return FrameArgumentResult::END;
  }
  // An argument id with no length byte after it.
  if (size - pos < 2) {
    return FrameArgumentResult::TRUNCATED;
  }
  size_t len = frame[pos + 1];
  if (len == ARGUMENT_LENGTH_NONE) {
    len = 0;
  }
  // A length that runs past the end of the frame.
  if (len > size - pos - 2) {
    return FrameArgumentResult::TRUNCATED;
  }
  arg.id = frame[pos];
  arg.value = frame.data() + pos + 2;
  arg.len = len;
  pos += 2 + len;
  return FrameArgumentResult::OK;
}

/// Outdoor temperature (function 0x0110, argument 0x41), in whole degrees C.
/// Sign-magnitude, not two's complement: 0xFF means "no outdoor probe",
/// otherwise bit 7 is the sign and the low seven bits the magnitude, so
/// 0x85 = -5 C. Rule from the OpenHAB binding written by the protocol's
/// reverse engineer (openhab-addons,
/// bundles/org.openhab.binding.bluetooth.daikinmadoka,
/// GetIndoorOutoorTemperatures.java). Keep it identical to the rule in
/// pymadoka-ng. No capture below 0 C has confirmed it yet.
/// Returns false when the unit reports no reading.
inline bool decode_outdoor_temperature(uint8_t raw, int &celsius) {
  if (raw == 0xFF) {
    return false;
  }
  celsius = (raw & 0x80) ? -static_cast<int>(raw - 128) : static_cast<int>(raw);
  return true;
}

/// Function id of a frame. Only call when frame.size() >= FRAME_HEADER_SIZE.
inline uint16_t frame_function_id(const std::vector<uint8_t> &frame) {
  return static_cast<uint16_t>(frame[2] << 8 | frame[3]);
}

}  // namespace madoka_base
}  // namespace esphome
