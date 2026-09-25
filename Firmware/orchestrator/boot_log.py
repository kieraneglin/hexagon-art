# boot_log.py — File logger with timestamps and size-based rotation.
#
# Formerly lib/httpserver/log.py — moved to a top-level module when the
# DIY HTTP server (lib/httpserver/) was replaced by Flask, since this
# logger was never really part of the HTTP layer, just something main.py
# used to mark boot events. Flask/Waitress do their own request logging
# (to stdout, captured by journald under systemd); this file is for the
# boot-divider marker and any other ad-hoc log() calls.
#
# Usage:
#     from boot_log import log, boot_divider
#     boot_divider()          # call once at startup
#     log("something happened")
#
# Rotation strategy:
#     On each write, os.stat() checks the file size. If it exceeds
#     _MAX_BYTES, the oldest _TRIM_BYTES worth of bytes are discarded
#     (the file is read, the first _TRIM_BYTES stripped, then rewritten).
#     This keeps flash wear proportional to log volume rather than
#     incurring a full rewrite on every single write.
#
# Timestamps:
#     time.monotonic() gives seconds-since-boot (float) of THIS PROCESS.
#     Kept as relative [+Xs] timestamps (unambiguous, useful for measuring
#     request latency) rather than switching to wall-clock — journald
#     already timestamps every line for anyone reading via `journalctl`.

import os
import time

# HEXAGON_FS_ROOT lets simhost/ redirect flash paths under a sandbox dir
# instead of the install directory. See main.py for where it's set.
_LOG_PATH = (os.getenv("HEXAGON_FS_ROOT") or "") + "/log.txt"

# Rotate when the file exceeds this size; keep the newest _KEEP_BYTES.
_MAX_BYTES = 32768  # 32 KB — trigger threshold
_KEEP_BYTES = 16384  # 16 KB — how much to retain after trimming


def _ts():
    """Return a compact timestamp string: [+123.4s]"""
    return f"[+{time.monotonic():.1f}s]"


def _rotate_if_needed():
    """Trim the log file if it exceeds _MAX_BYTES."""
    try:
        size = os.stat(_LOG_PATH).st_size
    except OSError:
        return  # file doesn't exist yet — nothing to do

    if size <= _MAX_BYTES:
        return

    try:
        with open(_LOG_PATH, "rb") as f:
            data = f.read()
        # Drop the oldest bytes, keeping the last _KEEP_BYTES.
        # Advance past the first partial line so we don't start mid-entry.
        keep = data[-_KEEP_BYTES:]
        newline = keep.find(b"\n")
        if newline != -1:
            keep = keep[newline + 1 :]
        with open(_LOG_PATH, "wb") as f:
            f.write(b"[log trimmed]\n")
            f.write(keep)
    except Exception:
        pass


def log(message):
    """Append a timestamped *message* to log.txt, rotating if needed.

    Silently swallows all errors so a logging failure never crashes
    the server.
    """
    try:
        _rotate_if_needed()
        with open(_LOG_PATH, "a") as f:
            f.write(f"{_ts()} {message}\n")
    except Exception:
        pass


def boot_divider():
    """Write a visible boot marker to the log.

    Call once at startup so restarts are clearly visible in the log.
    """
    try:
        _rotate_if_needed()
        with open(_LOG_PATH, "a") as f:
            f.write(f"\n{'─' * 40}\n")
            f.write(f"BOOT  {_ts()}\n")
            f.write(f"{'─' * 40}\n")
    except Exception:
        pass
