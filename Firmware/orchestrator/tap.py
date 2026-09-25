# tap.py — In-memory command tap for the simulator.
#
# orchestrator.send() is the single egress point for every outbound command
# in the system (terminal passthrough, patterns, macros). Recording here
# gives the simulator a complete, order-preserving view of the bus without
# touching any caller.
#
# Root-level (not server/tap.py) because server/__init__ imports
# server/routes, which imports orchestrator, which would need to import
# server.tap — a cycle. orchestrator.py importing tap.py directly is clean.
#
# Kept intentionally dependency-free (stdlib only, no orchestrator/server
# imports) — a leftover discipline from when this had to work unmodified
# under CircuitPython too, kept now because it's still just good hygiene
# for a module every request path touches.
#
# Storage: an append-only "<seq>,<t_ms>,<cmd>\n" string, trimmed by byte
# size once it exceeds _MAX_BYTES (see boot_log.py for the same
# rotate-by-size strategy, same rationale). A pattern's whole command
# burst (cascade at extreme params emits ~1300 commands across 61 units)
# is written from a single request-handling thread with no yield point in
# between — so even though the server is multi-threaded now (Waitress),
# one burst still lands as one uninterrupted run from the tap's point of
# view, and the simulator's 400ms poll interval can still land entirely
# inside one. The buffer must hold an entire realistic burst, not just the
# last N commands. orchestrator.py's _send_lock serializes access from
# concurrent requests, so record()'s "<seq>,<t_ms>,<cmd>" bookkeeping below
# never sees interleaved writers.

import time

_MAX_BYTES = 40960  # 40 KB — trigger threshold
_KEEP_BYTES = 20480  # 20 KB — how much to retain after trimming

_buf = ""  # "<seq>,<t_ms>,<cmd>\n" per line, oldest first
_next_seq = 0
_oldest_seq = 0  # seq of the first line still held, after trimming

# Changes once per process start. Lets a client notice "the device
# rebooted" (or the host harness restarted) and reset its local model
# instead of misinterpreting a seq counter that restarted at 0.
#
# time.time() rather than time.monotonic(): on Linux, monotonic() is
# uptime-since-boot of the *OS*, not the process, so two service restarts
# within the same second (systemd Restart=always) would collide on the
# same BOOT_ID and a client could miss a restart. Wall-clock time doesn't
# have that ambiguity, and unlike on the ESP32, NTP makes it available.
BOOT_ID = int(time.time() * 1000) & 0xFFFFFFFF


def _now_ms():
    return int(time.monotonic() * 1000)


def _trim_if_needed():
    """Drop the oldest lines once _buf exceeds _MAX_BYTES, down to _KEEP_BYTES."""
    global _buf, _oldest_seq
    if len(_buf) <= _MAX_BYTES:
        return
    cut = len(_buf) - _KEEP_BYTES
    newline = _buf.find("\n", cut)
    if newline == -1:
        # Pathological: no newline past the cut point (shouldn't happen
        # given line lengths vs. _KEEP_BYTES) — drop everything.
        _buf = ""
        return
    dropped = _buf[: newline + 1]
    _buf = _buf[newline + 1 :]
    _oldest_seq += dropped.count("\n")


def current_seq():
    """Return the seq that will be assigned to the *next* recorded command.

    Used as a watermark: capture this before doing something, then pass
    ``current_seq() - 1`` to tail() afterwards to get only what happened
    in between. See simhost/selftest.py for the intended usage.
    """
    return _next_seq


def record(cmd):
    """Append *cmd* (the raw command string, post-formatting) to the tap."""
    global _buf, _next_seq
    _buf += f"{_next_seq},{_now_ms()},{cmd}\n"
    _next_seq += 1
    _trim_if_needed()


def tail(since=-1):
    """Return every recorded command with seq > *since*, oldest first.

    ``dropped`` is true when *since* is older than the oldest line still
    held — the client missed commands to trimming and should reset its
    local model rather than replay a gap. Commands still returned are
    whatever the buffer currently has.
    """
    if _next_seq == 0:
        return {"boot_id": BOOT_ID, "seq": 0, "now_ms": _now_ms(), "dropped": False, "commands": []}

    dropped = since >= 0 and since + 1 < _oldest_seq
    skip = max(0, since + 1 - _oldest_seq)

    lines = _buf.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # trailing empty element from the final \n

    commands = []
    for line in lines[skip:]:
        seq_str, t_str, cmd = line.split(",", 2)
        commands.append([int(seq_str), int(t_str), cmd])

    return {
        "boot_id": BOOT_ID,
        "seq": _next_seq - 1,
        "now_ms": _now_ms(),
        "dropped": dropped,
        "commands": commands,
    }
