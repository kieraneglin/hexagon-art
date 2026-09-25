# Oscillator calibration sketch

One-time, per-unit calibration for the hexagon board's internal oscillator. Flash it once during manufacturing, calibrate, then flash production firmware over the same UPDI connection.

## Why

The hexagon board has no crystal (no 32.768kHz footprint is populated; cost). The ATtiny1616's internal 20 MHz RC oscillator varies unit to unit by roughly 1-2% out of the factory, which is fine on its own but accumulates into visible timing drift between units during choreographed movement over thousands of stepper half-steps. So each unit gets calibrated once against an external reference, and the result is written to USERROW, which survives chip erase and every subsequent firmware reflash.

## How it works

This uses the same measurement technique as Spence Konde's megaTinyTuner (part of megaTinyCore), simplified for a single target:

1. A 500 Hz square wave reference is fed into PA6.
2. The chip's clock runs at /4 prescale during the sweep so it stays stable across all 64 possible OSCCAL values, including ones that would otherwise push the oscillator above 30 MHz.
3. At each OSCCAL value, `pulseIn()` measures 8 consecutive low pulses. At exactly 20.000 MHz with /4 prescale, `pulseIn()`'s own timing (calculated against a compile-time 20 MHz `F_CPU`, while the CPU is actually running at 5 MHz) reports each 1000us low period as roughly 250us, so 8 pulses sum to about 2000us.
4. Whichever OSCCAL value's pulse sum lands closest to that 2000us target, within +/-38us, gets written to USERROW byte 0.

USERROW byte 0 is what `../hexagons/clockcal.cpp` reads at every boot, with EEPROM address 0x01 kept as a backup copy for units observed losing their USERROW value on power cycling in the field (cause unconfirmed, possibly heat-related NVM retention).

## Reference signal source

Either works:

- **The hexagon programmer jig** (`../programmer/`) in CALIBRATE mode, which puts 500 Hz on its pin 9. This is the practical option once a jig exists, since it's the same hardware that flashes the firmware.
- **An Arduino Uno running megaTinyCore's bundled `500HzReference` example** (File > Examples > megaTinyCore > 500HzReference), if you don't have the jig built yet.

Wiring is the same either way: reference source's GND and 500 Hz output pin to the target's GND and PA6, plus optionally 5V if you want the target powered from the reference source rather than separately. On a populated hexagon board, GND and PA6 are already broken out on the J6 programming header, so there's nothing extra to solder.

## Process, per unit

1. Flash this sketch to the ATtiny1616 over UPDI. Board settings: ATtiny1616, clock 20 MHz internal, millis()/micros() disabled (required, the sketch owns the timer directly; there's a compile-time `#warning` and a fallback that prevents millis() from starting even if the menu setting is wrong).
2. Connect the 500 Hz reference and watch the status LED (PB1):
   - 3 slow blinks: starting.
   - Rapid flicker for about 2 seconds: sweeping all 64 OSCCAL values.
   - Solid on: success, value written and verified in USERROW.
   - N long blinks followed by an SOS pattern: failure. 1 blink means no reference signal was detected at all (check wiring, check the reference source is actually running). 2 blinks means a signal was seen but no OSCCAL value landed within tolerance. 3 blinks means the USERROW write didn't verify (NVM hardware problem).
3. Flash production firmware over the same UPDI connection. Done.

## Compile settings

- Board: ATtiny1616 (1606/3216 share the same oscillator block)
- Clock: 20 MHz internal
- millis()/micros(): disabled

Calibrating at 20 MHz internal is valid even though the production firmware runs at 10 MHz internal: megaTinyCore's board definitions use the identical OSC20M calibration source for both clock options, just with a different post-scaler, so the calibrated trim value applies unchanged at either speed.

## USERROW layout

| Byte | Contents |
| --- | --- |
| 0 | Calibrated OSCCAL (`0x00`-`0x3F`) |
| 1-31 | Reserved |

## References

- ATtiny1616 datasheet, section 32.3.5 (User Row NVM access)
- [megaTinyTuner](https://github.com/SpenceKonde/megaTinyCore/blob/master/megaavr/libraries/megaTinyCore/examples/megaTinyTuner/megaTinyTuner.ino), Spence Konde / megaTinyCore
