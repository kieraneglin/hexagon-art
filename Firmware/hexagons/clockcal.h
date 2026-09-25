#pragma once
#include <stdint.h>

// ---------------------------------------------------------------------------
// Clock Calibration — USERROW-based
// ---------------------------------------------------------------------------
//
// On boot, clockcalBegin() reads USERROW byte 0 (written by an external
// calibration tool) and applies it directly to CLKCTRL.OSC20MCALIBA so the
// RC oscillator runs at the correct frequency for reliable UART comms. This
// only touches the CLKCTRL SFR, not NVM, so it's safe to do immediately.
//
// The USERROW is written once by the calibration tool, and afterward kept
// in sync with a backup byte at EEPROM address 0x01 (see comm.cpp for the
// EEPROM 0x00 unit-ID slot this must not collide with). Some deployed units
// have been observed reading USERROW as 0xFF (unprogrammed) after a power
// cycle despite a verified original write — cause unconfirmed, possibly
// heat-related NVM retention. To recover from that: if USERROW is 0xFF,
// clockcalBegin() falls back to the EEPROM backup (a read, always safe) and
// remembers to restore it to USERROW. If the backup is also 0xFF (never
// established), it flashes the status LED 3 times and continues booting
// with the factory-default, uncalibrated oscillator trim rather than
// halting the unit.
//
// Any actual NVM write (restoring USERROW, syncing the EEPROM backup) is
// deferred to clockcalCommit() rather than performed in clockcalBegin().
// megaTinyCore's EEPROM library documents corruption from NVM writes
// attempted within the first tens of milliseconds of power-on, on a
// slow-rising supply without BOD enabled (EEPROM library README, "Warning:
// Using EEPROM right at startup", upstream issue #452) — this bit a real
// unit in the field (a unit ID at EEPROM 0x00 got corrupted after the
// then-current code wrote the calibration backup unconditionally at the top
// of setup()). Call clockcalCommit() once, later in setup() (after homing),
// once the supply has had real time to settle.
//
// CLKCTRL.OSC20MCALIBA bits [5:0] (CAL20M) trim the oscillator frequency
// (~0.5–1% per LSB, datasheet §32.4). Writes require a CCP unlock sequence.
//
// clockcalNudge() allows post-deployment fine-tuning via the K command. It's
// operator-triggered well after boot, so it's not subject to the same
// startup-voltage risk — it writes USERROW and the EEPROM backup directly.
// It adjusts OSCCAL by ±1 LSB, persists the new value, and applies it
// immediately. Clamps at 0x00 and 0x3F; silent no-op at either limit.
//
// ---------------------------------------------------------------------------

// Call once in setup(), BEFORE commBegin(), so the baud rate is correct from
// the first character received. Does not write to NVM — see clockcalCommit().
void clockcalBegin();

// Call once in setup(), well after clockcalBegin() (after homing is a good
// point) so the supply has had time to settle. Performs whatever NVM writes
// clockcalBegin() deferred: restoring USERROW if it was found blank, and
// syncing the EEPROM backup. No-op if nothing is pending.
void clockcalCommit();

// Call once in setup(), AFTER commBegin(), to print the USERROW calibration
// value to the serial console for diagnostics.
void clockcalPrintDiag();

// Adjust OSCCAL by +1 (nudgeUp=true) or -1 (nudgeUp=false).
// Writes the new value to USERROW byte 0 and the EEPROM backup, and applies
// it live. Clamps at 0x00 / 0x3F; silent no-op at limits.
void clockcalNudge(bool nudgeUp);
