/*
 * jtag2updi.ino — Hexagon Programmer
 *
 * All-in-one ATtiny1616 programming and calibration jig.
 * Based on ElTangas/jtag2updi (JTAG ICE mkII → UPDI bridge).
 *
 * At a glance:
 *   Pin 0,1 → Host UART (avrdude JTAG ICE protocol)
 *   Pin 2   → Target UART RX (hexagon TX → operator console)
 *   Pin 6   → UPDI data → target UPDI pin
 *   Pin 9   → 500 Hz square wave → target PA6 (oscillator calibration)
 *
 * The 500 Hz signal runs on hardware Timer1 — zero CPU overhead.
 * Target UART echo is polled (non-blocking), suppressed during
 * active avrdude sessions to avoid protocol corruption.
 *
 * See AGENTS.md for setup and usage.
 */

// This file is empty by design — the real entry point is main() in
// jtag2updi.cpp.  The .ino file exists so the Arduino IDE recognises
// this directory as a sketch.  additions.h / additions.cpp provide
// the 500 Hz reference and target UART monitor.
