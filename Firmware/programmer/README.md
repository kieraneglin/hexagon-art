# Hexagon programmer

A single Arduino Uno sketch that doubles as a UPDI programmer and an oscillator calibration jig for the hexagon boards. It's built on [ElTangas/jtag2updi](https://github.com/ElTangas/jtag2updi) (JTAG ICE mkII to UPDI bridge, MIT licensed), with a mode-switching layer added on top so one Uno can do both jobs without rewiring.

Two modes, toggled by a push button:

| Mode | What it does | Timer1 owner |
| --- | --- | --- |
| PROGRAM (boot default) | avrdude talks JTAG ICE mkII over the host UART; UPDI goes out pin 6 | jtag2updi (host/target timeouts) |
| CALIBRATE | 500 Hz reference square wave on pin 9; host UART becomes a transparent passthrough to the target's UART at 115200 | 500 Hz CTC toggle |

The two modes need separate ownership of Timer1 because jtag2updi's `startTimer()` reconfigures it every time it waits for a host byte, which would otherwise stomp on the CTC toggle setup needed for the 500 Hz output. Mode LED (pin 5) is solid in PROGRAM and blinks at roughly 1 Hz in CALIBRATE.

## Why this exists

The hexagon boards have no crystal (see [`../calibrate_oscillator/README.md`](../calibrate_oscillator/README.md) for why). Calibrating the internal oscillator needs a known-frequency reference signal on the target while it's also connected to something that can read its console output, and separately, flashing firmware needs UPDI. Building both into one jig means a single Uno and a single set of wires cover a unit end to end: flash the calibration sketch, flip a switch, calibrate, flip it back, flash production firmware.

## Pinout

| Uno pin | Function | Notes |
| --- | --- | --- |
| 0 (RX) / 1 (TX) | Host UART | avrdude in PROGRAM mode; passthrough to target UART in CALIBRATE mode |
| 6 (PD6) | UPDI data, through a 4.7k series resistor | Recommended, not strictly required under 15cm |
| 9 (PB1, OC1A) | 500 Hz reference | CALIBRATE mode only, hardware Timer1, zero CPU overhead |
| 4 (PD4) | Mode button | Active low, internal pull-up, debounced 50ms |
| 3 (PD3) | Soft ground for the button | Wire button between pins 3 and 4, no external ground needed |
| 5 (PD5) | Mode LED | Through a resistor to pin 7 |
| 7 (PD7) | Soft ground for the LED | |

Wiring to the target (via the hexagon board's J6 programming header):

```
Programmer (Uno)          Hexagon board (J6 header)
Pin 6  (PD6)   ---4k7--->  UPDI
Pin 9  (PB1)   ---------->  CLK_PROG (PA6, calibration input)
Pin 0  (RX)    <----------  TX
Pin 1  (TX)    ---------->  RX
GND            ---------->  GND
5V             ---------->  5V (optional, can power the target from the Uno)
```

## Setup

1. **Disable auto-reset on the Uno.** avrdude opening the serial port normally resets the Uno, which kills jtag2updi mid-session. Add a 10uF capacitor between RESET and GND, or cut the RESET-EN trace. See [the classic writeup](https://playground.arduino.cc/Main/DisablingAutoResetOnSerialConnection).
2. **Flash this sketch** to the Uno with auto-reset still enabled for that one upload (board: Arduino Uno, upload normally). Disable auto-reset afterward; the jig is then permanent.
3. **Get an avrdude config with jtag2updi support**, either from the upstream repo or bundled with the Arduino "Nano Every" board package.

## Using it

Boot state is PROGRAM. To flash a hexagon unit:

```bash
avrdude -c jtag2updi -P <port> -p t1616 -U flash:w:hexagons.ino.hex:i
```

The project's `../hexagons/tools/update-unit.sh` and friends wrap this with fuse handling and EEPROM safety checks; use those instead of raw avrdude for anything beyond a one-off manual flash.

To calibrate a unit's oscillator, press the mode button to enter CALIBRATE, flash `../calibrate_oscillator/` to the target over the same UPDI connection, then open a terminal at 115200 baud (host UART auto-switches to that rate on entering CALIBRATE, and back to 19200 for avrdude on returning to PROGRAM). The target's console output passes through transparently, with zero CPU involvement on the Uno's side.

The target's UART should be silent during PROGRAM mode. Unexpected traffic on pin 0 while avrdude is talking to jtag2updi would corrupt the JTAG ICE stream.

## Files

| File | Purpose |
| --- | --- |
| `programmer.ino` | Arduino IDE entry point (comment only, no code) |
| `programmer.cpp` | `main()`, patched to call into the additions below |
| `additions.h` / `additions.cpp` | Mode switching, button/LED, 500 Hz generator, target UART passthrough |
| `sys.h` | jtag2updi configuration (UPDI pin, LED, timeouts) |
| `JTAG2.cpp` / `JTAG2.h` | JTAG ICE mkII protocol |
| `JICE_io.cpp` / `JICE_io.h` | Host UART layer |
| `UPDI_lo_lvl.cpp` / `UPDI_hi_lvl.cpp` | UPDI protocol, low and high level |
| `updi_io.cpp` / `updi_io_soft.cpp` | UPDI pin I/O, hardware and bit-banged |
| `crc16.cpp` | CRC-16 for the JTAG protocol |
| `dbg.cpp` / `dbg.h` | Debug output |
| `NVM.h` / `NVM_v2.h`, `parts.h` | NVM controller and part register definitions |
| `sys.cpp` | System init and timeout handling |

Everything except `programmer.cpp` and `additions.*` is unmodified upstream jtag2updi.

## Do not change

- UPDI pin (PD6): the bit-bang routines are tuned to it.
- 500 Hz frequency: the calibration sketch expects exactly that.
- Target UART baud (115200): must match the hexagon firmware.
- Button pin (PD4) and LED pin (PD5): hardcoded in `additions.cpp`.
