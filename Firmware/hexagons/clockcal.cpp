#include "clockcal.h"

#include <Arduino.h>
#include <EEPROM.h>
#include <avr/io.h>

static const uint8_t STATUS_LED_PIN = PIN_PB1;

// Backup copy of the USERROW calibration byte. Unit ID owns EEPROM 0x00
// (see comm.cpp) — this must not collide with it.
static const int EEPROM_ADDR_CLOCKCAL_BACKUP = 0x01;

// Set by clockcalBegin(), consumed by clockcalCommit(). See clockcalCommit()
// for why the actual NVM writes are deferred out of clockcalBegin().
static uint8_t s_pendingValue = 0;
static bool s_pendingRestore = false;  // USERROW was blank; needs writing back
static bool s_calibrated = false;      // false only when neither USERROW nor
                                        // the EEPROM backup had a value

// ---------------------------------------------------------------------------
// Clock Calibration — USERROW-based, with EEPROM backup
// ---------------------------------------------------------------------------

// Raw USERROW byte-0 write, shared by the boot-time recovery path and
// clockcalNudge(). USERROW is memory-mapped at 0x1300 on ATtiny1616.
// PAGEERASEWRITE atomically erases and rewrites the page in one SPM-unlocked
// command. Poll both FBUSY and EEBUSY for safety (matches calibration sketch).
static void writeUserrow(uint8_t value) {
  *(volatile uint8_t*)(0x1300) = value;
  _PROTECTED_WRITE_SPM(NVMCTRL.CTRLA, NVMCTRL_CMD_PAGEERASEWRITE_gc);
  while (NVMCTRL.STATUS & (NVMCTRL_FBUSY_bm | NVMCTRL_EEBUSY_bm)) {}
}

// One-shot 3-flash signal that calibration is missing everywhere (USERROW
// and EEPROM backup). Serial is not yet up, so the LED is the only option.
static void blinkUncalibrated() {
  pinMode(STATUS_LED_PIN, OUTPUT);
  for (uint8_t i = 0; i < 3; i++) {
    digitalWrite(STATUS_LED_PIN, HIGH);
    delay(100);
    digitalWrite(STATUS_LED_PIN, LOW);
    delay(150);
  }
}

void clockcalBegin() {
  uint8_t userrowCal = USERROW.USERROW0;

  if (userrowCal == 0xFF) {
    // USERROW has been observed losing its value in the field on power
    // cycling (cause unconfirmed, possibly heat-related NVM retention).
    // Fall back to the EEPROM backup rather than bricking the unit.
    // EEPROM.read() is safe here — reads don't touch NVM write circuitry.
    uint8_t backupCal = EEPROM.read(EEPROM_ADDR_CLOCKCAL_BACKUP);

    if (backupCal == 0xFF) {
      // No backup either — signal degraded boot, then continue with the
      // factory-default oscillator trim instead of halting.
      blinkUncalibrated();
      delay(1000);  // give the user a chance to see the LED signal
      return;
    }

    userrowCal = backupCal;
    s_pendingRestore = true;  // writeUserrow() deferred to clockcalCommit()
  }

  s_pendingValue = userrowCal;
  s_calibrated = true;

  // Apply the calibration value before Serial (commBegin) is initialised,
  // so the UART baud divisor is calculated against the corrected clock.
  // This only writes the CLKCTRL SFR, not NVM, so it's safe this early.
  _PROTECTED_WRITE(CLKCTRL_OSC20MCALIBA, userrowCal & 0x3F);
}

void clockcalCommit() {
  if (!s_calibrated) {
    return;
  }

  if (s_pendingRestore) {
    writeUserrow(s_pendingValue);
    s_pendingRestore = false;
  }

  // Keep the backup in sync so a future USERROW loss can recover from it.
  EEPROM.update(EEPROM_ADDR_CLOCKCAL_BACKUP, s_pendingValue);
}

void clockcalPrintDiag() {
  // Call this after commBegin() so Serial is ready.
  uint8_t userrowCal = USERROW.USERROW0;
  Serial.print(F("USERROW[0]: 0x"));
  if (userrowCal < 0x10) {
    Serial.print('0');
  }
  Serial.println(userrowCal, HEX);
}

void clockcalNudge(bool nudgeUp) {
  uint8_t current = USERROW.USERROW0 & 0x3F;

  // Clamp: silent no-op at limits.
  if (nudgeUp && current >= 0x3F) {
    return;
  }
  if (!nudgeUp && current == 0x00) {
    return;
  }

  uint8_t next = nudgeUp ? (current + 1u) : (current - 1u);

  writeUserrow(next);
  EEPROM.update(EEPROM_ADDR_CLOCKCAL_BACKUP, next);

  // Apply immediately so the change takes effect without a reset.
  _PROTECTED_WRITE(CLKCTRL_OSC20MCALIBA, next);
}
