# Oscillator Calibration Sketch

Flashing-time calibration for hexagon unit oscillators.

## What It Does

Measures the ATtiny1616's internal 20 MHz RC oscillator against a 500 Hz
reference signal from an Arduino Uno and writes the optimal OSCCAL value
to USERROW byte 0, where it survives all subsequent chip erases and firmware
re-flashes.

## Hardware Setup

```
Arduino Uno GND  ────  ATtiny1616 GND   (SOIC-20 pin 10)
Arduino Uno Pin 9 ────  ATtiny1616 PA6   (SOIC-20 pin 4)
Arduino Uno 5V   ────  ATtiny1616 VDD   (SOIC-20 pin 1)  [optional]
```

The Uno must be running the 500HzReference sketch from megaTinyCore
(File → Examples → megaTinyCore → 500HzReference).

The ATtiny1616 can be powered from the Uno's 5V rail or a separate supply.
If separate, ensure common ground.

## Process (per unit)

1. **Prep the Uno** — upload 500HzReference once. Leave it running forever.

2. **Flash this sketch** to the ATtiny1616 via UPDI.
   - Board: ATtiny1616
   - Clock: 20 MHz Internal
   - millis(): Disabled

3. **Move board to calibration jig** — connect Uno GND, Pin 9, and optionally 5V.

4. **Watch the LED (PB1)**:
   - 3 slow blinks → calibration starting
   - Rapid flickering → scanning 64 OSCCAL values (~2 seconds)
   - Solid ON → SUCCESS — calibrated value written to USERROW
   - S-O-S pattern → FAILURE — check connections

5. **Remove from jig** — flash production firmware. Done.

## Compile Settings

- Board: ATtiny1616 (or 1606/3216)
- Clock: 20 MHz Internal
- millis()/micros(): Disabled (required — set in Tools menu)

## USERROW Layout

| Byte | Content |
|------|---------|
| 0 | Calibrated OSCCAL (0x00–0x3F) |
| 1–31 | Reserved |

## LED Patterns

| Pattern | Meaning |
|---------|---------|
| 3× slow blink (200ms) | Calibration starting |
| Rapid flicker | Scanning OSCCAL values |
| Solid on | Success |
| S-O-S (3 short, 3 long, 3 short) | Failure — no value within tolerance |

## References

- ATtiny1616 Datasheet §32.3.5 (User Row NVM access)
- megaTinyTuner by Spence Konde (megaTinyCore)
