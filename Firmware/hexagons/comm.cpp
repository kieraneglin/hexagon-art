#include "comm.h"

#include <Arduino.h>
#include <EEPROM.h>

// ---------------------------------------------------------------------------
// UART Communication Protocol
// ---------------------------------------------------------------------------
//
// Baud rate : 115200, 8N1
// Direction : one-way (orchestrator → units). No response channel.
// Transport : shared bus — orchestrator broadcasts, all units listen.
//
// Message format:
//
//   <ID><CMD><ARGS>\r\n
//
//   ID   — 2 hex chars, 00–FF.  FF = broadcast all units.  00 = reserved.
//   CMD  — 1 ASCII letter, case-insensitive.
//   ARGS — command-specific (see below).  No spaces.
//   \r\n — \r is tolerated but optional; \n is the true delimiter.
//
// On receiving \n the unit:
//   1. Validates message structure (2 hex + 1 alpha + valid args).
//   2. Checks ID — discards silently if neither FF nor its own address.
//   3. Parses and executes the command.
//   Any parse or range error → silent discard, no side-effects.
//
// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------
//
//  M — Incremental move
//      <ID>M<+|-><ANGLE>
//      Rotate ANGLE degrees (1–65535) in the given direction from current pos.
//      + = forward/CW,  - = reverse/CCW.  Zero param is discarded.
//      Degree→step: round(param × 4096 / 360)
//      Examples:  01M+90   0AM-45   FFM+180
//
//  A — Absolute move
//      <ID>A<ANGLE>
//      Go to absolute position ANGLE (0–359) via the shortest path.
//      0° = home (endstop midpoint).  Wraps at 360°.
//      Δ = target − current, wrapped to [−180, +180]; positive = forward.
//      Examples:  01A0  (go home)   01A180   FFA0  (all units home)
//
//  H — Home
//      <ID>H
//      Run full homing sequence; park at endstop-window midpoint.
//      Resets absolute position counter to 0°.
//      Example:   05H
//
//  X — Stop
//      <ID>X
//      Immediately halt the motor. Coils stay energised through the stop so
//      the tracked angle is committed to wherever it actually stopped, then
//      release automatically once idle (see idle power-save below).
//      Current move is aborted; next move command works normally.
//      Examples:  01X   FFX  (emergency stop all)
//
//  S — Set speed
//      <ID>S<TENTHS>
//      Set shaft speed for subsequent moves. Argument is in tenths of an RPM
//      (10–300), so S145 = 14.5 RPM, S10 = 1.0 RPM, S300 = 30.0 RPM.
//      Does not affect an in-progress move.
//      Example:   01S145
//
//  G — Go (execute queue)
//      <ID>G
//      Execute all staged commands in order. If broadcast (FFG), all units
//      start their own queues simultaneously — the primary choreography
//      primitive for multi-unit sequences.
//      No-op if the queue is empty.
//      Example:   01G   FFG  (fire all units at once)
//
//  C — Clear queue
//      <ID>C
//      Discard all staged commands immediately. Also stops any in-progress
//      queued sequence. Does not affect an in-progress non-queued move.
//      Example:   01C   FFC
//
//  W — Wait
//      <ID>W<ms>
//      Pause execution of the queue for <ms> milliseconds (1–65535) before
//      proceeding to the next staged command. Non-blocking — the unit
//      continues polling for X/C commands during the wait.
//      Example:   01W500   FFW1000
//
//  D — Release (staged power-save)
//      <ID>D
//      Release motor coils at this point in the queue, without clearing the
//      queue or requiring a re-home — position is retained, same contract as
//      the automatic idle release below. Draining continues normally; the
//      next queued move re-energises and runs from the tracked angle as if
//      nothing happened. Unlike P, this does NOT set the "needs home" flag,
//      so it does not affect N.
//      Useful for a choreography whose waits are shorter than the idle
//      release timer: placing D right before a short W gives a zero-tail
//      release instead of waiting on the timer to catch up.
//      Example:   05D   (release mid-sequence)
//                 01A180 01D 01W500 01A0 01D 01W500 01R0 01G  (release at
//                 every stop, not just after the timer elapses)
//
//  N — Conditional home
//      <ID>N
//      Home only if the unit still needs it (i.e. since a P power-down with
//      no successful home since); otherwise a no-op that drains instantly.
//      Staged like H, but safe to put in every batch without paying the
//      full homing latency on units that don't need it — lets the
//      orchestrator budget homing time (e.g. via a following W) at a known
//      point in the sequence instead of it happening silently inside the
//      first M/A after a power-down. Neither D nor the automatic idle
//      release sets this flag, so neither one requires an N afterward.
//      Example:   05N   FFN
//
//  R — Repeat batch
//      <ID>R<n>
//      Staged at the head of a batch. Repeats everything staged after it (up
//      to the next G's fencepost) n times before returning to STAGING.
//      n=0 loops forever; n=1 is a no-op (identical to omitting R); n>=2 is
//      a fixed pass count. Implemented as pop-and-repush against the queue,
//      so occupancy never grows regardless of n — cannot overflow.
//      A later G during an infinite loop lets the current pass finish, then
//      moves on to the newly-queued batch instead of resuming the loop.
//      C, X, and P all stop a repeating batch immediately, same as any
//      other running sequence.
//      Examples:  01R0   (loop forever until stopped)
//                 01R5   (five passes)
//
//  K — Clock nudge (immediate, not stageable)
//      <ID>K<+|->
//      Adjust this unit’s oscillator calibration by ±1 OSCCAL step.
//      K+ speeds the clock up by one LSB; K- slows it down by one LSB.
//      The new value is written to USERROW byte 0 and applied live —
//      survives power cycle. Clamps at 0x00 and 0x3F; silent no-op at limits.
//      Intended for fine-tuning individual units after deployment.
//      Example:   01K+   01K-   (broadcast FFKx is valid but discouraged)
//
//  I — Set/reprovision unit ID (immediate, not stageable)
//      <ID>I<NEWID>
//      Reprovision this unit's address. NEWID is 2 hex chars, 01-FE.
//      Persists to EEPROM address 0x00 and applies live — no reboot needed.
//      Unlike every other command, broadcast (FF) is REJECTED for I — <ID>
//      must be an exact match for the unit's current address (read it off
//      the "Unit ID: XX" line this unit prints at boot). This is deliberate:
//      a mistaken FFI<x> on the live bus would reassign every unit on it to
//      the same address at once. Meant for repairing a unit whose EEPROM ID
//      got corrupted — connect to it directly and address the command to
//      whatever (possibly wrong) ID it's currently reporting.
//      Example:   50I19   (unit currently reporting as 50 becomes 19)
//
// ---------------------------------------------------------------------------
// Staging model
// ---------------------------------------------------------------------------
//
//  All commands except G, C, X, K, P, and I are staged into a per-unit queue.
//  The queue is executed in order when G is received.
//  X (stop) always executes immediately and clears the queue.
//  C executes immediately and clears the queue without stopping the motor.
//  W is stageable — it pauses queue draining for N ms, then resumes.
//  D is stageable — it releases coils in place without clearing the queue.
//  Broadcast FFG fires all units simultaneously from their individual queues,
//  enabling choreographed multi-unit sequences with a single command.
//
//  Queue capacity: 64 commands (see queue.h).
//
// ---------------------------------------------------------------------------
// Addressing
// ---------------------------------------------------------------------------
//
//   01–FE  Individual unit  (254 addresses available)
//   FF     Broadcast        (all units execute)
//   00     Reserved         (orchestrator ID — never sent as a target)
//
// Unit ID is stored in EEPROM address 0x00. Factory-fresh EEPROM reads 0xFF;
// 0x00 and 0xFF are both treated as "unprovisioned" — the unit will halt on
// startup and blink the built-in LED (2 long flashes, pause, repeat).
// Provision via the I command (see commReprovisionAddress()) or avrdude
// terminal mode — never over the bus, which could otherwise assign an ID
// from stray noise.
//
// ---------------------------------------------------------------------------

// Max valid message: "FFM+65535\r\n" = 12 chars; 16 gives comfortable headroom.
static char s_buf[16];
static uint8_t s_len = 0;
static bool s_overflow = false;
static uint8_t s_address = 0;

void commBegin(unsigned long baud) {
  Serial.begin(baud);
}

void commSetAddress(uint8_t addr) {
  s_address = addr;
}

void commReprovisionAddress(uint8_t newAddr) {
  EEPROM.update(0x00, newAddr);
  s_address = newAddr;

  Serial.print(F("ID reprovisioned: "));
  if (newAddr < 0x10) {
    Serial.print('0');
  }
  Serial.println(newAddr, HEX);
}

// One-shot-then-forever 2-long-flash signal that the unit ID is unprovisioned.
// Deliberately distinct from clockcal.cpp's blinkUncalibrated() (3 short
// flashes) so the two failure modes are told apart at a glance. Sets its own
// pinMode rather than relying on hexagons.ino, which never configures the
// status LED pin as an output.
[[noreturn]] static void blinkUnprovisioned() {
  static const uint8_t STATUS_LED_PIN = PIN_PB1;
  pinMode(STATUS_LED_PIN, OUTPUT);
  while (true) {
    for (uint8_t i = 0; i < 2; i++) {
      digitalWrite(STATUS_LED_PIN, HIGH);
      delay(400);
      digitalWrite(STATUS_LED_PIN, LOW);
      delay(300);
    }
    delay(700);
  }
}

uint8_t commReadAddressFromEEPROM() {
  uint8_t id = EEPROM.read(0x00);
  if (id != 0x00 && id != 0xFF) {
    Serial.print(F("Unit ID: "));
    if (id < 0x10) {
      Serial.print('0');
    }
    Serial.println(id, HEX);

    return id;
  }

  // Unprovisioned. Field re-provisioning is via the I command (see
  // commReprovisionAddress()) or avrdude terminal mode — never over the
  // shared bus from arbitrary serial input, since a stray short line from
  // bus noise could otherwise silently assign an ID. Halt here permanently;
  // this function does not return on this path.
  Serial.println(F("Unit ID not set (EEPROM 0x00). Halting."));
  blinkUnprovisioned();
}

// ---------------------------------------------------------------------------
// Low-level parsing helpers
// ---------------------------------------------------------------------------

static uint8_t hexVal(char c, bool* ok) {
  if (c >= '0' && c <= '9') {
    return (uint8_t)(c - '0');
  }
  if (c >= 'a' && c <= 'f') {
    return (uint8_t)(c - 'a' + 10);
  }
  if (c >= 'A' && c <= 'F') {
    return (uint8_t)(c - 'A' + 10);
  }
  *ok = false;
  return 0;
}

// Parse two hex characters at p[0..1] into a byte. Sets *ok=false on failure.
static uint8_t parseHex2(const char* p, bool* ok) {
  uint8_t hi = hexVal(p[0], ok);
  uint8_t lo = hexVal(p[1], ok);
  return (uint8_t)((hi << 4) | lo);
}

// Parse a decimal integer from p[0..len-1], with an optional leading + or -.
// Returns the absolute value; sets *negative and *ok accordingly.
// Rejects empty strings, lone signs, values > 65535, and non-digit chars.
static uint32_t parseUInt(const char* p, uint8_t len, bool* negative, bool* ok) {
  *negative = false;
  if (len == 0) {
    *ok = false;
    return 0;
  }
  uint8_t i = 0;
  if (p[0] == '+') {
    i = 1;
  } else if (p[0] == '-') {
    *negative = true;
    i = 1;
  }
  if (i >= len) {
    *ok = false;
    return 0;
  }  // nothing after sign
  uint32_t val = 0;
  for (; i < len; i++) {
    char c = p[i];
    if (c < '0' || c > '9') {
      *ok = false;
      return 0;
    }
    val = val * 10u + (uint32_t)(c - '0');
    if (val > 65535u) {
      *ok = false;
      return 0;
    }
  }
  return val;
}

// ---------------------------------------------------------------------------
// Line parser — operates on s_buf[0..s_len-1]
// ---------------------------------------------------------------------------

static Command parseLine() {
  Command cmd = {CommandType::NONE, 0, false};

  // Minimum: 2 (hex ID) + 1 (command letter) = 3 characters.
  if (s_len < 3) {
    return cmd;
  }

  // Parse the 2-character hex unit address.
  bool ok = true;
  uint8_t id = parseHex2(s_buf, &ok);
  if (!ok) {
    return cmd;
  }

  // Drop the message early if it isn't addressed to this unit or broadcast.
  if (id != 0xFF && id != s_address) {
    return cmd;
  }

  // Parse the command letter (case-insensitive).
  char cmdChar = s_buf[2];
  if (cmdChar >= 'a' && cmdChar <= 'z') {
    cmdChar = (char)(cmdChar - 'a' + 'A');
  }

  const char* args = s_buf + 3;
  uint8_t argsLen = s_len - 3;

  switch (cmdChar) {
    case 'M': {
      // <DIR><ANGLE> — DIR must be explicit + or -, ANGLE is 1–65535.
      if (argsLen < 2) {
        return cmd;
      }
      if (args[0] != '+' && args[0] != '-') {
        return cmd;
      }
      bool negative = false;
      ok = true;
      uint32_t param = parseUInt(args, argsLen, &negative, &ok);
      if (!ok || param == 0 || param > 65535u) {
        return cmd;
      }
      cmd.type = CommandType::MOVE_INCREMENTAL;
      cmd.param = (uint16_t)param;
      cmd.direction = !negative;  // true = forward (+)
      break;
    }

    case 'A': {
      // <ANGLE> — no sign, 0–359.
      if (argsLen == 0) {
        return cmd;
      }
      if (args[0] == '+' || args[0] == '-') {
        return cmd;
      }
      bool negative = false;
      ok = true;
      uint32_t param = parseUInt(args, argsLen, &negative, &ok);
      if (!ok || param > 359u) {
        return cmd;
      }
      cmd.type = CommandType::MOVE_ABSOLUTE;
      cmd.param = (uint16_t)param;
      break;
    }

    case 'H': {
      if (argsLen != 0) {
        return cmd;  // H takes no arguments
      }
      cmd.type = CommandType::HOME;
      break;
    }

    case 'X': {
      if (argsLen != 0) {
        return cmd;  // X takes no arguments
      }
      cmd.type = CommandType::STOP;
      break;
    }

    case 'S': {
      // <TENTHS> — no sign, 10–300 (tenths of RPM; 10 = 1.0 RPM, 300 = 30.0 RPM).
      if (argsLen == 0) {
        return cmd;
      }
      if (args[0] == '+' || args[0] == '-') {
        return cmd;
      }
      bool negative = false;
      ok = true;
      uint32_t tenths = parseUInt(args, argsLen, &negative, &ok);
      if (!ok || tenths < 10u || tenths > 300u) {
        return cmd;
      }
      cmd.type = CommandType::SET_SPEED;
      cmd.param = (uint16_t)tenths;
      break;
    }

    case 'W': {
      // <ms> — no sign, 1–65535.
      if (argsLen == 0) {
        return cmd;
      }
      if (args[0] == '+' || args[0] == '-') {
        return cmd;
      }
      bool negative = false;
      ok = true;
      uint32_t ms = parseUInt(args, argsLen, &negative, &ok);
      if (!ok || ms < 1u || ms > 65535u) {
        return cmd;
      }
      cmd.type = CommandType::WAIT;
      cmd.param = (uint16_t)ms;
      break;
    }

    case 'G': {
      if (argsLen != 0) {
        return cmd;
      }
      cmd.type = CommandType::GO;
      break;
    }

    case 'C': {
      if (argsLen != 0) {
        return cmd;
      }
      cmd.type = CommandType::CLEAR;
      break;
    }

    case 'K': {
      // <+|-> only. No digits — step size is always exactly 1.
      if (argsLen != 1) {
        return cmd;
      }
      if (args[0] != '+' && args[0] != '-') {
        return cmd;
      }
      cmd.type = CommandType::CLOCK_NUDGE;
      cmd.param = 0;
      cmd.direction = (args[0] == '+');  // true = faster, false = slower
      break;
    }

    case 'P': {
      if (argsLen != 0) {
        return cmd;  // P takes no arguments
      }
      cmd.type = CommandType::POWER_DOWN;
      break;
    }

    case 'D': {
      if (argsLen != 0) {
        return cmd;  // D takes no arguments
      }
      cmd.type = CommandType::RELEASE;
      break;
    }

    case 'N': {
      if (argsLen != 0) {
        return cmd;  // N takes no arguments
      }
      cmd.type = CommandType::CONDITIONAL_HOME;
      break;
    }

    case 'I': {
      // Broadcast is never valid for identity reassignment — see the doc
      // comment above. The generic id!=0xFF||id==s_address filter above
      // already let FF through like any other command, so reject it here.
      if (id == 0xFF) {
        return cmd;
      }
      // <NEWID> — exactly 2 hex chars, 01-FE (00 and FF are reserved).
      if (argsLen != 2) {
        return cmd;
      }
      ok = true;
      uint8_t newId = parseHex2(args, &ok);
      if (!ok || newId == 0x00 || newId == 0xFF) {
        return cmd;
      }
      cmd.type = CommandType::SET_ID;
      cmd.param = newId;
      break;
    }

    case 'R': {
      // <n> — no sign, 0–65535. 0 = loop forever, 1 = no-op (single pass).
      if (argsLen == 0) {
        return cmd;
      }
      if (args[0] == '+' || args[0] == '-') {
        return cmd;
      }
      bool negative = false;
      ok = true;
      uint32_t n = parseUInt(args, argsLen, &negative, &ok);
      if (!ok) {
        return cmd;
      }
      cmd.type = CommandType::REPEAT;
      cmd.param = (uint16_t)n;
      break;
    }

    default:
      return cmd;  // unknown command letter
  }

  return cmd;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

Command commPoll() {
  while (Serial.available()) {
    char c = (char)Serial.read();

    if (c == '\r') {
      continue;  // tolerate CR in CRLF line endings
    }

    if (c == '\n') {
      s_buf[s_len] = '\0';

      Command cmd = {CommandType::NONE, 0, false};
      if (!s_overflow) {
        cmd = parseLine();
      }
      s_len = 0;
      s_overflow = false;
      if (cmd.type != CommandType::NONE) {
        return cmd;
      }
      continue;
    }

    // Accumulate into buffer; flag overflow but keep consuming until \n.
    if (s_len < (uint8_t)(sizeof(s_buf) - 1)) {
      s_buf[s_len++] = c;
    } else {
      s_overflow = true;
    }
  }

  return {CommandType::NONE, 0, false};
}
