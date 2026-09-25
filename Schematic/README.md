# Schematic

KiCad 10 projects for the two boards in the installation.

| Directory                               | Board                | What it is                                        |
| --------------------------------------- | -------------------- | ------------------------------------------------- |
| [`hexagon/`](hexagon/README.md)         | Hexagon Art v2       | Per-unit board: ATtiny1616, motor driver, endstop |
| [`distributor/`](distributor/README.md) | Distributor Plate v1 | Passive 1-to-3 power/bus splitter                 |

## How they connect

Power and the UART bus come from upstream (ultimately the orchestrator Pi, see [`../Firmware/orchestrator/`](../Firmware/orchestrator/README.md)) as +5V, +12V, GND, and the one-way RX bus line, daisy-chained JST XH connector to JST XH connector between hexagon boards. The distributor board sits between a screw-terminal source and up to three JST outputs, wired straight through with no active components, for branching the chain rather than running it strictly in series.

Every hexagon board's `J1` (in) and `J2` (out) share the same 6-pin JST XH pinout, so a chain is built by plugging `J2` of one unit into `J1` of the next:

| Pin | Signal   |
| --- | -------- |
| 1   | RX (bus) |
| 2   | spare    |
| 3   | spare    |
| 4   | +12V     |
| 5   | GND      |
| 6   | +5V      |

+12V feeds the motor driver only. +5V feeds the MCU and endstop, arriving through a series Schottky diode on each board (`D1`) so a board being reprogrammed in place can't back-power the rest of the chain through the local 5V rail. Neither board does any voltage regulation; both rails have to already be correct at the source.

## Files

Each board directory carries the KiCad project (`.kicad_pro`, `.kicad_sch`, `.kicad_pcb`), a `GRBRs.zip` of gerbers and drill files ready for a fab house, and its own BOM.
