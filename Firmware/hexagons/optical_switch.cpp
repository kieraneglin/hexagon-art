#include "optical_switch.h"

#include <Arduino.h>

OpticalSwitch::OpticalSwitch(uint8_t pin) : _pin(pin) {}

void OpticalSwitch::begin() {
  pinMode(_pin, INPUT_PULLUP);
}

bool OpticalSwitch::isTriggered() const {
  return digitalRead(_pin) == LOW;
}

bool OpticalSwitch::isClear() const {
  return digitalRead(_pin) == HIGH;
}
