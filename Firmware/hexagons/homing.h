#pragma once
#include "optical_switch.h"
#include "stepper.h"

// Homes `motor` to the midpoint of the endstop trigger window.
//
// Sequence:
//   1. If the endstop is already triggered, reverse until it clears.
//   2. Advance forward one step at a time until the endstop triggers.
//   3. Keep advancing, counting steps, until the endstop clears again.
//   4. Reverse by half that step count — the midpoint of the trigger window.
//
// `maxSteps` is a per-phase safety limit; if any phase exceeds it the
// routine stops and returns false. Returns true on success.
bool homeMotor(Stepper28BYJ& motor, OpticalSwitch& endstop, uint16_t maxSteps = 4096);
