// src/sim/clock.js — Polls the command tap, buckets commands per unit via
// bus.js addressing, and drives playback (live/pause/rate/scrub) over the
// JS firmware model in unit.js.
//
// Waitress (server/__init__.py) has no WSGI WebSocket upgrade path, and a
// separate WS-serving process would sit outside both orchestrator.py's
// UART lock and tap.py's in-process buffer — not worth the redesign just
// to avoid polling. So this is deliberately poll-based: GET
// /api/sim/tail?since=N, appending whatever's new to a client-side,
// ever-growing per-unit command log. The 40KB server-side tap buffer
// (tap.py) only needs to survive the gap between two polls, not a whole
// session — see tap.py's header for why that's sized the way it is.
//
// Poll cadence is adaptive, not fixed: it runs at pollMinMs while commands
// are flowing and backs off geometrically (see POLL_BACKOFF_FACTOR) up to
// pollMaxMs after a few consecutive empty responses, snapping back to
// pollMinMs the instant a response carries commands again. Polling also
// pauses outright while the tab is hidden. Do not reinstate a flat
// setInterval here — that was the original design and it polled a Pi Zero
// W at a steady rate regardless of activity or tab visibility.
//
// Playback model: `playheadMs` is expressed in the server's clock (the
// same units as the tap's t_ms, which come from time.monotonic() on the
// server). In live mode it's re-anchored to the server's reported "now"
// on every poll and, between polls, extrapolated forward using the
// browser's own elapsed wall time — cheap and accurate enough since both
// are real-time clocks over sub-second gaps. Scrubbing detaches from live
// mode entirely and moves the playhead directly.

import { parseCommand, targetsFor } from "./bus.js"
import { simulateUnit } from "./unit.js"

const POLL_BACKOFF_FACTOR = 1.5
const POLL_EMPTIES_BEFORE_BACKOFF = 3 // a brief lull shouldn't immediately slow polling

export class SimClock {
  constructor({ pollMinMs = 400, pollMaxMs = 2500, tickIntervalMs = 100 } = {}) {
    this.pollMinMs = pollMinMs
    this.pollMaxMs = pollMaxMs
    this.tickIntervalMs = tickIntervalMs

    this.layout = null
    this.populatedIds = []

    this.bootId = null
    this.sinceSeq = -1
    this.commandsById = new Map() // unitId -> [{seq, tMs, raw, parsed}]
    this.allCommands = [] // flat, time-ordered — powers the global command log view

    this.playing = true
    this.liveMode = true
    this.rate = 1
    this.playheadMs = null // null until the first successful poll

    this._frameListeners = new Set()
    this._resetListeners = new Set()
    this._pollTimer = null
    this._tickTimer = null
    this._lastTickWallMs = null

    this._pollInFlight = false
    this._stopped = true
    this._pollDelayMs = pollMinMs
    this._emptyPolls = 0
    this._onVisibilityChange = () => this._handleVisibilityChange()
  }

  /** Subscribe to computed frames: cb(Map<unitId, UnitState>, playheadMs). */
  onFrame(cb) {
    this._frameListeners.add(cb)
    return () => this._frameListeners.delete(cb)
  }

  /** Subscribe to local-history resets (boot change, or a tap buffer gap). */
  onReset(cb) {
    this._resetListeners.add(cb)
    return () => this._resetListeners.delete(cb)
  }

  async start() {
    const res = await fetch("/api/layout")
    this.layout = await res.json()
    this.populatedIds = this.layout.units.map((u) => u.id)

    this._stopped = false
    document.addEventListener("visibilitychange", this._onVisibilityChange)

    await this._poll()
    this._scheduleNextPoll()

    this._lastTickWallMs = performance.now()
    if (!document.hidden) {
      this._tickTimer = setInterval(() => this._tick(), this.tickIntervalMs)
    }
  }

  stop() {
    this._stopped = true
    document.removeEventListener("visibilitychange", this._onVisibilityChange)
    if (this._pollTimer) clearTimeout(this._pollTimer)
    if (this._tickTimer) clearInterval(this._tickTimer)
    this._pollTimer = null
    this._tickTimer = null
  }

  _scheduleNextPoll() {
    if (this._stopped || document.hidden) return
    this._pollTimer = setTimeout(async () => {
      if (this._pollInFlight) {
        this._scheduleNextPoll()
        return
      }
      this._pollInFlight = true
      try {
        await this._poll()
      } catch (err) {
        console.error("simclock poll failed:", err)
      } finally {
        this._pollInFlight = false
        this._scheduleNextPoll()
      }
    }, this._pollDelayMs)
  }

  _handleVisibilityChange() {
    if (document.hidden) {
      if (this._pollTimer) clearTimeout(this._pollTimer)
      if (this._tickTimer) clearInterval(this._tickTimer)
      this._pollTimer = null
      this._tickTimer = null
      return
    }

    // Tab just became visible again: the wall-clock delta since the last
    // tick would otherwise include however long we were hidden, which
    // would jump the playhead forward in one leap.
    this._lastTickWallMs = performance.now()
    this._tickTimer = setInterval(() => this._tick(), this.tickIntervalMs)

    this._pollDelayMs = this.pollMinMs
    this._emptyPolls = 0
    this._scheduleNextPoll()
  }

  async _poll() {
    const res = await fetch(`/api/sim/tail?since=${this.sinceSeq}`)
    if (!res.ok) return
    const data = await res.json()

    if (this.bootId !== null && data.boot_id !== this.bootId) {
      this._reset("device restarted")
    }
    this.bootId = data.boot_id

    if (data.dropped) {
      this._reset("history gap — tap buffer trimmed before this could poll")
    }

    for (const [seq, tMs, raw] of data.commands) {
      const entry = { seq, tMs, raw, parsed: parseCommand(raw) }
      this.allCommands.push(entry)
      for (const uid of targetsFor(entry.parsed, this.populatedIds)) {
        if (!this.commandsById.has(uid)) this.commandsById.set(uid, [])
        this.commandsById.get(uid).push(entry)
      }
      this.sinceSeq = seq
    }

    if (data.commands.length > 0) {
      this._emptyPolls = 0
      this._pollDelayMs = this.pollMinMs
    } else {
      this._emptyPolls++
      if (this._emptyPolls > POLL_EMPTIES_BEFORE_BACKOFF) {
        this._pollDelayMs = Math.min(this._pollDelayMs * POLL_BACKOFF_FACTOR, this.pollMaxMs)
      }
    }

    if (this.playheadMs === null || this.liveMode) {
      this.playheadMs = data.now_ms
    }
  }

  _reset(reason) {
    this.commandsById = new Map()
    this.allCommands = []
    this.sinceSeq = -1
    this._pollDelayMs = this.pollMinMs
    this._emptyPolls = 0
    for (const cb of this._resetListeners) cb(reason)
  }

  _tick() {
    const nowWall = performance.now()
    const dtWallMs = nowWall - this._lastTickWallMs
    this._lastTickWallMs = nowWall

    if (this.playheadMs === null) return // haven't completed the first poll yet

    if (this.liveMode) {
      this.playheadMs += dtWallMs // re-anchored to server "now" on each poll anyway
    } else if (this.playing) {
      this.playheadMs += dtWallMs * this.rate
      if (this.playheadMs >= this.latestMs) {
        this.playheadMs = this.latestMs
        this.playing = false
      }
    }

    this._render()
  }

  _render() {
    const frame = new Map()
    for (const uid of this.populatedIds) {
      const cmds = this.commandsById.get(uid) || []
      frame.set(uid, simulateUnit(cmds, this.playheadMs))
    }
    for (const cb of this._frameListeners) cb(frame, this.playheadMs)
  }

  // --- transport controls ---

  play() {
    this.playing = true
  }

  pause() {
    this.playing = false
  }

  setRate(rate) {
    this.rate = rate
  }

  /** Resume tracking the live edge (undoes scrubTo()). */
  goLive() {
    this.liveMode = true
    this.playing = true
  }

  /** Jump to an arbitrary past instant and pause there. */
  scrubTo(ms) {
    this.liveMode = false
    this.playing = false
    this.playheadMs = ms
    this._render()
  }

  get earliestMs() {
    return this.allCommands.length ? this.allCommands[0].tMs : (this.playheadMs ?? 0)
  }

  get latestMs() {
    return this.allCommands.length ? this.allCommands[this.allCommands.length - 1].tMs : (this.playheadMs ?? 0)
  }
}
