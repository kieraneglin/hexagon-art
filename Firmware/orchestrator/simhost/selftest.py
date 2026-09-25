# simhost/selftest.py — Headless pattern sanity checks.
#
# Replays every registered pattern's command stream (at default and
# extreme parameter values, per server/patterns/__init__.py's
# PATTERN_META) through a minimal port of the firmware's staging-queue
# and merge rules (hexagons/runner.cpp's stageMerging()), and asserts:
#
#   1. No unit's queue would exceed the real 64-slot capacity.
#   2. Every emitted command is well-formed per the wire protocol
#      (hexagons/comm.cpp's parseLine()) — a pattern author's own typo
#      or off-by-one wouldn't be silently swallowed the way it would be
#      on real hardware.
#
# This deliberately does NOT simulate motion or timing — that's what
# src/sim/unit.js does, in the browser, against a live/replayed command
# stream. This is a much cheaper, CI-friendly check for the one class of
# bug a pattern author can introduce without ever seeing it happen: a
# queue silently overflowing because a param was pushed too high.
#
# Usage:
#   python3 -m simhost.selftest

import sys

from simhost.run import _prepare_fs_root

QUEUE_CAPACITY = 64


def _parse_uint(s, allow_sign):
    """Mirrors comm.cpp's parseUInt: optional sign (if allowed), then
    only digits, value 0-65535. Returns (value, negative) or None."""
    if not s:
        return None
    negative = False
    i = 0
    if s[0] in "+-":
        if not allow_sign:
            return None
        negative = s[0] == "-"
        i = 1
    digits = s[i:]
    if not digits.isdigit():
        return None
    value = int(digits)
    return None if value > 65535 else (value, negative)


def parse_command(raw):
    """Mirror hexagons/comm.cpp's parseLine() validation. Returns True if
    a real unit would accept this command's syntax (not whether an
    addressed unit exists — pattern code only ever addresses real units
    via layout.RINGS/ALL_UNITS, so that distinction doesn't matter here)."""
    s = raw.strip()
    if len(s) < 3:
        return False
    try:
        int(s[:2], 16)
    except ValueError:
        return False

    cmd_char = s[2].upper()
    args = s[3:]

    if cmd_char == "M":
        if len(args) < 2 or args[0] not in "+-":
            return False
        r = _parse_uint(args, True)
        return r is not None and r[0] != 0
    if cmd_char == "A":
        if not args or args[0] in "+-":
            return False
        r = _parse_uint(args, False)
        return r is not None and r[0] <= 359
    if cmd_char in ("H", "X", "G", "C", "N", "P"):
        return args == ""
    if cmd_char == "S":
        if not args or args[0] in "+-":
            return False
        r = _parse_uint(args, False)
        return r is not None and 10 <= r[0] <= 300
    if cmd_char == "W":
        if not args or args[0] in "+-":
            return False
        r = _parse_uint(args, False)
        return r is not None and r[0] >= 1
    if cmd_char == "R":
        if not args or args[0] in "+-":
            return False
        r = _parse_uint(args, False)
        return r is not None
    if cmd_char == "K":
        return args in ("+", "-")
    return False


def _stage(queue, cmd_type, payload):
    """Mirror runner.cpp's stageMerging() for M/A — the two command types
    a pattern can grow without bound if it isn't careful. Other stageable
    types (H, S, W) never merge, so they're handled inline by the caller."""
    if queue and cmd_type == "M" and queue[-1][0] == "M" and queue[-1][1]["forward"] == payload["forward"]:
        queue[-1][1]["degrees"] = min(65535, queue[-1][1]["degrees"] + payload["degrees"])
        return True
    if queue and cmd_type == "A" and queue[-1][0] == "A":
        if queue[-1][1]["angle"] == payload["angle"]:
            return True  # no-op — matches firmware dropping a same-target A
        queue[-1][1]["angle"] = payload["angle"]
        return True
    if len(queue) >= QUEUE_CAPACITY:
        return False
    queue.append((cmd_type, payload))
    return True


def check_pattern(name, run_fn, kwargs, all_unit_ids):
    """Run one pattern invocation and return a list of problem strings."""
    import tap

    watermark = tap.current_seq()
    run_fn(**kwargs)
    result = tap.tail(watermark - 1)

    problems = []
    queues = {uid: [] for uid in all_unit_ids}

    for _seq, _t_ms, raw in result["commands"]:
        if not parse_command(raw):
            problems.append(f"{name}{kwargs}: malformed command {raw!r} — a real unit would silently drop it")
            continue

        id_str, cmd_char, args = raw[:2], raw[2].upper(), raw[3:]
        target_ids = list(queues.keys()) if id_str.upper() == "FF" else [int(id_str, 16)]

        if cmd_char in ("G", "C", "X", "K", "P"):
            continue  # immediate — doesn't grow a staged queue

        for uid in target_ids:
            queue = queues.setdefault(uid, [])
            if cmd_char == "M":
                ok = _stage(queue, "M", {"degrees": int(args[1:]), "forward": args[0] == "+"})
            elif cmd_char == "A":
                ok = _stage(queue, "A", {"angle": int(args)})
            else:  # H, N, S, W — stageable, never merge. R is also staged and
                # never merges, but its real semantics (pop-and-repush against
                # the existing queue) mean a loop can never overflow regardless
                # of its count — modeling it as an ordinary slot here is
                # strictly more conservative than reality, not less.
                if len(queue) >= QUEUE_CAPACITY:
                    ok = False
                else:
                    queue.append((cmd_char, {}))
                    ok = True
            if not ok:
                problems.append(f"{name}{kwargs}: unit {id_str} queue exceeds {QUEUE_CAPACITY} slots")

    return problems


def _check_life_generations(kwargs, all_unit_ids, generations=30):
    """Run several generations of life.py's pure advance/render/stage
    pipeline directly — no thread, no time.sleep — and check every emitted
    command's syntax, plus that no unit receives more than one A command
    in a single generation (render()/stage_generation()'s per-unit-per-tick
    contract). life.py stages `A` (absolute) commands driven by a CA, not
    helpers.stage_bounce, so it needs its own check rather than the
    amplitude-based one below."""
    import orchestrator
    import tap
    from server.modes.life import advance, render, seed_board, stage_generation
    from server.patterns.helpers import rand_stream

    rand = rand_stream(kwargs["seed"])
    state = seed_board(rand, kwargs["ruleset"], kwargs["density"], kwargs["cycle_states"])
    angles = {uid: float(kwargs["start_angle"]) for uid in all_unit_ids}

    problems = []
    for gen in range(generations):
        new_angles = render(
            state,
            angles,
            kwargs["ruleset"],
            kwargs["cycle_states"],
            kwargs["smoothing"],
            kwargs["start_angle"],
            kwargs["max_angle"],
            kwargs["max_step"],
        )

        watermark = tap.current_seq()
        if stage_generation(angles, new_angles):
            orchestrator.go_all()

        seen = {}
        for _seq, _t_ms, raw in tap.tail(watermark - 1)["commands"]:
            if not parse_command(raw):
                problems.append(f"mode life{kwargs} (gen {gen}): malformed command {raw!r}")
                continue
            if raw[2].upper() == "A":
                uid = int(raw[:2], 16)
                seen[uid] = seen.get(uid, 0) + 1
        for uid, count in seen.items():
            if count > 1:
                problems.append(
                    f"mode life{kwargs} (gen {gen}): unit {uid:02X} received {count} A commands "
                    f"in one generation (expected at most 1)"
                )

        angles = new_angles
        state = advance(state, kwargs["ruleset"], rand, kwargs["spawn_rate"], kwargs["cycle_states"])

    return problems


def check_modes(all_unit_ids):
    """Static checks for server/modes/ — no threads, no time.sleep.

    A mode runs forever until stopped, so unlike a pattern there's no
    finite command stream to replay. Instead this exercises the same two
    things a mode's tick loop actually relies on, both reachable without
    ever starting the background thread:

      1. Each mode's queue-safety validator (server/modes/__init__.py's
         VALIDATORS) at default and extreme params — this is the same
         check a real POST /api/modes/<name> runs before starting the
         thread, and it's what guarantees a unit's queue never grows
         without bound (see server/modes/wave.py's module docstring).
      2. The actual command syntax a mode emits — for wave/random that's
         helpers.stage_bounce (the one-sided rise-and-return shape both
         use), parsed the same way check_pattern parses a burst; life.py
         stages `A` from a CA instead, so it gets its own generation-by-
         generation check (_check_life_generations above).
    """
    import tap
    from server.modes import VALIDATORS, mode_meta
    from server.patterns.helpers import stage_bounce

    problems = []
    for meta in mode_meta():
        name = meta["id"]
        validator = VALIDATORS.get(name)
        params = meta.get("params", [])
        default_kwargs = {p["key"]: p["default"] for p in params}
        extreme_kwargs = {p["key"]: p["max"] for p in params}

        for label, kwargs in (("default", default_kwargs), ("extreme", extreme_kwargs)):
            print(f"checking mode {name} ({label} params: {kwargs})...")
            if validator is not None:
                try:
                    validator(**kwargs)
                except ValueError as exc:
                    # "extreme" maxes every param independently, which can
                    # legitimately violate a *joint* constraint between two
                    # params (e.g. wave's start_angle + amplitude <= 180)
                    # even though each individual value is in-range alone —
                    # that's the validator correctly doing its job at a
                    # boundary that was never a valid combination, not a
                    # bug. "default" must always be valid; hard-fail there.
                    if label == "default":
                        problems.append(f"mode {name}{kwargs} ({label}): {exc}")
                    continue

            if name == "life":
                problems.extend(_check_life_generations(kwargs, all_unit_ids))
                continue

            # wave uses "amplitude"; random (shimmer.py) uses "max_amplitude" —
            # either is what a single tick would actually stage.
            amplitude = kwargs.get("amplitude", kwargs.get("max_amplitude"))
            if amplitude is None:
                continue
            uid = all_unit_ids[0]
            watermark = tap.current_seq()
            stage_bounce(uid, amplitude)
            for _seq, _t_ms, raw in tap.tail(watermark - 1)["commands"]:
                if not parse_command(raw):
                    problems.append(f"mode {name}{kwargs}: malformed command {raw!r}")

    return problems


def check_schedule():
    """Static checks for server/schedule.py — no threads, the scheduler
    supervisor thread (server/scheduler.py) is never started here, same as
    every other check in this module.

    Covers: "HH:MM" parsing accept/reject, the on_time==off_time rejection,
    an unknown timezone rejection, the default timezone actually
    constructing (catches a bare dev environment with no tzdata installed —
    see requirements.txt), and is_on_at()'s truth table for both window
    shapes (same-day, overnight-wrap) including both boundary instants,
    which is exactly the class of off-by-one a "<=" vs "<" typo would
    introduce silently.
    """
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    from server import schedule

    problems = []

    for bad in ("09:60", "24:00", "", "9am", "09:00:00"):
        try:
            schedule.update(on_time=bad)
            problems.append(f"schedule: on_time={bad!r} should have been rejected")
        except ValueError:
            pass

    try:
        schedule.update(on_time="09:00", off_time="09:00")
        problems.append("schedule: on_time == off_time should have been rejected")
    except ValueError:
        pass

    try:
        schedule.update(timezone="Not/AZone")
        problems.append("schedule: an unknown timezone should have been rejected")
    except ValueError:
        pass

    try:
        ZoneInfo(schedule.DEFAULT_TIMEZONE)
    except Exception as exc:
        problems.append(f"schedule: DEFAULT_TIMEZONE {schedule.DEFAULT_TIMEZONE!r} failed to construct: {exc!r}")

    tz = ZoneInfo("UTC")
    schedule.update(on_time="09:00", off_time="22:00", timezone="UTC")
    same_day_cases = [(8, False), (9, True), (15, True), (21, True), (22, False), (23, False)]
    for hour, expected in same_day_cases:
        actual = schedule.is_on_at(datetime(2026, 1, 1, hour, 0, tzinfo=tz))
        if actual != expected:
            problems.append(f"schedule: is_on_at same-day window at {hour:02d}:00 expected {expected}, got {actual}")

    schedule.update(on_time="20:00", off_time="02:00")
    overnight_cases = [(19, False), (20, True), (23, True), (1, True), (2, False), (3, False)]
    for hour, expected in overnight_cases:
        actual = schedule.is_on_at(datetime(2026, 1, 1, hour, 0, tzinfo=tz))
        if actual != expected:
            problems.append(f"schedule: is_on_at overnight window at {hour:02d}:00 expected {expected}, got {actual}")

    # Exact boundary instants: on_time-inclusive, off_time-exclusive.
    boundary_cases = [
        (time(20, 0), True),
        (time(2, 0), False),
    ]
    for t, expected in boundary_cases:
        actual = schedule.is_on_at(datetime(2026, 1, 1, t.hour, t.minute, tzinfo=tz))
        if actual != expected:
            problems.append(f"schedule: is_on_at boundary {t} expected {expected}, got {actual}")

    # Leave the sandbox's schedule at its defaults for whatever runs next.
    schedule.update(
        enabled=schedule.DEFAULT_ENABLED,
        on_time=schedule.DEFAULT_ON,
        off_time=schedule.DEFAULT_OFF,
        timezone=schedule.DEFAULT_TIMEZONE,
    )

    return problems


def check_schedule_replay():
    """Replay-validates the exact failure mode server/scheduler.py's
    _turn_on() guards against: a mode's param recorded while one motor
    speed was configured can fall outside another speed's queue-safety
    floor (every VALIDATORS entry is speed-dependent — see AGENTS.md's
    "Queue-safety guard"). Called from main() inside the existing
    MIN_RPM/DEFAULT_RPM/MAX_RPM sweep, the same place every other
    speed-dependent check already lives.
    """
    from server.modes import REGISTRY, mode_meta, resolve_params

    problems = []
    for meta in mode_meta():
        name = meta["id"]
        for label, kwargs_in in (
            ("default", {p["key"]: p["default"] for p in meta["params"]}),
            ("extreme", {p["key"]: p["max"] for p in meta["params"]}),
        ):
            try:
                resolve_params(name, kwargs_in)
            except ValueError:
                # Same caveat as check_modes(): "extreme" maxes every param
                # independently and can legitimately violate a joint
                # constraint. "default" must always resolve cleanly, since
                # that's exactly the shape a real recorded last-mode takes.
                if label == "default":
                    problems.append(f"schedule replay: {name}{kwargs_in} ({label}) failed to resolve")
                continue
            if name not in REGISTRY:
                problems.append(f"schedule replay: resolved mode {name!r} not in REGISTRY")

    return problems


def check_auto_config():
    """Static check for server/modes/auto_config.py — auto mode (see
    server/modes/auto.py) has no MODE_META params of its own, so
    check_modes() above's default/extreme sweep never exercises it beyond
    calling auto.validate() with no kwargs. This calls it for real, which
    walks every configured segment and validates its sub-mode + params
    (auto.validate()'s own job) — catching a bad hand-edit to
    auto_config.py (an unknown mode name, a non-positive `minutes`, or
    params that violate that sub-mode's own validate()) the same way a
    malformed pattern/mode param would be caught above, before it ever
    reaches the background thread on real hardware.
    """
    from server.modes.auto import validate as auto_validate

    print("checking auto_config.py...")
    try:
        auto_validate()
    except ValueError as exc:
        return [f"auto_config: {exc}"]
    return []


def main():
    _prepare_fs_root()

    from server import settings
    from server.patterns import PATTERN_META, REGISTRY
    from server.patterns.layout import ALL_UNITS

    all_problems = []
    for meta in PATTERN_META:
        name = meta["id"]
        run_fn = REGISTRY[name]
        params = meta.get("params", [])

        default_kwargs = {p["key"]: p["default"] for p in params}
        extreme_kwargs = {p["key"]: p["max"] for p in params}

        for label, kwargs in (("default", default_kwargs), ("extreme", extreme_kwargs)):
            print(f"checking {name} ({label} params: {kwargs})...")
            all_problems.extend(check_pattern(name, run_fn, kwargs, ALL_UNITS))

    # home_wave is registry-only (deliberately absent from PATTERN_META so
    # it isn't a Patterns-page card), which is exactly why its N-vs-H
    # bug went unnoticed by this suite before — cover it explicitly.
    print("checking home_wave (default params)...")
    all_problems.extend(check_pattern("home_wave", REGISTRY["home_wave"], {}, ALL_UNITS))

    # Every mode's queue-safety floor (server/modes/*.py's min_*_ms()) and
    # auto_config.py's per-segment scaling (auto.py's _scaled_params) both
    # key off server/settings.py's configured default_rpm — a slower speed
    # is exactly the case that can silently eat a segment's queue-safety
    # margin (see auto.py's _scaled_params docstring), so check at both
    # ends of the allowed range plus the untouched default rather than
    # only at whatever speed happens to be configured on disk.
    for rpm in (settings.MIN_RPM, settings.DEFAULT_RPM, settings.MAX_RPM):
        print(f"--- checking modes/auto at {rpm} RPM ---")
        settings.set_default_rpm(rpm)
        all_problems.extend(check_modes(ALL_UNITS))
        all_problems.extend(check_auto_config())
        all_problems.extend(check_schedule_replay())
    settings.set_default_rpm(settings.DEFAULT_RPM)  # leave the sandbox at its default

    print("checking schedule.py...")
    all_problems.extend(check_schedule())

    if all_problems:
        print(f"\n{len(all_problems)} problem(s) found:")
        for p in all_problems:
            print(f"  - {p}")
        sys.exit(1)

    print("\nAll patterns and modes OK.")


if __name__ == "__main__":
    main()
