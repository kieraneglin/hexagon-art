/*
 * programmer.ino — Hexagon Programmer
 *
 * All-in-one ATtiny1616 UPDI programming + oscillator calibration jig.
 * Based on ElTangas/jtag2updi (JTAG ICE mkII → UPDI bridge).
 *
 * At a glance:
 *   Pins 0,1 → Host UART (avrdude JTAG ICE protocol in PROGRAM mode;
 *              transparent passthrough to the target's UART in CALIBRATE
 *              mode)
 *   Pin 4    → Mode button (active-low, pin 3 as its soft ground)
 *   Pin 5    → Mode LED (pin 7 as its soft ground) — solid in PROGRAM,
 *              ~1 Hz blink in CALIBRATE
 *   Pin 6    → UPDI data → target UPDI pin
 *   Pin 9    → 500 Hz square wave → target PA6 (oscillator calibration),
 *              CALIBRATE mode only
 *
 * See README.md for wiring, setup, and usage.
 */

// This file is empty by design — the real entry point is main() in
// programmer.cpp. The .ino file exists so the Arduino IDE recognises
// this directory as a sketch. additions.h / additions.cpp provide the
// mode switching, 500 Hz reference, and target UART passthrough.
