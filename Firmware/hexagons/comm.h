#pragma once
#include <stdint.h>

enum class CommandType : uint8_t {
  NONE,
  MOVE_INCREMENTAL,  // M+90 or M-45 — rotate by angle relative to current
  MOVE_ABSOLUTE,     // A180 — go to absolute angle via shortest path
  HOME,              // H — run homing sequence
  STOP,              // X — immediately stop; bypasses queue
  SET_SPEED,         // S145 — set motor speed in tenths of RPM (S145 = 14.5 RPM)
  GO,                // G — execute the staged command queue
  CLEAR,             // C — discard the staged command queue
  WAIT,              // W — pause N ms before next command
  CLOCK_NUDGE,       // K+/K- — increment/decrement OSCCAL by 1, persist to USERROW
  POWER_DOWN,        // P — release motor coils; unit must home before next move
  RELEASE,           // D — release motor coils in sequence; position retained, no re-home needed
  CONDITIONAL_HOME,  // N — home only if the unit still needs it; no-op otherwise
  REPEAT,            // R<n> — repeat the following batch n times (0 = forever)
  SET_ID,            // I<hex> — reprovision this unit's address; must be addressed to its CURRENT id, never FF
  SEPARATOR,         // Internal fencepost — never parsed from UART, only created by GO handler
};

struct Command {
  CommandType type;
  uint16_t param;  // command-specific payload:
                   //   MOVE_INCREMENTAL — degrees (1–65535)
                   //   MOVE_ABSOLUTE    — degrees (0–359)
                   //   WAIT             — duration in ms (1–65535)
                   //   SET_SPEED        — tenths of RPM (10–300; i.e. 1.0–30.0 RPM)
                   //   CLOCK_NUDGE      — unused (always 0)
                   //   REPEAT           — pass count (0 = loop forever, 1 = no-op)
                   //   SET_ID           — new address (0x01–0xFE)
  bool direction;  // true = forward/+, false = reverse/-
                   //   MOVE_INCREMENTAL — motor direction
                   //   CLOCK_NUDGE      — true = nudge up (+, faster), false = nudge down (-, slower)
};

// Call once in setup(). Initialises the hardware Serial port.
void commBegin(unsigned long baud);

// Reads this unit's address from EEPROM address 0x00.
// If the stored value is 0x00 or 0xFF (unprogrammed), prints a message and
// halts forever, blinking the status LED (2 long flashes, pause, repeat) —
// does not return in that case. Provisioning is via the I command
// (commReprovisionAddress()) or avrdude terminal mode, never over the bus.
// Call after commBegin() so Serial is available for the halt message.
uint8_t commReadAddressFromEEPROM();

// Set this unit's address (0x01–0xFE). Must be called before commPoll().
void commSetAddress(uint8_t addr);

// Handler for SET_ID: persists newAddr to EEPROM address 0x00 and applies it
// live (no reboot needed). Called from runner.cpp's immediate-command
// dispatch. See parseLine()'s 'I' case for why broadcast can never reach it.
void commReprovisionAddress(uint8_t newAddr);

// Call every loop() iteration. Non-blocking.
// Returns a parsed Command when a complete message addressed to this unit
// (or broadcast FF) has been received, or {CommandType::NONE} when nothing
// is ready. Messages addressed to other units are silently discarded.
Command commPoll();
