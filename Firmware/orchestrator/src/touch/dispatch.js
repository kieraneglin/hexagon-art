// src/touch/dispatch.js — command policy + batching for the Touch page.
//
// Kept separate from TouchPage.jsx (mirrors src/sim/render.js being split
// out from SimPage.jsx) so the send policy is readable and testable without
// Preact: every tile a pointer crosses gets a relative CW turn, fired
// immediately and targeted at that unit alone (never FF — a swipe must
// never fire every unit's queue).
//
// Touch mode has an explicit "baseline" — the rest angle every unit is
// reset to when the page's enable overlay is dismissed (see TouchPage.jsx).
// It's always 0° (flush with the wall) in practice — TouchPage no longer
// exposes it as a user-chosen value — but it's threaded through as a
// parameter rather than hardcoded below since reset() is what a fresh
// touch session actually needs: forget which units have been touched.
// Every trigger settles back to the baseline, and the turn amplitude
// depends on whether this is the *first* touch a unit has seen since the
// baseline reset (a full revolution) or a repeat touch (a shorter turn
// from wherever it stopped):
//   <ID>X                     stop dead (no-op if already idle; see below)
//   <ID>M+360 | <ID>M+270     fresh touch: full revolution / re-touch: partial turn
//   <ID>A<baseline>           settle at the baseline angle, shortest path
//   <ID>G
//
// Tracking "has this unit been touched since the last reset" is the only
// state this dispatcher keeps — it is not a physical-angle estimate (see
// below for why that used to exist and no longer needs to). It's reset()
// by TouchPage.jsx every time the enable overlay's baseline reset
// succeeds, so a fresh page load — which always shows the overlay, see
// TouchPage.jsx — starts every unit back at "fresh".
//
// This is otherwise stateless by design: the dispatcher tracks nothing
// about any unit's physical angle between triggers. That's only safe
// because of a firmware change — X now commits the angle actually reached
// (stop() + tick() in hexagons/runner.cpp) instead of leaving the
// firmware's tracked angle frozen at its pre-move value, so there is no
// more "desync" for A to trip over. A<baseline> computes its path against
// a tracked angle that is always correct, mid-move interruption included.
//
// What this replaces: an earlier version of this dispatcher kept a
// client-side prediction of each unit's physical angle (reusing the same
// trapezoidal motion profile src/sim/unit.js uses) purely so it could
// estimate where an X-interrupted unit actually stopped, then send just
// enough additional M+ to close the gap back to 0, followed by a staged H
// to re-zero the firmware's tracked angle against the real endstop. All of
// that existed to work around A being untrustworthy after an interrupted
// move — once X stopped losing position, the estimate, the correction,
// and the re-home were all redundant, so the whole predictor is gone.
//
// A fresh touch (unit already at rest at the baseline) gets no-ops for X
// and A<baseline> — stop() has nothing to interrupt, and moveToAngle()
// drops a zero-delta move — so the wire effect is identical to a plain
// M+360 / G. A re-touch stops wherever the unit physically is, spins a
// further 270° from there, then settles back to the baseline by the
// shortest path (which may be a reverse of up to 180° — accepted, since it
// only ever happens on a re-touch, not a fresh one). A unit that was
// power_down()'d (P) homes itself first: dispatch()'s lazy-home check in
// runner.cpp runs before the M, so the sequence self-corrects with no
// special-casing here.
//
// Real physical drift (skipped steps, a hand-nudge) is no longer this
// dispatcher's job either — the firmware's own position safeguard
// (runner.cpp's idle/mid-move liveAngle() vs. endstop check) now catches
// that on its own, on every unit, all the time, not just after a touch.

import { sendCommands } from "../api.js"

const FRESH_TURN_DEG = 360
const RETOUCH_TURN_DEG = 270

/**
 * Batches outgoing POST /api/send calls so a fast swipe across many tiles
 * coalesces into as few requests as the network allows, while a slow drag
 * still sends each tile the instant it's touched (no fixed polling/debounce
 * interval).
 */
export class TouchDispatcher {
  constructor({ onError, baselineAngle = 0 } = {}) {
    this._onError = onError || (() => {})
    this._baselineAngle = baselineAngle
    this._touched = new Set() // unitId -> has been triggered since last reset()
    this._pending = []
    this._pendingSet = new Set()
    this._inFlight = false
  }

  /**
   * Re-arm for a new touch session: forget which units have been touched
   * (so the next trigger on any unit counts as "fresh" again) and adopt a
   * new baseline angle. Called by TouchPage.jsx once the enable overlay
   * has actually reset every unit's physical angle on the bus.
   */
  reset(baselineAngle) {
    this._baselineAngle = baselineAngle
    this._touched.clear()
  }

  /** Queue one trigger for `unitId` (an int) and flush. */
  trigger(unitId) {
    const id = unitId.toString(16).toUpperCase().padStart(2, "0")
    const fresh = !this._touched.has(unitId)
    this._touched.add(unitId)
    if (!this._pendingSet.has(unitId)) {
      this._pendingSet.add(unitId)
      this._pending.push({ id, fresh })
    }
    this._flush()
  }

  _flush() {
    if (this._inFlight || this._pending.length === 0) return

    const batch = this._pending
    this._pending = []
    this._pendingSet = new Set()

    const lines = []
    for (const { id, fresh } of batch) {
      const turnDeg = fresh ? FRESH_TURN_DEG : RETOUCH_TURN_DEG
      lines.push(id + "X")
      lines.push(id + "M+" + turnDeg)
      lines.push(id + "A" + this._baselineAngle)
      lines.push(id + "G")
    }

    this._inFlight = true
    sendCommands(lines.join("\r\n"))
      .catch((err) => this._onError(err))
      .finally(() => {
        this._inFlight = false
        this._flush() // pick up anything queued while this request was out
      })
  }
}
