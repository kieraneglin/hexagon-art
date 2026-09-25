#include "runner.h"

#include <Arduino.h>

#include "clockcal.h"
#include "comm.h"
#include "homing.h"
#include "queue.h"
#include "stepper.h"

// ---------------------------------------------------------------------------
// Internal state
// ---------------------------------------------------------------------------

static Stepper28BYJ* s_motor = nullptr;
static OpticalSwitch* s_endstop = nullptr;

enum class RunState : uint8_t {
  STAGING,  // accumulating commands; waiting for G
  RUNNING   // draining the queue command-by-command
};

static RunState s_runState = RunState::STAGING;
static uint32_t s_waitUntil = 0;  // millis() deadline set by WAIT commands
static bool s_needsHome = false;  // set by P; cleared by a successful home

// Idle power-save: release coils after this long with nothing that will
// start the motor moving on its own. "Idle" means the motor isn't stepping
// AND nothing queued is about to make it step without new bus traffic —
// STAGING with no G yet, or RUNNING but parked inside a W wait both count,
// same as a fully drained queue. See motorIsIdle() below. Unlike P, this does
// NOT set s_needsHome — friction and the gear ratio are trusted to hold the
// physical position, so the next move just resumes from wherever the
// stepper's tracked position already says it is (the D command shares this
// contract for a manually-placed release inside a sequence). Gated on actual
// idleness rather than message traffic, so it can't be starved by
// non-actionable messages (e.g. a stray K nudge) and gives a long-running
// R<n> loop a fresh countdown once it actually finishes, instead of
// inheriting however stale s_idleSince was when the loop started.
//
// The 28BYJ-48 is a constant-voltage L/R drive with no current chopping, so
// holding and moving draw essentially the same current — heat is
// proportional to time energised, not to speed or load. That makes this
// value worth getting tight: within one batch there's no gap at all
// (advanceQueue() dispatches the instant the motor goes idle), so all this
// has to cover is orchestrator/bus latency between batches — tens of ms at
// 115200 baud — not anything about the choreography itself. A choreography
// with waits shorter than this can still get an immediate release via the
// staged D command instead of waiting on the timer.
static const uint32_t IDLE_RELEASE_MS = 100;
static uint32_t s_idleSince = 0;          // millis() when idleness began
static bool s_idleCoilsReleased = false;  // avoids repeat release() calls while idle

// Set by a staged R<n> with n > 1; cleared when the final pass begins, or by
// anything that aborts a running sequence (X, C, P, or a G that supersedes
// an in-progress infinite loop).
static bool s_recycling = false;
static uint16_t s_loopRemaining = 0;  // passes left; 0 while s_recycling means "forever"

// Position safeguard: home is the midpoint of the endstop trigger window, so
// a unit whose commanded position (liveAngle()) is 0° must read the endstop
// as triggered. Reading clear there means the commanded and physical
// positions have diverged — skipped steps, a hand-nudge, etc. Only used by
// the idle recovery path below; the mid-move path is debounce-free (see its
// own comment for why).
static const uint16_t POS_CHECK_CONFIRM_MS = 50;
static uint32_t s_posLostSince = 0;  // millis() when the mismatch first appeared; 0 = none
static bool s_posCheckArmed = true;  // one recovery attempt per visit to the home window

// ---------------------------------------------------------------------------
// Command dispatch
// ---------------------------------------------------------------------------

// Run a blocking home and reset angle. Returns false on failure (queue cleared).
static bool doHome() {
  if (homeMotor(*s_motor, *s_endstop)) {
    s_motor->resetAngle();
    s_needsHome = false;
    return true;
  }
  queueClear();
  s_runState = RunState::STAGING;
  s_recycling = false;
  s_loopRemaining = 0;
  return false;
}

static void dispatch(const Command& cmd) {
  switch (cmd.type) {
    case CommandType::MOVE_INCREMENTAL:
      if (s_needsHome && !doHome()) {
        return;
      }
      s_motor->moveByAngle(cmd.param, cmd.direction);
      break;
    case CommandType::MOVE_ABSOLUTE:
      if (s_needsHome && !doHome()) {
        return;
      }
      s_motor->moveToAngle(cmd.param);
      break;
    case CommandType::HOME:
      doHome();
      break;
    case CommandType::CONDITIONAL_HOME:
      if (s_needsHome) {
        doHome();
      }
      break;
    case CommandType::SET_SPEED:
      s_motor->setRPM((float)cmd.param / 10.0f);
      break;
    case CommandType::WAIT:
      s_waitUntil = millis() + cmd.param;
      break;
    case CommandType::RELEASE:
      // Staged power-save: release coils in place. Queue keeps draining and
      // s_needsHome is untouched — same position-retained contract as the
      // automatic idle release below, just placed explicitly by the
      // orchestrator instead of inferred from a stall.
      s_motor->release();
      break;
    default:
      break;
  }
}

// ---------------------------------------------------------------------------
// Move merging
// ---------------------------------------------------------------------------
//
// Consecutive stageable move commands that travel in the same direction are
// merged into a single command so the motor never comes to a full stop between
// them.  Only immediate neighbours are considered — any intervening non-move
// command (S, H, W, …) breaks the chain.
//
// Rules:
//   INCREMENTAL + INCREMENTAL, same direction → sum degrees (capped at 65535)
//   ABSOLUTE    + ABSOLUTE                    → replace tail with new target
//     (direction is inferred from the delta between the two absolute targets;
//      if the delta is zero the new command is a no-op and is dropped)
//   Any other combination                     → push normally
//
// For absolute moves, "same direction" means the shortest-path delta from the
// tail target to the new target has the same sign as the shortest-path delta
// from the tail's predecessor to the tail target.  Because we only store the
// final target (not the predecessor direction) we instead simply compare the
// sign of the delta between the two absolute targets: if it is non-zero, the
// motor will travel that way regardless of history, so merging is safe.

static void stageMerging(const Command& cmd) {
  Command tail;

  if (cmd.type == CommandType::MOVE_INCREMENTAL) {
    if (queuePeekTail(&tail) && tail.type == CommandType::MOVE_INCREMENTAL && tail.direction == cmd.direction) {
      // Merge: accumulate degrees, saturate at 65535.
      uint32_t merged = (uint32_t)tail.param + (uint32_t)cmd.param;
      if (merged > 65535u) {
        merged = 65535u;
      }
      tail.param = (uint16_t)merged;
      queueReplaceTail(tail);
      return;
    }
  } else if (cmd.type == CommandType::MOVE_ABSOLUTE) {
    if (queuePeekTail(&tail) && tail.type == CommandType::MOVE_ABSOLUTE) {
      int16_t delta = computeDelta(cmd.param, tail.param);
      if (delta == 0) {
        // New target is identical to the tail — drop it (no-op move).
        return;
      }
      // Replace tail with the new target; the motor travels from wherever it
      // was heading to the new destination without stopping.
      tail.param = cmd.param;
      queueReplaceTail(tail);
      return;
    }
  }

  // No merge possible — push as a new entry.
  queuePush(cmd);
}

// Pops and dispatches the next actionable command, transparently consuming
// REPEAT and SEPARATOR control entries along the way (they adjust loop state
// and move on without dispatching, all within one call). Leaves s_runState
// as STAGING if the queue drains or a loop is cancelled instead of finding
// something to dispatch.
//
// Called both by the per-tick RUNNING advance below and by the STAGING->G
// transition — a batch is allowed to *open* with R<n>, so the very first
// pop after a G must go through this same control-entry handling rather
// than a raw dispatch() that doesn't know what REPEAT/SEPARATOR mean.
//
// Bounded so a queue of nothing but control entries can never spin forever
// here and starve the commPoll() drain below it — which would leave the
// unit deaf to X for however long that took.
static void advanceQueue() {
  for (uint8_t guard = 0; guard < QUEUE_CAPACITY + 2; guard++) {
    Command next;
    if (!queuePop(&next)) {
      s_runState = RunState::STAGING;  // sequence complete
      s_recycling = false;
      s_loopRemaining = 0;
      return;
    }

    if (next.type == CommandType::REPEAT) {
      // param 1 is a no-op (single pass, same as omitting R). param 0
      // means loop forever; anything else is a fixed pass count.
      if (next.param != 1) {
        s_recycling = true;
        s_loopRemaining = next.param;
      }
      continue;  // control-only; look at what follows this tick
    }

    if (next.type == CommandType::SEPARATOR) {
      if (!s_recycling) {
        // Plain fencepost. Nothing more queued -> batch complete.
        if (queueIsEmpty()) {
          s_runState = RunState::STAGING;
          return;
        }
        continue;  // more staged beyond the fencepost — keep looking
      }

      // Recycling: this SEPARATOR marks the end of one pass.
      if (queueIsEmpty()) {
        // Loop body is empty — nothing to repeat. Stop rather than spin.
        s_recycling = false;
        s_loopRemaining = 0;
        s_runState = RunState::STAGING;
        return;
      }
      if (s_loopRemaining != 0 && --s_loopRemaining <= 1) {
        // Final pass ahead — let it drain clean instead of recycling further.
        s_recycling = false;
        s_loopRemaining = 0;
      }
      queuePush(next);  // repush the fencepost; the next pass starts now
      continue;
    }

    // Ordinary staged command.
    if (s_recycling) {
      queuePush(next);  // occupancy stays constant — this can never overflow
    }
    dispatch(next);
    return;
  }
}

// Idle means the motor is stopped AND nothing is going to start it moving on
// the next tick without new bus traffic arriving: either we're STAGING (a
// staged batch just sits there until a G arrives) or we're RUNNING but
// parked inside a still-running W wait (once the wait elapses,
// advanceQueue() would fire on this same tick, so that case is NOT idle).
static bool motorIsIdle(bool waitElapsed) {
  if (s_motor->isBusy()) {
    return false;
  }
  if (s_runState == RunState::RUNNING) {
    return !waitElapsed;
  }
  return true;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

void runnerBegin(Stepper28BYJ& motor, OpticalSwitch& endstop) {
  s_motor = &motor;
  s_endstop = &endstop;
  s_idleSince = millis();
}

void runnerUpdate() {
  s_motor->tick();  // advance one step if a non-blocking move is in progress

  // Computed once per tick and reused below by both the queue-advance check
  // and the idle power-save check, so the two can't disagree about whether a
  // wait has elapsed. (advanceQueue() may set a new, later s_waitUntil later
  // in this same tick — reusing this stale value for the idle check below
  // means that tick is conservatively treated as active, delaying release by
  // at most one tick. Deliberate: the safe direction to be wrong in.)
  const bool waitElapsed = (millis() >= s_waitUntil);

  // When running, advance to the next queued command once the motor is idle
  // and any active wait has elapsed.
  if (s_runState == RunState::RUNNING && !s_motor->isBusy() && waitElapsed) {
    advanceQueue();
  }

  // Position safeguard — see comment on POS_CHECK_CONFIRM_MS above. Runs on
  // every tick, in every run state, whether or not a move is in progress —
  // a genuine homeMotor() call is the only time this is skipped, and that
  // falls out for free since it's blocking and doesn't re-enter
  // runnerUpdate(). liveAngle() (not currentAngle()) is what makes checking
  // while busy possible: it tracks AccelStepper's own step counter in real
  // time, so it's accurate mid-move instead of jumping only on completion.
  if (s_motor->liveAngle() != 0) {
    s_posLostSince = 0;
    s_posCheckArmed = true;  // re-arm once the unit leaves the home window
  } else if (s_needsHome) {
    // P (or a mid-move detection below) already flagged this — nothing more
    // to do until the next home resolves it.
    s_posLostSince = 0;
  } else if (s_endstop->isTriggered()) {
    s_posLostSince = 0;  // position confirmed against the endstop
  } else if (s_motor->isBusy()) {
    // Mismatch while a move is actively stepping. doHome() can't run here —
    // it drives the same AccelStepper object the move is using, so calling
    // it concurrently would corrupt motor state, not just add latency. So
    // recovery is deliberately cheap: flag it and let the current move
    // finish; the next dispatched move homes first via dispatch()'s existing
    // lazy-home check (or an operator can force it sooner with H/N).
    //
    // No debounce here (unlike the idle path below) — at speed the shaft
    // only dwells in the home window for a few milliseconds, well under
    // POS_CHECK_CONFIRM_MS, so debouncing would mean this branch could never
    // fire during a fast pass-through. The cost of an occasional false flag
    // is one extra (blocking) home before the next real move — acceptable
    // for actually catching drift while the unit is in motion.
    if (s_posCheckArmed) {
      s_posCheckArmed = false;
      s_needsHome = true;
    }
  } else if (s_posCheckArmed) {
    // Idle: dwell time in the window is unbounded, so require the mismatch
    // to persist before forcing a blocking home — a single stray read
    // shouldn't be able to trigger one, even with C5's hardware debounce.
    if (s_posLostSince == 0) {
      s_posLostSince = millis();
    } else if ((millis() - s_posLostSince) >= POS_CHECK_CONFIRM_MS) {
      s_posCheckArmed = false;  // a stuck sensor can't spin us in a home loop
      s_posLostSince = 0;
      if (!doHome()) {
        // Couldn't recover — make the next move retry rather than let the
        // unit carry on pretending its angle is good.
        s_needsHome = true;
      }
      s_idleSince = millis();  // homing is activity; restart the power-save clock
      s_idleCoilsReleased = false;
    }
  }

  // Idle power-save (see IDLE_RELEASE_MS and motorIsIdle() above). Any sign
  // of the motor actually moving, or about to on this same tick, pushes
  // s_idleSince forward, so the countdown only ever measures a *continuous*
  // idle stretch.
  if (!motorIsIdle(waitElapsed)) {
    s_idleSince = millis();
    s_idleCoilsReleased = false;
  } else if (!s_idleCoilsReleased && (millis() - s_idleSince) >= IDLE_RELEASE_MS) {
    s_motor->release();
    s_idleCoilsReleased = true;
  }

  // Drain ALL complete messages available this tick, not just one.
  // Without this loop, the hardware RX buffer (64 bytes) overflows when
  // the orchestrator sends many commands in rapid succession, silently
  // dropping later messages before runnerUpdate() is called again.
  Command cmd;
  while ((cmd = commPoll()).type != CommandType::NONE) {
    switch (cmd.type) {
      case CommandType::STOP:
        // Always immediate: halt motor dead and discard any pending sequence
        // (the true emergency stop — unlike C, this does not let an
        // in-progress move finish). Coils stay energised and stop() + tick()
        // commit the angle actually reached, so no re-home is needed
        // afterward, unlike P. The tick() call is what makes this safe to
        // pair with a G in the same drain pass below: without it the flag
        // sits pending until the next runnerUpdate(), and a move dispatched
        // by that G would be silently killed on the following tick().
        s_motor->stop();
        s_motor->tick();
        queueClear();
        s_waitUntil = 0;
        s_runState = RunState::STAGING;
        s_recycling = false;
        s_loopRemaining = 0;
        break;

      case CommandType::CLEAR:
        // Discard the staged queue without touching a move already in
        // progress — it finishes naturally via tick(); only what would come
        // after it is cancelled. Once the motor goes idle, advanceQueue()
        // finds the (now empty) queue and falls back to STAGING on its own.
        queueClear();
        s_waitUntil = 0;
        s_recycling = false;
        s_loopRemaining = 0;
        break;

      case CommandType::CLOCK_NUDGE:
        // Always immediate: adjust OSCCAL ±1 step, persist to USERROW, apply live.
        clockcalNudge(cmd.direction);
        break;

      case CommandType::SET_ID:
        // Always immediate: reprovision this unit's address, persist to
        // EEPROM, apply live. comm.cpp's parser already rejected broadcast.
        commReprovisionAddress((uint8_t)cmd.param);
        break;

      case CommandType::POWER_DOWN:
        // Immediate: halt motor, clear queue, release coils, require re-home.
        // tick() flushes the stop flag now for the same reason STOP does —
        // otherwise it would sit pending across the release() below.
        s_motor->stop();
        s_motor->tick();
        queueClear();
        s_waitUntil = 0;
        s_runState = RunState::STAGING;
        s_recycling = false;
        s_loopRemaining = 0;
        s_needsHome = true;
        s_motor->release();
        break;

      case CommandType::GO:
        if (s_runState == RunState::STAGING) {
          // Push a fencepost to mark the boundary of this batch, then start.
          // The queue is now non-empty (at least the fencepost is in it), so
          // hand off to advanceQueue() to consume any leading R/SEPARATOR
          // control entries and dispatch the first real command — it leaves
          // us back in STAGING on its own if the batch turns out to be empty.
          Command sep = {CommandType::SEPARATOR, 0, false};
          queuePush(sep);
          s_runState = RunState::RUNNING;
          advanceQueue();
        } else {
          // G during RUNNING: push a fencepost for the next batch.
          // The unit keeps draining the current batch; when it hits this SEP
          // it will continue into whatever was queued after it.
          //
          // If a loop is currently recycling, stop recycling now rather than
          // when its own fencepost is next reached: the commands already
          // queued for this pass still drain normally, but its SEPARATOR is
          // then treated as a plain (disposable) one instead of being
          // repushed — so the loop finishes this pass and falls through into
          // the newly-queued batch instead of starting another lap.
          s_recycling = false;
          s_loopRemaining = 0;
          Command sep = {CommandType::SEPARATOR, 0, false};
          queuePush(sep);
        }
        break;

      default:
        // Always accept stageable commands. During RUNNING they queue after
        // the fencepost for the next batch.
        stageMerging(cmd);
        break;
    }
  }
}
