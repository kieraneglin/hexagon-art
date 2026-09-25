#pragma once
#include "optical_switch.h"
#include "stepper.h"

// ---------------------------------------------------------------------------
// Runner — queue + motor integration
// ---------------------------------------------------------------------------
//
// Owns the STAGING/RUNNING state machine, the wait timer, and command
// dispatch. Bridges comm → queue → stepper so that hexagons.ino only has
// to deal with hardware initialisation.
//
// Typical usage:
//   setup(): hardware init, then runnerBegin(motor, endstop)
//   loop():  runnerUpdate()

// Bind the runner to the motor and endstop objects created in the main sketch.
// Must be called once in setup(), after motor.begin() and endstop.begin().
void runnerBegin(Stepper28BYJ& motor, OpticalSwitch& endstop);

// Call every loop() iteration. Ticks the motor, drains the staged command
// queue when running, and processes one incoming UART command.
void runnerUpdate();
