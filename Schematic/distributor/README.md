# Distributor plate (v1)

A passive breakout board: two lever-terminal inputs, three JST XH outputs, all six conductors (RX bus, two spares, +12V, GND, +5V) paralleled straight across. No active components, no regulation, nothing to configure. Its job is purely to let a daisy chain branch into more than one direction from a single upstream feed, since the hexagon boards themselves only have one input and one output connector each (see [`../hexagon/README.md`](../hexagon/README.md) for that pinout, which this board mirrors exactly).

## Important note

This board cannot route enough current to power dozens of boards. If you need to do that, you should either solder thicker wires to the board or redesign this PCB.

## Bill of materials

| Ref        | Qty | Part                      | Footprint                                             | MPN             |
| ---------- | --- | ------------------------- | ----------------------------------------------------- | --------------- |
| J1, J2, J5 | 3   | JST XH, 6-pin, horizontal | Connector_JST:JST_XH_S6B-XH-A_1x06_P2.50mm_Horizontal |                 |
| J3, J4     | 2   | Screw terminal, 6-pin     | Connector_Pin:QT06                                    | QT06BDP83H0000G |

Four M3 mounting holes round out the board.

## Files

- `distributor.kicad_pro` / `.kicad_sch` / `.kicad_pcb`: KiCad 10 project.
- `GRBRs.zip`: gerbers and drill files
