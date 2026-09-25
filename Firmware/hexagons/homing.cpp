#include "homing.h"

bool homeMotor(Stepper28BYJ& motor, OpticalSwitch& endstop, uint16_t maxSteps) {
  // Phase 1: if already triggered, back off until clear.
  if (endstop.isTriggered()) {
    motor.setDirection(StepperDirection::REVERSE);
    motor.runStart();
    uint16_t steps = 0;
    while (endstop.isTriggered()) {
      if (motor.runTick() && ++steps >= maxSteps) {
        motor.runStop();
        return false;
      }
    }
    motor.runStop();
  }

  // Phase 2: advance forward until the endstop triggers.
  motor.setDirection(StepperDirection::FORWARD);
  motor.runStart();
  uint16_t steps = 0;
  while (endstop.isClear()) {
    if (motor.runTick() && ++steps >= maxSteps) {
      motor.runStop();
      return false;
    }
  }
  motor.runStop();

  // Phase 3: keep advancing through the trigger window, counting steps.
  uint16_t triggerSteps = 0;
  motor.runStart();  // still FORWARD
  while (endstop.isTriggered()) {
    if (motor.runTick()) {
      if (++triggerSteps >= maxSteps) {
        motor.runStop();
        return false;
      }
    }
  }
  motor.runStop();

  // Phase 4: reverse to the midpoint of the trigger window.
  // Guard: if the trigger window was too narrow to measure (< 2 steps), skip
  // the back-off rather than calling step(0), which corrupts AccelStepper's
  // internal timing state and causes the next homing attempt to hang.
  if (triggerSteps >= 2) {
    motor.setDirection(StepperDirection::REVERSE);
    motor.step(triggerSteps / 2);
  }

  return true;
}
