#pragma once
#include <AccelStepper.h>
#include <stdint.h>

enum class StepperDirection {
  FORWARD,  // clockwise
  REVERSE   // counter-clockwise
};

// Driver for the 28BYJ-48 stepper motor via the ULN2003 board.
// Uses AccelStepper in HALF4WIRE mode; 4096 steps per output-shaft revolution.
// The actual gear ratio is 63.68395:1 and the internal steps-per-rev is 64 so
// the math works out to 4096 steps/rev
class Stepper28BYJ {
 public:
  static const uint16_t STEPS_PER_REV = 4096;

  Stepper28BYJ(uint8_t in1, uint8_t in2, uint8_t in3, uint8_t in4);

  // Call once in setup() to configure pin modes.
  void begin();

  // Advance the motor by `steps` steps in the current direction. Blocking.
  void step(uint16_t steps);

  // Set rotation direction.
  void setDirection(StepperDirection dir);

  // Set the inter-step delay directly in microseconds.
  void setDelay(unsigned int us);

  // Derive the inter-step delay from a target shaft speed in RPM.
  void setRPM(float rpm);

  // Non-blocking constant-speed run — intended for homing loops.
  // Call runStart() once, then runTick() every iteration (returns true when a
  // step is taken), then runStop() when done.
  void runStart();
  bool runTick();
  void runStop();

  // Signal an in-progress step() call, or a tick()-driven move, to abort at
  // its next iteration. If no step() is in progress, the next step() call
  // will return immediately without moving. The flag is consumed (cleared)
  // by step()/tick(). For a tick()-driven move, tick() commits the angle
  // actually reached (not the pre-move value and not the target) before
  // returning, so the motor's tracked position stays trustworthy without a
  // re-home — coils stay energised and holding torque is never released.
  void stop();

  // De-energise all coils — removes holding torque and cuts idle power draw.
  // Position tracking is fully preserved (AccelStepper's step counter is
  // untouched; the next move silently re-energises from it). Whether a
  // re-home is required is the caller's decision, not this function's —
  // runner.cpp's automatic idle release and D command call this without
  // requiring one; P does, but only because it also clears the queue.
  void release();

  // High-level param-based moves. Non-blocking: call tick() every loop().

  // Queue a rotation by `degrees` in the given direction.
  void moveByAngle(uint16_t degrees, bool forward);

  // Queue a move to `target` (0–359) via the shortest path.
  void moveToAngle(uint16_t target);

  // Reset the tracked position to 0° (call after a successful home). Also
  // anchors AccelStepper's own step counter to 0 at this shaft position, so
  // liveAngle() stays meaningful — see liveAngle().
  void resetAngle();

  // Advance the motor by one step if a non-blocking move is in progress.
  // Commits _currentAngle when the move completes. Respects stop().
  // Call once per loop() iteration.
  bool tick();

  // Returns true while a non-blocking move is in progress.
  bool isBusy();

  // Commanded absolute position, 0–359°, derived from AccelStepper's own
  // step counter — unlike _currentAngle, this updates in real time as steps
  // are taken, including mid-move. Only meaningful relative to the shaft
  // position resetAngle() was last called at.
  uint16_t liveAngle();

 private:
  AccelStepper _stepper;
  StepperDirection _direction;
  volatile bool _stopFlag;
  int16_t _currentAngle;  // 0–359, degrees
  int16_t _targetAngle;   // committed to _currentAngle on completion
  long _moveDelta;        // signed steps requested by the in-flight moveByAngle(); 0 when idle
};

// ---------------------------------------------------------------------------
// Angular math helpers
// ---------------------------------------------------------------------------

// Convert degrees to motor half-steps with rounding.
// Intermediate arithmetic uses uint32_t; safe for degrees up to 65535.
uint32_t angleToSteps(uint16_t degrees);

// Shortest signed angular distance from current to target, wrapped to
// [-180, +180]. Positive = forward (CW), negative = reverse (CCW).
int16_t computeDelta(uint16_t target, uint16_t current);
