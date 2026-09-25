#!/usr/bin/env python3
# Hexagon Art Piece — Orchestrator entry point (Raspberry Pi / CPython)
#
# Networking (WiFi, static IP, mDNS) is the OS's job now — NetworkManager
# and Avahi handle it, see PI_SETUP.md for the one-time Pi setup. nginx owns
# port 80 and reverse-proxies /api/* here (see deploy/nginx.hexagons.conf)
# — this process binds to localhost only and is never reached directly
# from the network. This file's only job is to point flash-path writes at
# the install directory and start the Flask app under Waitress. Run
# directly, or via systemd (see deploy/hexagons.service).
#
# Usage:
#   python3 main.py                # serve on 127.0.0.1:8080 (nginx-only)
#   python3 main.py --host 0.0.0.0 --port 9090
#   HEXAGON_UART=/dev/ttyUSB0 python3 main.py

import argparse
import os
import threading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("HEXAGON_PORT", 8080)))
    args = parser.parse_args()

    # HEXAGON_FS_ROOT is where flash-path writes (dist/, macros.json, log.txt)
    # resolve under. Defaulting it to this file's own directory means
    # dist/ and macros.json live next to main.py with zero systemd
    # configuration, and the install directory can move without editing
    # the unit file. Must be set before importing server (and the
    # log/macros modules it pulls in), which is why those imports are
    # deferred to inside main() rather than top-level.
    os.environ.setdefault("HEXAGON_FS_ROOT", os.path.dirname(os.path.abspath(__file__)))

    import server
    from boot_log import boot_divider, log

    boot_divider()
    log(f"boot  fs_root={os.environ['HEXAGON_FS_ROOT']}  uart={os.getenv('HEXAGON_UART', '/dev/serial0')}")

    maybe_start_scheduler(log)
    maybe_start_auto(log)

    server.serve(args.host, args.port)


def maybe_start_auto(log, default_enabled=None):
    """Arm "auto" mode (server/modes/auto.py) to start shortly after boot,
    so the installation runs unattended with no browser ever having to open
    it — see server/modes/auto_config.py for the on/off switch and delay.
    Also called by simhost/run.py (imported from here rather than
    duplicated) so auto can be previewed in the simulator the same way.

    `default_enabled` picks the fallback when HEXAGON_AUTO_START isn't set:
    None (main.py's real-boot path) defers to auto_config.START_ON_BOOT;
    simhost/run.py passes False explicitly, since the dev harness
    shouldn't self-start auto — and churn the command tap — on every launch
    the way the real installation should on every boot. Either way,
    HEXAGON_AUTO_START=("0"|"1") overrides both.

    Schedule-aware: when server/schedule.py's schedule is both enabled and
    its clock trustworthy (schedule.clock_synced()), server/scheduler.py
    owns every on/off decision from here on — including the very first one,
    evaluated the instant its thread starts (see maybe_start_scheduler,
    called before this from main()). Arming this unconditional legacy path
    on top of that would race it: a boot at 2am would see this timer
    unconditionally start auto ~10s in, undoing the scheduler's own initial
    "should be off" edge with no ordering guarantee between the two. If the
    schedule is enabled but the clock hasn't synced yet, this still arms
    normally — schedule.py's own "fail on" contract means the scheduler
    will no-op until the clock catches up, so the piece should keep running
    in the meantime rather than sit dark for an unbounded wait.

    A plain threading.Timer, not a call from inside server.serve() (which
    blocks): this only needs to fire once, well after the caller's own
    `import server` has already resolved server.modes' package-level
    imports, so there's no risk of touching mode_runner before it's fully
    initialised.
    """
    from server import schedule
    from server.modes import auto_config
    from server.modes import runner as mode_runner
    from server.modes.auto import run as auto_run

    sched = schedule.get()
    if sched["enabled"] and schedule.clock_synced():
        log("auto: not starting on boot — schedule is enabled and the clock is synced, scheduler owns it")
        return

    base_default = auto_config.START_ON_BOOT if default_enabled is None else default_enabled
    override = os.getenv("HEXAGON_AUTO_START")
    enabled = base_default if override is None else override == "1"

    if not enabled:
        log(f"auto: not starting on boot (default={base_default}, override={override!r})")
        return

    delay_s = auto_config.START_DELAY_S
    log(f"auto: will start in {delay_s}s (see server/modes/auto_config.py to retune or disable)")

    def _start():
        log("auto: starting")
        mode_runner.start("auto", auto_run, {})

    threading.Timer(delay_s, _start).start()


def maybe_start_scheduler(log, default_enabled=None):
    """Start server/scheduler.py's background supervisor thread.

    The thread itself is cheap and inert whenever server/schedule.py's
    schedule is disabled (the default) — it just no-ops every 30s tick —
    so unlike auto there's no meaningful "don't start it at all" case on
    real hardware. `default_enabled`/HEXAGON_SCHEDULE exist only so
    simhost/run.py can opt out by default (mirroring HEXAGON_AUTO_START's
    reasoning: the dev harness shouldn't run a background thread nobody
    asked for on every launch) while still letting a developer preview
    the schedule with HEXAGON_SCHEDULE=1.
    """
    from server import scheduler as mode_scheduler

    base_default = True if default_enabled is None else default_enabled
    override = os.getenv("HEXAGON_SCHEDULE")
    enabled = base_default if override is None else override == "1"

    if not enabled:
        log(f"scheduler: not starting (default={base_default}, override={override!r})")
        return

    log("scheduler: starting")
    mode_scheduler.start()


if __name__ == "__main__":
    main()
