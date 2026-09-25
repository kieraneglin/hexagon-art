#!/usr/bin/env python3
"""
simhost/run.py — Run the real orchestrator server, sandboxed, for local dev.

Now that the orchestrator itself runs on CPython (Raspberry Pi), this is
just main.py pointed at a throwaway sandbox directory instead of the repo
root, plus HEXAGON_UART=none so there's no attempt to open a real serial
port on your dev machine — every command is already captured by
tap.record() inside orchestrator.send() before a write would happen, so
discarding it is exactly right for a harness with no bus to write to.

Usage:
    python3 simhost/run.py                # serve on 127.0.0.1:8080
    python3 simhost/run.py --port 9090

Then build the UI once (or run `npm run dev` separately) so dist/
exists:
    npm run build

Open http://127.0.0.1:8080/sim in a browser.
"""

import argparse
import os
import sys

SIMHOST_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SIMHOST_DIR)
FS_ROOT = os.path.join(SIMHOST_DIR, ".fsroot")


def _prepare_fs_root():
    """Set up the sandbox directory that flash-path writes land under.

    dist/ is symlinked (not copied) so a fresh `npm run build` is picked
    up immediately without re-running this script.
    """
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)

    os.makedirs(FS_ROOT, exist_ok=True)
    os.environ["HEXAGON_FS_ROOT"] = FS_ROOT
    os.environ.setdefault("HEXAGON_UART", "none")

    dist_link = os.path.join(FS_ROOT, "dist")
    dist_target = os.path.join(REPO_ROOT, "dist")
    if not os.path.exists(dist_link):
        if os.path.isdir(dist_target):
            os.symlink(dist_target, dist_link)
        else:
            print(f"warning: {dist_target} does not exist — run `npm run build` first", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    _prepare_fs_root()

    import server  # the real app — server/__init__.py, same Flask/Waitress path as main.py
    from boot_log import log
    from main import maybe_start_auto, maybe_start_scheduler  # same boot-arm logic main.py uses on real hardware

    # default_enabled=False for both: the dev harness shouldn't self-start
    # "auto" (server/modes/auto.py) or the schedule supervisor — and start
    # writing to the command tap — on every launch the way the real
    # installation should on every boot. HEXAGON_AUTO_START=1 /
    # HEXAGON_SCHEDULE=1 preview either here anyway. Scheduler first, same
    # order as main.py, though only maybe_start_auto's own gate (not
    # ordering) is what actually prevents the two racing on real hardware.
    maybe_start_scheduler(log, default_enabled=False)
    maybe_start_auto(log, default_enabled=False)

    print(f"simhost: serving the real orchestrator server on http://{args.host}:{args.port}")
    print(f"simhost: flash filesystem sandboxed under {FS_ROOT}")
    print(f"simhost: open http://{args.host}:{args.port}/sim")
    server.serve(args.host, args.port)


if __name__ == "__main__":
    main()
