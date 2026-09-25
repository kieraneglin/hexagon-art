# Hexagon board (v2)

Per-unit board: one ATtiny1616, a motor driver for the 28BYJ-48, and an optical endstop. Firmware lives at [`../../Firmware/hexagons/`](../../Firmware/hexagons/README.md), which documents the full pin map and communication protocol; this covers the hardware side only.

## Bill of materials

| Ref        | Qty | Value / part                      | Footprint                                           | MPN              |
| ---------- | --- | --------------------------------- | --------------------------------------------------- | ---------------- |
| U3         | 1   | ATtiny1616-MN                     | VQFN-20-1EP, 3x3mm, 0.4mm pitch                     | ATtiny1616-MN    |
| U2         | 1   | TPL7407L                          | TSSOP-16                                            | TPL7407LAQPWRQ1  |
| PI1        | 1   | Optical interrupter               | LiteOn LTH-301-07                                   | LTH-301-07       |
| D1         | 1   | Schottky diode                    | SOD-123W                                            | PMEG2010ER,115   |
| D2         | 1   | Status LED                        | 0805                                                | 5988110107F      |
| C2         | 1   | 10uF                              | 0805                                                | C0805C106K8PACTU |
| C1, C3, C5 | 3   | 100nF                             | 0805                                                | CL21B104KACNNNC  |
| C4         | 1   | 220uF                             | 6.3x7.7mm electrolytic                              | UCM1E221MCL1GS   |
| R4         | 1   | 4.7k                              | 0805                                                | RC0805FR-074K7L  |
| R2, R3, R6 | 3   | 220 ohm                           | 0805                                                | RC0805FR-07220RL |
| J1, J2     | 2   | JST XH, 6-pin, vertical           | Connector_JST:JST_XH_B6B-XH-A_1x06_P2.50mm_Vertical | (JST)            |
| J4         | 1   | JST XH, 5-pin, vertical (motor)   | Connector_JST:JST_XH_B5B-XH-A_1x05_P2.50mm_Vertical | (JST)            |
| J5         | 1   | 6-pin socket (GPIO breakout)      | PinSocket_1x06_P2.54mm_Vertical                     |                  |
| J6         | 1   | 8-pin socket (programming header) | PinSocket_2x04_P2.54mm_Vertical                     |                  |
| J7         | 1   | 2-pin header (spare passthrough)  | PinHeader_1x02_P2.54mm_Vertical                     |                  |

Not on this board: the 28BYJ-48 motor itself, which connects via `J4`.

`BOM.csv` in this directory is the raw KiCad-generated export; it collapses several unrelated connectors and the MCU into a single row with no part number, which is why the table above is organized per reference instead.

## Connectors

**J1 (in) / J2 (out)**, the daisy-chain, 6-pin JST XH, identical pinout on both:

| Pin | Signal   |
| --- | -------- |
| 1   | RX (bus) |
| 2   | spare    |
| 3   | spare    |
| 4   | +12V     |
| 5   | GND      |
| 6   | +5V      |

Pins 2-3 also passthrough to `J7`, a 2-pin header for a future sensor or accessory tap.

**J4**, motor (5-pin JST XH): pins 1-4 are the driver outputs to the 28BYJ-48's four coils, pin 5 is +12V.

**J5**, GPIO breakout (6-pin socket): PA1, PA2, PA3, PA7, PB4, PB5, unused by the current firmware.

**J6**, programming header (8-pin socket):

| Pin  | Signal                             |
| ---- | ---------------------------------- |
| 1, 2 | GND                                |
| 3, 4 | +5V                                |
| 5    | UPDI                               |
| 6    | TX                                 |
| 7    | PA6 (oscillator calibration input) |
| 8    | RX                                 |

This is what the programmer jig (`../../Firmware/programmer/`) connects to.

## MCU pin map

| Pin                          | Net                                       |
| ---------------------------- | ----------------------------------------- |
| PC0-PC3                      | Motor driver inputs (to `U2`)             |
| PB0                          | Endstop (`PI1`, active low)               |
| PB1                          | Status LED                                |
| PB2                          | UART TX                                   |
| PB3                          | UART RX                                   |
| PA0                          | UPDI                                      |
| PA6                          | Oscillator calibration input (`J6` pin 7) |
| PA1, PA2, PA3, PA7, PB4, PB5 | Broken out on `J5`, unused                |

## Power

+5V arrives through `D1` (series Schottky) before reaching the local rail, isolating the board's own supply from the daisy chain so that reprogramming one unit in place can't back-power its neighbors. +12V feeds `U2`'s COM pin and the motor directly, with no local regulation of either rail.

The `D1` diode is to prevent back-powering the local +5V rail from the daisy chain if you're reprogramming the board in place.

## Files

- `hexagon.kicad_pro` / `.kicad_sch` / `.kicad_pcb`: KiCad 10 project.
- `GRBRs.zip`: gerbers and drill files
- `BOM.csv`: raw KiCad BOM export.
