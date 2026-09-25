# Firmware

Four sub-projects to setup and run a hexagon board, plus the software that drives all of them at once.

| Directory                                                 | What it is                                                  | Runs on                              |
| --------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------ |
| [`hexagons/`](hexagons/README.md)                         | Per-unit firmware: motor control, homing, the UART protocol | ATtiny1616, one per hexagon          |
| [`programmer/`](programmer/README.md)                     | UPDI programmer + oscillator calibration jig                | Arduino Uno                          |
| [`calibrate_oscillator/`](calibrate_oscillator/README.md) | One-time manufacturing calibration sketch                   | ATtiny1616, temporarily, via the jig |
| [`orchestrator/`](orchestrator/README.md)                 | Choreography engine + web UI                                | Raspberry Pi Zero W                  |

## Bringing up a new board

1. Build and flash `programmer/` to an Uno, then disable its auto-reset (a capacitor across RESET/GND, or cut the trace). This jig is reusable across every unit you build.
2. With the jig in PROGRAM mode, flash `calibrate_oscillator/` to the target over UPDI.
3. Switch the jig to CALIBRATE mode. It feeds a 500 Hz reference into the target's PA6 pin (broken out on the hexagon board's J6 header) and the calibration sketch writes the result to USERROW. Watch the target's status LED for success/failure.
4. Set the unit's address with `hexagons/tools/set-unit-id.sh <port> --id <hex>`, still over the same UPDI connection.
5. Switch the jig back to PROGRAM and flash production firmware with `hexagons/tools/update-unit.sh <port>`, which also verifies the BOD fuse and confirms the ID survived.
6. Wire the unit onto the bus and confirm it responds. If you're standing up the whole installation rather than one unit, `orchestrator/server/patterns/layout.py` is where physical position gets mapped to unit ID.

`hexagons/tools/update-unit.sh --loop` runs steps 4-5 repeatedly for a batch, prompting between units.

## Toolchain

- [arduino-cli](https://arduino.github.io/arduino-cli/) with the [megaTinyCore](https://github.com/SpenceKonde/megaTinyCore) board package and the AccelStepper library, for `hexagons/` and `calibrate_oscillator/`.
- Standard Arduino IDE/CLI with the AVR core, for `programmer/` (it targets an Uno).
- Node.js and Python 3.10+, for `orchestrator/`'s frontend and server respectively.
