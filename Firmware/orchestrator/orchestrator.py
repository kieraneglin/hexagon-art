# Hexagon Art Piece — Orchestrator UART module
#
# Import this at the REPL after boot:  import orchestrator as oc
# Then call any API function, e.g.:
#   oc.speed(0x01, 14.5)        # stage 14.5 RPM speed for unit 01
#   oc.move(0x01, 360)          # stage 360° CW move for unit 01
#   oc.go(0x01)                 # execute unit 01's queue
#   oc.go_all()                 # execute all units simultaneously (FFG)
#   oc.send("01M+90")           # raw command fallback
#
# Unit IDs:
#   0x01 – 0xFE  individual units
#   0xFF         broadcast (also available as BROADCAST constant)

import os
import threading

import serial

import tap

# Serial device for the hexagon bus. Override with HEXAGON_UART; the special
# value "none" (used by simhost — no real bus to write to) forces the null
# backend below without ever touching hardware.
_PORT = os.getenv("HEXAGON_UART", "/dev/serial0")

_uart = None
_uart_error = None  # last open/write failure, surfaced by GET /api/health

# Waitress (the WSGI server main.py runs under) serves requests from a
# small thread pool — unlike the old single-threaded DIY server, two
# requests really can call send() at once. This guards the one shared
# resource that matters: the UART itself and tap.py's append-only buffer,
# whose "<seq>,<t_ms>,<cmd>" bookkeeping isn't safe under concurrent
# writers. Held for one command at a time (not per-request), so e.g.
# POST /api/patterns/stop can still cut in between a burst's individual
# commands rather than queuing behind the whole burst.
_send_lock = threading.Lock()


class _NullUART:
    """Discards writes. Used for HEXAGON_UART=none and as a fallback when
    the real port can't be opened, so the web UI still boots and can be
    inspected/fixed instead of every request 500ing."""

    def write(self, data):
        return len(data)


def _get_uart():
    """Lazy-init so a missing/busy port doesn't crash the whole server at
    import — the failure is instead captured in _uart_error and surfaced
    via uart_status() / GET /api/health."""
    global _uart, _uart_error
    if _uart is not None:
        return _uart

    if _PORT == "none":
        _uart = _NullUART()
        return _uart

    try:
        # write_timeout=None (blocking) is deliberate: a burst like cascade
        # at extreme params emits ~1300 commands (~10 KB, ~0.9s of wire
        # time at 115200 baud) — far more than the kernel tty buffer holds.
        # A finite write timeout would raise mid-burst and silently drop
        # commands onto a bus that can never report the loss. No .flush()
        # per command either — that's tcdrain(), a per-byte stall; the
        # kernel preserves write ordering on its own.
        _uart = serial.Serial(_PORT, 115200, write_timeout=None)
        _uart_error = None
    except serial.SerialException as exc:
        _uart_error = str(exc)
        _uart = _NullUART()

    return _uart


def uart_status() -> tuple:
    """Return (port, ok, error) — used by GET /api/health.

    Touches _get_uart() so a not-yet-attempted open is actually tried
    rather than reporting a misleadingly optimistic "ok" before first use.
    """
    _get_uart()
    return (_PORT, _uart_error is None, _uart_error)


# Broadcast address — pass to any function to target all units at once.
BROADCAST = 0xFF


def _id(unit_id: int) -> str:
    """Format a unit ID as a 2-char uppercase hex string (e.g. 0x01 → '01')."""
    return f"{unit_id & 0xFF:02X}"


# ---------------------------------------------------------------------------
# Raw send
# ---------------------------------------------------------------------------


def send(cmd: str) -> None:
    """Write a raw command string to the bus. Appends \\r\\n terminator.

    Also records the command to the in-memory tap (tap.py) for consumption
    by the simulator — see /api/sim/tail. Locked so wire order always
    matches tap order even under concurrent requests (see _send_lock).
    """
    with _send_lock:
        tap.record(cmd)
        _get_uart().write(cmd.encode() + b"\r\n")


# ---------------------------------------------------------------------------
# Staged commands — queued on the unit, executed when go() is called
# ---------------------------------------------------------------------------


def move(unit_id: int, degrees: int) -> None:
    """Stage an incremental move of |degrees| (1–65535).
    Positive = CW/forward, negative = CCW/reverse.
    Protocol: <ID>M<+|-><angle>
    """
    sign = "+" if degrees >= 0 else "-"
    send(f"{_id(unit_id)}M{sign}{abs(degrees)}")


def absolute(unit_id: int, angle: int) -> None:
    """Stage an absolute move to angle 0–359° via the shortest path.
    0° = home position.
    Protocol: <ID>A<angle>
    """
    send(f"{_id(unit_id)}A{angle % 360}")


def home(unit_id: int) -> None:
    """Stage a full homing sequence; resets position to 0°.
    Protocol: <ID>H
    """
    send(f"{_id(unit_id)}H")


def conditional_home(unit_id: int) -> None:
    """Stage a conditional home: homes only if needed since the unit's last
    power_down() with no successful home since (see power_down()). A no-op
    that drains instantly otherwise — safe to stage in every batch without
    paying full homing latency on units that don't need it.
    Protocol: <ID>N
    """
    send(f"{_id(unit_id)}N")


def repeat(unit_id: int, count: int) -> None:
    """Stage a batch repeat at the head of the current batch: everything
    staged after it (up to the next go()'s fencepost) runs count times
    before the unit returns to staging. count=0 loops forever until
    stop()/clear()/power_down()/go(). count=1 is a no-op.
    Protocol: <ID>R<count>
    """
    send(f"{_id(unit_id)}R{count}")


def speed(unit_id: int, rpm: float) -> None:
    """Stage a speed change (1.0–30.0 RPM). Applies to subsequent moves.
    The wire protocol carries tenths of an RPM (e.g. 14.5 RPM -> S145), so
    the value passed here is multiplied by 10, rounded, and clamped to the
    firmware's valid range (10-300 tenths) before sending.
    Protocol: <ID>S<tenths>
    """
    tenths = max(10, min(300, round(rpm * 10)))
    send(f"{_id(unit_id)}S{tenths}")


def wait_ms(unit_id: int, ms: int) -> None:
    """Stage a pause of ms milliseconds before the next queued command.
    The wire protocol caps a single W at 65535ms; a longer wait is
    automatically split into consecutive W commands so the total delay
    is preserved exactly instead of being silently dropped by the
    firmware (an out-of-range W fails validation and is discarded).
    cascade's unit_delay_ms x up to 61 units can exceed 65535 for outer
    units at high settings — this is what surfaced the need for it.
    Protocol: <ID>W<ms> (chained as needed)
    """
    remaining = int(ms)
    while remaining > 0:
        chunk = min(remaining, 65535)
        send(f"{_id(unit_id)}W{chunk}")
        remaining -= chunk


# ---------------------------------------------------------------------------
# Immediate commands — execute right away, not queued
# ---------------------------------------------------------------------------


def go(unit_id: int) -> None:
    """Execute the unit's staged command queue immediately.
    Use go_all() / go(BROADCAST) to fire all units simultaneously.
    Protocol: <ID>G
    """
    send(f"{_id(unit_id)}G")


def clear(unit_id: int) -> None:
    """Clear the unit's staged queue without stopping an in-progress move.
    Protocol: <ID>C
    """
    send(f"{_id(unit_id)}C")


def stop(unit_id: int) -> None:
    """Emergency stop: immediately halt the motor and clear the queue. The
    firmware corrects the tracked angle to wherever the motor actually
    stopped, so no re-home is needed afterward — unlike power_down().
    Protocol: <ID>X
    """
    send(f"{_id(unit_id)}X")


def power_down(unit_id: int) -> None:
    """Immediately halt the motor, discard the queue, and release the coils
    (de-energised — no holding torque, no current draw). Marks the unit as
    needing a home before its next move: the next move()/absolute() homes
    implicitly first, or stage conditional_home() to home at a controlled
    point instead.
    Protocol: <ID>P
    """
    send(f"{_id(unit_id)}P")


# ---------------------------------------------------------------------------
# Broadcast convenience helpers
# ---------------------------------------------------------------------------


def go_all() -> None:
    """Fire all units simultaneously (FFG) — primary choreography primitive."""
    send("FFG")


def stop_all() -> None:
    """Emergency stop all units (FFX)."""
    send("FFX")


def clear_all() -> None:
    """Clear the queue on all units without stopping in-progress moves (FFC)."""
    send("FFC")


def home_all() -> None:
    """Broadcast a home command to all units (FFH)."""
    send("FFH")


def conditional_home_all() -> None:
    """Broadcast a conditional home to all units, individually (FFN) — each
    unit homes only if it needs to. See conditional_home().
    """
    send("FFN")


def power_down_all() -> None:
    """Power down all units together (FFP)."""
    send("FFP")
