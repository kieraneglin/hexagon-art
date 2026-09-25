#pragma once
#include <stdint.h>

// Driver for an optical endstop (or similar) sensor whose signal line
// reads LOW when idle and HIGH when triggered.
class OpticalSwitch {
 public:
  // `pin` — the Arduino pin connected to the sensor's signal line.
  explicit OpticalSwitch(uint8_t pin);

  // Call once in setup() to configure the pin mode.
  void begin();

  // Returns true when the sensor beam is interrupted (signal LOW).
  bool isTriggered() const;

  // Returns true when the sensor beam is clear (signal HIGH).
  bool isClear() const;

 private:
  uint8_t _pin;
};
