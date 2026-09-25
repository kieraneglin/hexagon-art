// src/sim/unit.js — JS port of one hexagon unit's firmware: the
// STAGING/RUNNING queue state machine (hexagons/runner.cpp) and the
// stepper motion model (hexagons/stepper.cpp), replayed from the same
// timestamped command stream a real unit would receive over the bus.
//
// This is a *predictive* model, not a mirror of telemetry — the wire is
// physically one-way (units cannot report state back), so there is
// nothing to read. Every behavior below is derived directly from the
// firmware source, including its quirks: matching it bit-for-bit is more
// useful than being "nicer" about it.
//
// Fidelity notes — each one changes what a pattern visibly does:
//
//  - Move merging (runner.cpp stageMerging(), see stageWithMerge below):
//    consecutive same-direction M commands coalesce into one (saturating
//    at 65535); consecutive A commands collapse to just the last target,
//    dropped entirely if it's a no-op. Any intervening S/H/W breaks the
//    chain. "01M+90" "01M+90" is ONE 180 degree move, not two.
//
//  - A single tracked/physical angle, not two. Earlier firmware left an
//    interrupted move's tracked angle stale — a "desync" only a full H
//    could clear, since every subsequent M/A computed its delta against
//    that stale value. The current firmware's stop() + tick() instead
//    commit whatever angle the motor actually reached, so there's
//    nothing left to desync: one `angle` field is both the rendered
//    truth and what a subsequent M/A computes against.
//
//    `angle` stays trustworthy through an interruption because of the
//    scotch-yoke's gearing: cutting power (X) stops the mechanism dead
//    within a very tight angular range — there's no meaningful
//    coast/overshoot to model. So interruptAction() below only needs
//    "wherever the idealized profile placed it at the instant X
//    arrived", not a separate deceleration curve.
//
//  - X and C are no longer identical. X (STOP) aborts an in-progress move
//    and clears the queue; C (CLEAR) discards only the *staged* queue and
//    lets a move already underway finish naturally (runner.cpp). Only
//    POWER_DOWN (P) still loses position: it sets `needsHome`, which the
//    next dispatched MOVE_INCREMENTAL/MOVE_ABSOLUTE or CONDITIONAL_HOME
//    (N) clears by homing first — see dispatch() below, mirroring
//    runner.cpp's dispatch()/doHome() lazy-home check. The firmware also
//    gained a background position safeguard (liveAngle() vs. the
//    endstop, checked every tick) that re-homes on a detected physical
//    drift even without a P — not modeled here, since this sim has no
//    step-loss/stall model for it to ever detect a mismatch.
//
//  - G during RUNNING pushes a SEPARATOR fencepost so the next batch
//    pipelines instead of being dropped; consecutive separators resolve
//    transparently (real hardware clears them within one loop()
//    iteration — negligible on any visible timescale, so this sim
//    collapses that into the same instant too).
//
//  - The 64-slot queue silently discards on overflow; so does this
//    model (surfaced via `overflowCount` for the UI to flag, since the
//    firmware itself never will).
//
//  - Homing is blocking and deaf: the firmware spends the whole
//    homeMotor() call not polling the UART, so on real hardware nearly
//    everything sent to that unit while it homes is lost to RX-FIFO
//    overflow. Modeled here as fully uninterruptible: any command whose
//    timestamp falls inside an active HOME action is dropped.
//
//  - Motion uses a closed-form trapezoidal (or triangular, for short
//    moves) profile approximating AccelStepper's ramp, not per-step
//    iteration — see buildProfile()/profilePositionSteps().

export const STEPS_PER_REV = 4096
export const ACCEL_STEPS_PER_S2 = 10000
export const QUEUE_CAPACITY = 64
export const DEFAULT_RPM_TENTHS = 145 // firmware boot default: 14.5 RPM (setup())
export const STROKE_MM = 30

// AccelStepper's first-step interval (_c0 in the Austin approximation),
// independent of target speed — see stepper.cpp begin()/AccelStepper
// internals. Applied once at the start of every dispatched move.
const LEAD_IN_S = 0.676 * Math.sqrt(2 / ACCEL_STEPS_PER_S2)

export function angleToSteps(degrees) {
  return Math.round((degrees * STEPS_PER_REV + 180) / 360)
}

export function computeDelta(target, current) {
  let delta = target - current
  if (delta > 180) delta -= 360
  if (delta < -180) delta += 360
  return delta
}

export function wrap360(deg) {
  let d = deg % 360
  if (d < 0) d += 360
  return d
}

/** Scotch yoke: 0deg = 0mm (home), 180deg = 30mm (full extension). */
export function strokeMm(angleDeg) {
  return (STROKE_MM / 2) * (1 - Math.cos((angleDeg * Math.PI) / 180))
}

function maxSpeedStepsPerSec(rpmTenths) {
  return (rpmTenths / 10 / 60) * STEPS_PER_REV
}

/**
 * Idealized trapezoidal (or triangular, for short moves) profile. Used by
 * stateAt() below to report a unit's in-progress angle at an arbitrary
 * queried instant, not just on completion; exported in case other code
 * ever needs the same motion-profile math.
 */
export function buildProfile(steps, rpmTenths) {
  const v = maxSpeedStepsPerSec(rpmTenths)
  const a = ACCEL_STEPS_PER_S2
  if (steps <= 0 || v <= 0) return { kind: "none", steps: 0, duration: 0 }

  const rampSteps = (v * v) / (2 * a)
  if (steps >= 2 * rampSteps) {
    const accelTime = v / a
    const cruiseSteps = steps - 2 * rampSteps
    const cruiseTime = cruiseSteps / v
    return { kind: "trapezoid", steps, v, a, accelTime, cruiseTime, duration: LEAD_IN_S + 2 * accelTime + cruiseTime }
  }
  const peakV = Math.sqrt(a * steps)
  const accelTime = peakV / a
  return { kind: "triangle", steps, a, peakV, accelTime, duration: LEAD_IN_S + 2 * accelTime }
}

/**
 * Steps completed (0..profile.steps) after `elapsedS` seconds of motion.
 * Exported for the same reason as buildProfile() above.
 */
export function profilePositionSteps(profile, elapsedS) {
  if (profile.kind === "none") return 0
  const t = elapsedS - LEAD_IN_S
  if (t <= 0) return 0

  if (profile.kind === "trapezoid") {
    const { a, v, accelTime, cruiseTime, steps } = profile
    if (t >= 2 * accelTime + cruiseTime) return steps
    const rampSteps = 0.5 * a * accelTime * accelTime
    if (t < accelTime) return 0.5 * a * t * t
    if (t < accelTime + cruiseTime) return rampSteps + v * (t - accelTime)
    const t2 = t - accelTime - cruiseTime
    const cruiseSteps = steps - 2 * rampSteps
    return rampSteps + cruiseSteps + v * t2 - 0.5 * a * t2 * t2
  }

  const { a, accelTime, steps } = profile
  if (t >= 2 * accelTime) return steps
  if (t < accelTime) return 0.5 * a * t * t
  const t2 = t - accelTime
  return steps / 2 + profile.peakV * t2 - 0.5 * a * t2 * t2
}

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

function initialState() {
  return {
    runState: "STAGING", // 'STAGING' | 'RUNNING'
    queue: [], // pending staged commands (post-merge), FIFO — may include SEPARATOR markers
    waitUntilMs: null,
    action: null, // in-progress MOVE or HOME, or null when idle
    angle: 0, // degrees, wrapped 0-359 — the rendered truth AND what M/A compute against (see file header)
    needsHome: false, // set by POWER_DOWN; cleared by a completed HOME, explicit or auto-triggered
    rpmTenths: DEFAULT_RPM_TENTHS,
    overflowCount: 0,
  }
}

function startMove(state, D, nowMs) {
  const steps = angleToSteps(Math.abs(D))
  const profile = buildProfile(steps, state.rpmTenths)
  state.action = {
    kind: "MOVE",
    startMs: nowMs,
    duration: profile.duration,
    profile,
    D,
    fromPhysical: state.angle,
  }
}

/**
 * Constant speed (no accel ramp — matches runStart()/runSpeed()), sweeping
 * the shortest path from the current angle to a true zero-crossing. Real
 * homing's actual path depends on where the endstop physically is, which
 * the sim has no ground truth for — this is a deliberate stand-in, not a
 * literal replay of homing.cpp's four phases.
 */
function startHome(state, nowMs) {
  const D = computeDelta(0, wrap360(state.angle))
  const steps = angleToSteps(Math.abs(D))
  const v = maxSpeedStepsPerSec(state.rpmTenths)
  state.action = {
    kind: "HOME",
    startMs: nowMs,
    duration: v > 0 ? steps / v : 0,
    D,
    fromPhysical: state.angle,
  }
}

function dispatch(state, cmd, nowMs) {
  switch (cmd.type) {
    case "MOVE_INCREMENTAL": {
      // Mirrors runner.cpp dispatch()'s lazy-home check: a move dispatched
      // while needsHome is set homes first. Re-queue the move ahead of
      // whatever else is staged and let the HOME action run; the move
      // dispatches for real once that HOME completes and clears needsHome.
      if (state.needsHome) {
        state.queue.unshift(cmd)
        startHome(state, nowMs)
        break
      }
      const D = cmd.forward ? cmd.degrees : -cmd.degrees
      startMove(state, D, nowMs)
      break
    }
    case "MOVE_ABSOLUTE": {
      if (state.needsHome) {
        state.queue.unshift(cmd)
        startHome(state, nowMs)
        break
      }
      const D = computeDelta(cmd.angle, state.angle)
      if (D === 0) break // matches moveToAngle()'s early return — true no-op, no move queued
      startMove(state, D, nowMs)
      break
    }
    case "HOME":
      startHome(state, nowMs)
      break
    case "CONDITIONAL_HOME":
      // Homes only if needed, otherwise a true no-op (no action started,
      // so this drains instantly — matches FFN's "nothing needed homing"
      // common case in server/routes/patterns.py).
      if (state.needsHome) startHome(state, nowMs)
      break
    case "SET_SPEED":
      state.rpmTenths = cmd.tenths
      break
    case "WAIT":
      state.waitUntilMs = nowMs + cmd.ms
      break
    default:
      break // SEPARATOR never reaches here (handled by the advance loop)
  }
}

function commitAction(state, action) {
  if (action.kind === "MOVE") {
    state.angle = wrap360(action.fromPhysical + action.D)
  } else if (action.kind === "HOME") {
    state.angle = 0
    state.needsHome = false
  }
}

/** Truncate an in-progress action at `nowMs` (X mid-move, or POWER_DOWN). */
function interruptAction(state, action, nowMs) {
  const elapsedS = (nowMs - action.startMs) / 1000
  if (action.kind === "MOVE") {
    const totalSteps = action.profile.steps || 1
    const frac = Math.min(1, profilePositionSteps(action.profile, elapsedS) / totalSteps)
    state.angle = wrap360(action.fromPhysical + frac * action.D)
  } else if (action.kind === "HOME") {
    // Unreachable in practice (homing is deaf to X on real hardware — see
    // applyCommand below), kept as a defensive fallback only.
    const frac = action.duration > 0 ? Math.min(1, elapsedS / action.duration) : 1
    state.angle = wrap360(action.fromPhysical + frac * action.D)
  }
}

/**
 * Advance `state` as far as possible without exceeding `nowMs`: complete
 * any finished action, then drain the queue (dispatching WAIT/SET_SPEED
 * chains and starting the next MOVE/HOME) until nothing more can happen
 * before `nowMs`.
 */
function advanceTo(state, nowMs) {
  // `cursor` is when the *next* dispatch logically happens — not always
  // `nowMs`. When this loop catches up through several already-finished
  // actions/waits in one call (e.g. replaying a big time jump), each new
  // action must be timestamped at the moment its predecessor actually
  // completed, not at the outer query time — otherwise a move dispatched
  // during catch-up gets `startMs = nowMs`, so every future query of the
  // same fixed command list (this is a pure-replay model — see
  // simulateUnit) recomputes it as "just started" forever, and it never
  // appears to progress no matter how far past its real start `nowMs` is.
  let cursor = nowMs
  for (;;) {
    if (state.action) {
      const completesAtMs = state.action.startMs + state.action.duration * 1000
      if (nowMs < completesAtMs) return // still mid-action as of nowMs
      commitAction(state, state.action)
      state.action = null
      cursor = completesAtMs
      continue
    }
    if (state.runState !== "RUNNING") return
    if (state.waitUntilMs !== null) {
      if (nowMs < state.waitUntilMs) return
      cursor = state.waitUntilMs
      state.waitUntilMs = null
    }
    if (state.queue.length === 0) {
      state.runState = "STAGING"
      return
    }
    const next = state.queue.shift()
    if (next.type === "SEPARATOR") continue // transparent — see file header
    dispatch(state, next, cursor)
  }
}

function stageWithMerge(state, cmd) {
  const tail = state.queue.length > 0 ? state.queue[state.queue.length - 1] : null

  if (cmd.type === "MOVE_INCREMENTAL" && tail && tail.type === "MOVE_INCREMENTAL" && tail.forward === cmd.forward) {
    tail.degrees = Math.min(65535, tail.degrees + cmd.degrees)
    return { ok: true }
  }
  if (cmd.type === "MOVE_ABSOLUTE" && tail && tail.type === "MOVE_ABSOLUTE") {
    const delta = computeDelta(cmd.angle, tail.angle)
    if (delta === 0) return { ok: true, dropped: "no-op absolute move (matches previous target)" }
    tail.angle = cmd.angle
    return { ok: true }
  }

  if (state.queue.length >= QUEUE_CAPACITY) {
    state.overflowCount += 1
    return { ok: false, reason: "queue full (64 slots) — silently discarded by firmware" }
  }
  state.queue.push({ ...cmd })
  return { ok: true }
}

const STAGEABLE = new Set(["MOVE_INCREMENTAL", "MOVE_ABSOLUTE", "HOME", "CONDITIONAL_HOME", "SET_SPEED", "WAIT"])

function applyImmediate(state, cmd, nowMs) {
  switch (cmd.type) {
    case "STOP":
      // Always immediate: halt motor dead and discard the staged queue —
      // the true emergency stop, unlike CLEAR below. stop()+tick() commit
      // the angle actually reached (runner.cpp), so no re-home is needed.
      if (state.action) {
        interruptAction(state, state.action, nowMs)
        state.action = null
      }
      state.queue = []
      state.waitUntilMs = null
      state.runState = "STAGING"
      return { ok: true }
    case "CLEAR":
      // Discard the staged queue without touching a move already in
      // progress — it finishes naturally; only what was queued after it
      // is cancelled. No explicit STAGING transition needed here:
      // advanceTo() already falls back to STAGING once it finds the
      // (now-empty) queue after the in-progress action completes.
      state.queue = []
      state.waitUntilMs = null
      return { ok: true }
    case "POWER_DOWN":
      // Immediate: halt motor, discard queue, require a re-home before
      // the next move. The only command that still loses position.
      if (state.action) {
        interruptAction(state, state.action, nowMs)
        state.action = null
      }
      state.queue = []
      state.waitUntilMs = null
      state.runState = "STAGING"
      state.needsHome = true
      return { ok: true }
    case "GO":
      state.queue.push({ type: "SEPARATOR" })
      state.runState = "RUNNING"
      return { ok: true }
    case "CLOCK_NUDGE":
      return { ok: true } // accepted, no modeled motion effect
    default:
      return { ok: true }
  }
}

/**
 * Apply one already-parsed, already-addressed command to `state` at
 * `nowMs`. Mutates `state` in place. Returns `{ok, reason?}` describing
 * whether it actually took effect (vs. being dropped — queue overflow,
 * or the unit being deaf mid-home).
 */
function applyCommand(state, cmd, nowMs) {
  advanceTo(state, nowMs) // catch up to just before this command's effect

  if (state.action && state.action.kind === "HOME") {
    return { ok: false, reason: "unit deaf: homing in progress (RX FIFO overflow on real hardware)" }
  }

  const result = STAGEABLE.has(cmd.type) ? stageWithMerge(state, cmd) : applyImmediate(state, cmd, nowMs)

  advanceTo(state, nowMs) // let GO's synchronous first dispatch happen immediately
  return result
}

/** Snapshot the visible state of `state` at `nowMs` (non-mutating). */
function stateAt(state, nowMs) {
  let angle = state.angle
  let moving = false
  let homing = false

  if (state.action) {
    const elapsedS = (nowMs - state.action.startMs) / 1000
    const frac =
      state.action.kind === "MOVE"
        ? Math.min(1, profilePositionSteps(state.action.profile, elapsedS) / (state.action.profile.steps || 1))
        : state.action.duration > 0
          ? Math.min(1, elapsedS / state.action.duration)
          : 1
    angle = wrap360(state.action.fromPhysical + frac * state.action.D)
    moving = elapsedS < state.action.duration
    homing = state.action.kind === "HOME"
  }

  return {
    angleDeg: angle,
    depthMm: strokeMm(angle),
    moving,
    homing,
    queueLength: state.queue.filter((c) => c.type !== "SEPARATOR").length,
    rpm: state.rpmTenths / 10,
    runState: state.runState,
    needsHome: state.needsHome,
    overflowCount: state.overflowCount,
  }
}

/**
 * Replay a single unit's full command history (already filtered to
 * commands addressed to it, sorted by timestamp ascending) up to
 * `queryNowMs`, and return its visible state at that instant.
 *
 * Pure function: no shared mutable state, no memoization — each call
 * replays from t=0. This is what makes the timeline scrubber trivial
 * (jumping to any past instant is just calling this with a different
 * `queryNowMs`), at the cost of O(commands so far) per call. Fine at
 * the command volumes a single dev session produces — see tap.py's
 * buffer sizing notes for the numbers this was checked against.
 *
 * `commands` entries: `{ tMs, raw, parsed }` where `parsed` is
 * bus.parseCommand()'s result.
 */
export function simulateUnit(commands, queryNowMs) {
  const state = initialState()
  const log = []

  for (const entry of commands) {
    if (entry.tMs > queryNowMs) break
    if (!entry.parsed.ok) {
      log.push({ tMs: entry.tMs, raw: entry.raw, ok: false, reason: entry.parsed.reason })
      continue
    }
    const result = applyCommand(state, entry.parsed, entry.tMs)
    log.push({ tMs: entry.tMs, raw: entry.raw, ok: result.ok, reason: result.reason })
  }

  advanceTo(state, queryNowMs)
  const snapshot = stateAt(state, queryNowMs)
  snapshot.log = log
  return snapshot
}
