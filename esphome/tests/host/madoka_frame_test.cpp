// Host-side checks for the frame argument walker in
// esphome/components/madoka_base/madoka_frame.h. No ESPHome, no ESP-IDF: the
// header is plain C++ so this builds with the runner's g++. The command is in
// .github/workflows/esphome.yml (job "host").
//
// The sanitizers matter: an out-of-bounds read is the defect this walker
// exists to prevent, and without them such a read can pass silently here.

#include "madoka_base/madoka_frame.h"

#include <cstdio>
#include <cstdlib>
#include <vector>

using esphome::madoka_base::decode_outdoor_temperature;
using esphome::madoka_base::FRAME_HEADER_SIZE;
using esphome::madoka_base::frame_function_id;
using esphome::madoka_base::FrameArgument;
using esphome::madoka_base::FrameArgumentResult;
using esphome::madoka_base::next_frame_argument;

static int failures = 0;

#define CHECK(cond) \
  do { \
    if (!(cond)) { \
      std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
      failures++; \
    } \
  } while (0)

// Walk a frame and return the arguments seen plus the final result. Each
// argument's value is copied while the frame is alive, so the sanitizer sees
// every byte read; `args[i].value` must not be used after walk() returns
// (it points into a frame that may have been a temporary), use values[i].
struct Walk {
  std::vector<FrameArgument> args;
  std::vector<std::vector<uint8_t>> values;
  FrameArgumentResult last;
};

static Walk walk(const std::vector<uint8_t> &frame) {
  Walk w;
  size_t pos = FRAME_HEADER_SIZE;
  FrameArgument arg;
  while ((w.last = next_frame_argument(frame, pos, arg)) == FrameArgumentResult::OK) {
    w.args.push_back(arg);
    w.values.emplace_back(arg.value, arg.value + arg.len);
    if (arg.len >= 2) {
      CHECK(arg.u16() == (uint16_t) (arg.value[0] << 8 | arg.value[1]));
    }
    CHECK(pos <= frame.size());
  }
  return w;
}

// A header, sized as the reassembler requires (byte 0 = frame size).
static std::vector<uint8_t> frame(uint16_t function_id, std::vector<uint8_t> args) {
  std::vector<uint8_t> f{0x00, 0x00, (uint8_t) (function_id >> 8), (uint8_t) (function_id & 0xFF)};
  f.insert(f.end(), args.begin(), args.end());
  f[0] = (uint8_t) f.size();
  // No spare capacity, so a read one byte past the end leaves the heap block
  // and AddressSanitizer reports it.
  f.shrink_to_fit();
  return f;
}

int main() {
  // Header only: no arguments, clean end.
  {
    auto f = frame(0x0110, {});
    CHECK(frame_function_id(f) == 0x0110);
    Walk w = walk(f);
    CHECK(w.args.empty());
    CHECK(w.last == FrameArgumentResult::END);
  }
  // Two well-formed arguments, 1 and 2 bytes.
  {
    Walk w = walk(frame(0x0040, {0x20, 0x01, 0x07, 0x21, 0x02, 0x0B, 0x00}));
    CHECK(w.args.size() == 2);
    CHECK(w.args[0].id == 0x20 && w.args[0].len == 1 && w.values[0][0] == 0x07);
    CHECK(w.args[1].id == 0x21 && w.args[1].len == 2 && w.values[1][0] == 0x0B && w.values[1][1] == 0x00);
    CHECK(w.last == FrameArgumentResult::END);
  }
  // Zero-length argument (what a VAM sends for every 0x0050 argument, and a
  // unit without energy counters for 0x0120): reported with len 0, the walk
  // continues with the next argument.
  {
    Walk w = walk(frame(0x0110, {0x40, 0x00, 0x41, 0x01, 0x0C}));
    CHECK(w.args.size() == 2);
    CHECK(w.args[0].id == 0x40 && w.args[0].len == 0);
    CHECK(w.args[1].id == 0x41 && w.args[1].len == 1 && w.values[1][0] == 0x0C);
    CHECK(w.last == FrameArgumentResult::END);
  }
  // Length 0xFF means "no value" (pymadoka-ng reads it as 0), not 255 bytes.
  {
    Walk w = walk(frame(0x0110, {0x40, 0xFF, 0x41, 0x01, 0x0C}));
    CHECK(w.args.size() == 2);
    CHECK(w.args[0].id == 0x40 && w.args[0].len == 0);
    CHECK(w.args[1].id == 0x41 && w.values[1][0] == 0x0C);
  }
  // Zero-length argument as the very last bytes of the frame.
  {
    Walk w = walk(frame(0x0110, {0x40, 0x00}));
    CHECK(w.args.size() == 1 && w.args[0].len == 0);
    CHECK(w.last == FrameArgumentResult::END);
  }
  // A trailing argument id with no length byte: truncated, earlier
  // arguments still delivered.
  {
    Walk w = walk(frame(0x0110, {0x40, 0x01, 0x15, 0x41}));
    CHECK(w.args.size() == 1 && w.values[0][0] == 0x15);
    CHECK(w.last == FrameArgumentResult::TRUNCATED);
  }
  // A length running past the end of the frame: truncated, nothing read.
  {
    Walk w = walk(frame(0x0040, {0x20, 0x02, 0x0B}));
    CHECK(w.args.empty());
    CHECK(w.last == FrameArgumentResult::TRUNCATED);
  }
  // A large length byte (not 0xFF) must not wrap anything: truncated.
  {
    Walk w = walk(frame(0x0040, {0x20, 0xFE, 0x01, 0x02}));
    CHECK(w.args.empty());
    CHECK(w.last == FrameArgumentResult::TRUNCATED);
  }
  // Largest possible frame (255 bytes, its size byte cannot say more),
  // filled with one argument per two bytes of length 0xFF: the walk ends
  // cleanly, one empty argument at a time.
  {
    std::vector<uint8_t> args;
    while (args.size() + FRAME_HEADER_SIZE + 2 <= 254) {
      args.push_back(0x10);
      args.push_back(0xFF);
    }
    args.push_back(0x10);  // odd byte out: an id with no length
    auto f = frame(0x0031, args);
    CHECK(f.size() == 255);
    Walk w = walk(f);
    CHECK(w.args.size() == 125);
    CHECK(w.last == FrameArgumentResult::TRUNCATED);
  }
  // A frame shorter than its header has no arguments to walk.
  {
    std::vector<uint8_t> f{0x02, 0x00};
    size_t pos = FRAME_HEADER_SIZE;
    FrameArgument arg;
    CHECK(next_frame_argument(f, pos, arg) == FrameArgumentResult::END);
  }
  // Outdoor temperature: sign-magnitude, 0xFF = no probe.
  {
    int c = 999;
    CHECK(!decode_outdoor_temperature(0xFF, c));
    CHECK(c == 999);
    CHECK(decode_outdoor_temperature(0x00, c) && c == 0);
    CHECK(decode_outdoor_temperature(0x0C, c) && c == 12);
    CHECK(decode_outdoor_temperature(0x7F, c) && c == 127);
    CHECK(decode_outdoor_temperature(0x85, c) && c == -5);
    CHECK(decode_outdoor_temperature(0x80, c) && c == 0);
    CHECK(decode_outdoor_temperature(0xFE, c) && c == -126);
  }

  if (failures != 0) {
    std::fprintf(stderr, "%d check(s) failed\n", failures);
    return EXIT_FAILURE;
  }
  std::printf("madoka_frame: all checks passed\n");
  return EXIT_SUCCESS;
}
