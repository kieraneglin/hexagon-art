# Hexagon Art Piece

Because I'm incapable of saying no to ideas that sound fun

## What is it?

Many tightly packed wooden hexagons, each one driven linearly by its own stepper motor through a scotch yoke, creating a moving effect across the whole piece. A central orchestrator choreographs all of them together over a one-way UART bus.

```
Orchestrator (Raspberry Pi) --UART--> Distributor board --+--> Hexagon unit
                                                            +--> Hexagon unit
                                                            +--> ... (daisy-chained)
```

Each hexagon is a small standalone board: an ATtiny1616, a stepper driver, and an optical endstop for homing. The orchestrator broadcasts movement commands down the bus; units never talk back.

## Why is it?

A local art exhibit asked a communal woodshop that I am part of to create some installations. We were given no requirements other than being told that the theme is "art on the move". I took that extremely literally and this is what I came up with!

## Repository layout

- [`Firmware/`](Firmware/README.md): the per-unit firmware, the UPDI programmer/calibration jig, and the orchestrator software.
- [`Schematic/`](Schematic/README.md): KiCad projects for the hexagon board and the distributor board.
- `CAD/`: the enclosure and mechanism design (Fusion 360 source and a STEP export).

Each directory's own README has the actual detail; this file is just the map.

## About this repository

This is published as a reference, not a kit. There's no bill-of-materials-to-finished-piece build guide, and no support will be offered. If you're the kind of person who can pick up a KiCad file, a firmware protocol spec, and a CAD model and run with it, everything you need to understand how this works is in here.

## Design philosophy

Because this project is self-funded then donated (and because I bought enough for 80 units), the hexagon units are aggressively cost-optimized. This does introduce compromises in terms of my selected components or communication capabilities (eg: the one-way UART bus), but it's all about tradeoffs.

Besides, I don't expect anyone to actually build this themselves, but I wanted to put it out there in case any of the ideas or techniques might be useful in another context.

## License

Licensed under MIT; see [`LICENSE`](LICENSE). The UPDI programmer builds on [ElTangas/jtag2updi](https://github.com/ElTangas/jtag2updi) (also MIT).
