# Hexagon unit firmware

Firmware for the ATtiny1616 that lives inside each hexagon. It drives one 28BYJ-48 stepper through a scotch yoke, homes against an optical endstop, and listens on a shared one-way UART bus for movement commands from the orchestrator.

There's no build guide here, just the reference. If you're bringing up a board from scratch, start at [`../README.md`](../README.md) for the order things need to happen in.

## Hardware assumptions

This firmware is written against the v2 hexagon PCB (`../../Schematic/hexagon/`), not a generic ATtiny1616 breakout. Pin map:

| Pin | Function |
| --- | --- |
| PC0-PC3 | Stepper driver inputs, through a TPL7407L to the 28BYJ-48 |
| PB0 | Optical endstop (active low) |
| PB1 | Status LED |
| PB2 / PB3 | UART TX / RX (bus) |
| PA0 | UPDI |
| PA6 | Oscillator calibration input, exposed on the programming header |

Pin ordering in the `Stepper28BYJ` constructor (`stepper.cpp`) is `in1, in3, in2, in4`, matched to the 28BYJ-48's coil layout for AccelStepper's HALF4WIRE mode. Changing it makes the motor buzz instead of turn.

The motor is a 12V 28BYJ-48 run at 4096 half-steps per output-shaft revolution (the gearbox is 63.68395:1 against 64 internal steps). Default speed is 14.5 RPM, chosen to minimize resonance at this gear ratio; heat in this motor is proportional to time energized regardless of speed, so a slower default doesn't save power, it just burns the same energy over more time.

There's no crystal. Each unit's internal oscillator is trimmed once at manufacturing (see [`../calibrate_oscillator/`](../calibrate_oscillator/)) and the trim value is read from USERROW at boot.

## Building

```bash
arduino-cli compile --profile ATtiny1616 hexagons.ino
```

`sketch.yaml` pins the board config: 10 MHz internal clock, BOD enabled at 2.9V, and AccelStepper 1.64.0 via megaTinyCore. The clock was dropped from 20 MHz to 10 MHz because the 20 MHz speed grade needs a VCC this board (bus-powered over a daisy-chained Schottky diode, no local regulation) can't reliably hold under motor load.

## Deploying

`tools/update-unit.sh <port>` is the one to run for a real unit: it checks the BOD/EESAVE fuses, fixes only what's wrong, uploads firmware, and confirms the EEPROM unit ID survived, all without a chip erase. `--loop` runs it repeatedly for flashing a batch. `tools/fuses-read.sh <port>` is the read-only audit if you just want current state. See the comments at the top of each script in `tools/` for the full flag list; they're kept accurate there rather than duplicated here.

`tools/set-unit-id.sh <port> --id <hex> --clock <hex>` sets the EEPROM unit ID and/or the USERROW oscillator trim directly over UPDI, no bus and no firmware upload involved. That's the tool for a freshly assembled board or one whose ID got corrupted badly enough that the bus `I` command can't target it.

Fuse targets: `fuse1 = 0x65` (BOD 2.9V, continuous) and `fuse5 = 0xC5` (EESAVE on, so EEPROM survives a chip erase). These only get written by `update-unit.sh` / `fuses-fix-bod.sh`, never by a plain `arduino-cli upload`, which is why the fuse check exists as a separate idempotent step instead of being folded silently into every flash.

## Boot sequence

1. Apply the USERROW oscillator trim to the clock (no NVM write yet).
2. Start the UART and print the trim value and unit ID to the serial console.
3. Read the unit ID from EEPROM address `0x00`. If it's `0x00` or `0xFF` (unprovisioned), halt forever blinking two long flashes, pause, repeat. There's no way back from this without UPDI access or the bus `I` command from a unit that already has some working address.
4. Wait a random 0-999ms, seeded off the unit ID, so a batch of units homing simultaneously don't all load the power rail at once.
5. Run the four-phase homing sequence against the endstop.
6. Commit any deferred clock calibration writes to NVM. This happens here rather than at boot because writing NVM in the first tens of milliseconds after power-on, before the supply has settled, can corrupt the write on this hardware, and BOD alone doesn't fully close that window.
7. Start the command runner.

If the unit's calibration is missing from both USERROW and its EEPROM backup, it blinks three short flashes at boot and then continues with the uncalibrated default trim rather than halting, since a heavily-mistimed UART is recoverable (send commands slower, or reflash) where a full halt isn't.

Once running, the status LED just mirrors the endstop: on when triggered, off when clear.

## Communication protocol

One-way UART bus, 115200 baud, 8N1. The orchestrator broadcasts; units never talk back. There's no ACK, no NACK, and no error response of any kind: a malformed or out-of-range message is silently dropped.

**Message format:** `<ID><CMD><ARGS>\r\n`

- `ID`: 2 hex characters. `01`-`FE` addresses one unit, `FF` broadcasts to all, `00` is reserved.
- `CMD`: one ASCII letter, case-insensitive.
- `ARGS`: command-specific, no spaces. `\n` is the real delimiter; a `\r` before it is tolerated but not required.

A unit ignores any line not addressed to it or to broadcast, and silently drops anything that fails to parse, including a line long enough to overflow its 16-byte receive buffer.

### Commands

| Cmd | Args | Staged? | Effect |
| --- | --- | --- | --- |
| `M` | `+`/`-` then 1-65535 | yes | Incremental move, that many degrees, in the given direction |
| `A` | 0-359 | yes | Absolute move to that angle via the shortest path |
| `H` | none | yes | Full home sequence; resets position to 0° |
| `N` | none | yes | Home only if needed (see below); otherwise a no-op |
| `S` | 10-300 | yes | Set speed for subsequent moves, in tenths of an RPM (`S145` = 14.5 RPM) |
| `W` | 1-65535 | yes | Pause that many milliseconds before the next staged command |
| `R` | 0-65535 | yes | Repeat everything staged after it, up to the next `G`, that many times (`0` = forever) |
| `D` | none | yes | Release coils in place, mid-sequence, no re-home needed |
| `G` | none | immediate | Execute the staged queue |
| `C` | none | immediate | Discard the queue; a move already in progress finishes |
| `X` | none | immediate | Stop dead, clear the queue; coils stay energized, no re-home needed |
| `K` | `+`/`-` | immediate | Nudge oscillator calibration by one OSCCAL step, persisted to USERROW |
| `P` | none | immediate | Power down: release coils, clear the queue, require a home before the next move |
| `I` | 2 hex chars (`01`-`FE`) | immediate | Reprovision this unit's address in EEPROM; rejects broadcast |

Commands not marked immediate go into a 64-slot FIFO queue and wait for `G`. Once running, a stageable command that arrives mid-batch queues behind the current one for the *next* `G`, which is how batches pipeline: stage the next sequence while the current one is still draining, then fire it the moment the current one finishes.

Typical choreography is to stage individual commands on several units, then broadcast `FFG` to fire them all in the same instant.

### Move merging

Adjacent staged move commands in the same direction get merged so the motor never stops between them: two `M+` moves in a row sum their degrees, and two `A` moves in a row collapse to just the final target (the motor travels straight through to it). Anything else staged between them (`S`, `W`, `H`, ...) breaks the chain. This is transparent, not something you need to account for when choreographing, but it's why the queue rarely fills up even with long incremental sequences.

### X vs C vs P vs D

These four all touch the motor's power state but differ in scope:

- **X** stops immediately, clears the queue, and leaves coils energized. The tracked position is corrected to wherever the motor actually stopped, so no re-home is needed.
- **C** clears only the staged queue. A move already running finishes on its own.
- **P** is the full power-down: stop, clear the queue, release coils, and require a home before anything moves again (`N` will trigger it; `A`/`M` will trigger it lazily on their next dispatch).
- **D** releases coils in place without touching the queue or position tracking. It's meant to be staged explicitly inside a sequence, e.g. `A180 D W500 A0 D W500 R0 G` releases at every stop instead of only after the idle timer.

An idle unit also releases its own coils automatically about 100ms after nothing left in its queue will start it moving without new bus traffic (a drained queue, a staged-but-not-yet-`G`'d batch, or a live `W` wait all count). That release doesn't require a re-home either, for the same reason `D` doesn't: the mechanical gearing is non-backdrivable and isn't fighting gravity, so it holds position through the coil release on its own.

### Position safeguard

Home is defined as the midpoint of the endstop's trigger window, so a unit whose tracked position is 0° should always read the endstop as triggered. If it doesn't, the firmware treats that as skipped steps or a hand-nudge and forces a re-home: immediately if idle and the mismatch persists past a short debounce, or (if a move is actively in progress) by flagging the next dispatched move to home first, since a home can't run concurrently with an active AccelStepper move. Either path is limited to one recovery attempt per visit to the home window, so a genuinely stuck sensor can't loop it forever. `P`'s "stay put" contract is exempt: it's the one case where sitting at a nonzero position after power-down is expected and not treated as drift.

## EEPROM / USERROW layout

| Location | Contents |
| --- | --- |
| EEPROM `0x00` | Unit ID (`0x01`-`0xFE`; `0x00`/`0xFF` = unprovisioned) |
| EEPROM `0x01` | Backup copy of the USERROW calibration byte |
| USERROW byte 0 | Oscillator calibration trim (OSCCAL, `0x00`-`0x3F`), written once at manufacturing, nudgeable ±1 via `K` |

There's no over-the-bus path to set the unit ID. That was removed deliberately: it was the only raw EEPROM write reachable from arbitrary serial input, and a stray two-character line from bus noise could have silently reassigned a unit. If a deployed unit's ID gets corrupted, fix it with the bus `I` command (which needs the unit's current, possibly-wrong ID to target it) or `tools/set-unit-id.sh` over UPDI (which doesn't).

## File map

| File | Purpose |
| --- | --- |
| `hexagons.ino` | Hardware init and boot sequence |
| `comm.h` / `comm.cpp` | UART I/O, message parsing, addressing |
| `runner.h` / `runner.cpp` | Staging/running state machine, queue dispatch, idle release, position safeguard |
| `queue.h` / `queue.cpp` | 64-slot circular buffer |
| `stepper.h` / `stepper.cpp` | AccelStepper wrapper: angle tracking, non-blocking moves, coil release |
| `clockcal.h` / `clockcal.cpp` | USERROW-based oscillator calibration |
| `homing.h` / `homing.cpp` | Four-phase optical endstop homing |
| `optical_switch.h` / `optical_switch.cpp` | Endstop pin wrapper |
| `tools/` | Deployment scripts (fuse audit/fix, firmware upload, unit ID/clock provisioning) |
