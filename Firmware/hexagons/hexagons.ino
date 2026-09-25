#include "clockcal.h"
#include "comm.h"
#include "homing.h"
#include "optical_switch.h"
#include "runner.h"
#include "stepper.h"

Stepper28BYJ motor(PIN_PC0, PIN_PC1, PIN_PC2, PIN_PC3);
OpticalSwitch endstop(PIN_PB0);

static const uint8_t statusLedPin = PIN_PB1;

void setup() {
  delay(250);  // Ensure stability when testing
  clockcalBegin();
  commBegin(115200);
  clockcalPrintDiag();
  uint8_t addr = commReadAddressFromEEPROM();
  commSetAddress(addr);

  endstop.begin();

  motor.begin();
  motor.setRPM(14.5f);  // minimises resonance on 28BYJ-48 at this gear ratio

  randomSeed(addr);  // per-unit seed so the stagger below actually differs across units
  delay((uint16_t)(random(1000)));  // stagger motor startup load across units

  homeMotor(motor, endstop);
  motor.resetAngle();

  // Deferred until well after boot — see clockcal.h for why writing NVM
  // (USERROW/EEPROM) right at power-on risks corruption on this hardware.
  clockcalCommit();

  runnerBegin(motor, endstop);
}

void loop() {
  runnerUpdate();

  if (endstop.isTriggered()) {
    digitalWrite(statusLedPin, HIGH);
  } else {
    digitalWrite(statusLedPin, LOW);
  }
}
