#include "stepper.h"

// The 28BYJ-48's internal coil layout requires pins passed to AccelStepper
// in (in1, in3, in2, in4) order for HALF4WIRE to produce a valid half-step
// sequence. Straight (in1, in2, in3, in4) activates non-adjacent phases
// together, which causes the rotor to buzz instead of rotate.
Stepper28BYJ::Stepper28BYJ(uint8_t in1, uint8_t in2, uint8_t in3, uint8_t in4)
    : _stepper(AccelStepper::HALF4WIRE, in1, in3, in2, in4),
      _direction(StepperDirection::FORWARD),
      _stopFlag(false),
      _currentAngle(0),
      _targetAngle(0),
      _moveDelta(0) {}

void Stepper28BYJ::begin() {
  // Default speed (~14.6 RPM); overridden by setRPM() / setDelay().
  _stepper.setMaxSpeed(500.0f);
  // High acceleration makes ramp-up negligible, preserving constant-speed feel.
  _stepper.setAcceleration(10000.0f);
}

void Stepper28BYJ::setDirection(StepperDirection dir) {
  _direction = dir;
}

void Stepper28BYJ::setDelay(unsigned int us) {
  _stepper.setMaxSpeed(1000000.0f / (float)us);
}

void Stepper28BYJ::setRPM(float rpm) {
  _stepper.setMaxSpeed(rpm / 60.0f * STEPS_PER_REV);
}

void Stepper28BYJ::step(uint16_t steps) {
  // If stop() was called before this move started, consume the flag and bail.
  if (_stopFlag) {
    _stopFlag = false;
    return;
  }
  long delta = (_direction == StepperDirection::FORWARD) ? (long)steps : -(long)steps;
  _stepper.move(delta);
  while (_stepper.run()) {
    if (_stopFlag) {
      _stopFlag = false;
      // Reset AccelStepper internal velocity state so the next move starts
      // clean.
      _stepper.setCurrentPosition(_stepper.currentPosition());
      return;
    }
  }
}

void Stepper28BYJ::stop() {
  _stopFlag = true;
}

void Stepper28BYJ::release() {
  _stepper.disableOutputs();
}

void Stepper28BYJ::runStart() {
  // Use constant-speed runSpeed() — no accel ramp — so single-step polling
  // in homing loops runs at full speed instead of restarting from rest each
  // step.
  //
  // setCurrentPosition() flushes AccelStepper's internal _lastStepTime and
  // ramp state (_n, _stepInterval) so each homing phase starts from a clean
  // timing baseline. Without this, stale state from a previous phase (or a
  // prior home call) can leave _stepInterval in an inconsistent state that
  // causes runSpeed() to never fire a step, hanging the blocking homing loops.
  _stepper.setCurrentPosition(_stepper.currentPosition());
  float s = _stepper.maxSpeed();
  _stepper.setSpeed(_direction == StepperDirection::FORWARD ? s : -s);
}

bool Stepper28BYJ::runTick() {
  return _stepper.runSpeed();
}

void Stepper28BYJ::runStop() {
  _stepper.setSpeed(0.0f);
}

// ---------------------------------------------------------------------------
// Angular math helpers
// ---------------------------------------------------------------------------

uint32_t angleToSteps(uint16_t degrees) {
  return ((uint32_t)degrees * Stepper28BYJ::STEPS_PER_REV + 180u) / 360u;
}

int16_t computeDelta(uint16_t target, uint16_t current) {
  int16_t delta = (int16_t)target - (int16_t)current;
  if (delta > 180) {
    delta -= 360;
  }
  if (delta < -180) {
    delta += 360;
  }
  return delta;
}

// ---------------------------------------------------------------------------
// High-level param moves (non-blocking — driven by tick())
// ---------------------------------------------------------------------------

void Stepper28BYJ::moveByAngle(uint16_t degrees, bool forward) {
  // Cancel any in-progress move so the new delta isn't added to the old one.
  _stepper.setCurrentPosition(_stepper.currentPosition());

  // Pre-compute the param we'll commit when this move completes.
  if (forward) {
    _targetAngle = (int16_t)(((uint32_t)_currentAngle + degrees) % 360u);
  } else {
    _targetAngle = (int16_t)(((int32_t)_currentAngle - (int32_t)(degrees % 360u) + 360) % 360);
  }

  // Queue the move. AccelStepper uses the sign of delta for direction.
  // Remembered so a mid-move stop can work out how far it actually got.
  long delta = forward ? (long)angleToSteps(degrees) : -(long)angleToSteps(degrees);
  _moveDelta = delta;
  _stepper.move(delta);
}

void Stepper28BYJ::moveToAngle(uint16_t target) {
  int16_t delta = computeDelta(target, (uint16_t)_currentAngle);
  if (delta == 0) {
    return;
  }

  moveByAngle((delta > 0) ? (uint16_t)delta : (uint16_t)(-delta), delta > 0);
}

void Stepper28BYJ::resetAngle() {
  _currentAngle = 0;
  // Anchors liveAngle() to this shaft position. Safe: nothing else reads
  // _stepper.currentPosition() as an absolute value — the other call sites
  // (moveByAngle(), runStart(), the stop path in tick()) only ever
  // self-assign it to flush AccelStepper's internal ramp/timing state.
  _stepper.setCurrentPosition(0);
}

bool Stepper28BYJ::tick() {
  if (_stopFlag) {
    _stopFlag = false;
    // AccelStepper's own step count already reflects reality — nothing is
    // lost there. What's stale is _currentAngle, which only updates on full
    // completion. Work out how much of _moveDelta actually happened and
    // commit that, so a mid-move stop doesn't require a re-home to trust
    // the angle again. (_moveDelta is 0 whenever no move is outstanding, so
    // this is a no-op if the stop lands while idle.)
    long stepsTaken = _moveDelta - _stepper.distanceToGo();
    _moveDelta = 0;
    if (stepsTaken != 0) {
      long numerator = stepsTaken * 360L;
      long half = STEPS_PER_REV / 2;
      int32_t degTraveled = (int32_t)((numerator + (numerator >= 0 ? half : -half)) / STEPS_PER_REV);
      _currentAngle = (int16_t)(((int32_t)_currentAngle + degTraveled) % 360 + 360) % 360;
    }
    _stepper.setCurrentPosition(_stepper.currentPosition());
    return false;
  }

  if (_stepper.distanceToGo() == 0) {
    return false;
  }

  _stepper.run();

  if (_stepper.distanceToGo() == 0) {
    _currentAngle = _targetAngle;
    _moveDelta = 0;
  }

  return (_stepper.distanceToGo() != 0);
}

bool Stepper28BYJ::isBusy() {
  return _stepper.distanceToGo() != 0;
}

uint16_t Stepper28BYJ::liveAngle() {
  long steps = _stepper.currentPosition() % (long)STEPS_PER_REV;
  if (steps < 0) {
    steps += STEPS_PER_REV;
  }
  long numerator = steps * 360L;
  return (uint16_t)(((numerator + STEPS_PER_REV / 2) / STEPS_PER_REV) % 360);
}
