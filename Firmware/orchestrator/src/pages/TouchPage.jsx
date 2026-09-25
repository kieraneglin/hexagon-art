import { useEffect, useRef, useState } from "preact/hooks"
import {
  depthColor,
  hexCorners,
  tracePath,
  axialToPixel,
  fitHexGrid,
  hitTest,
  CALM_DEPTH_RAMP,
  CALM_STROKE,
} from "../sim/render.js"
import { TouchDispatcher } from "../touch/dispatch.js"
import { sendCommands, stopPatterns, fetchSettings } from "../api.js"
import { useUiTheme } from "../theme.js"

// How long a tile's touch-flash takes to fade back to idle, in ms. Purely
// cosmetic — decoupled from the actual motion timing sent by
// src/touch/dispatch.js, since the flash is an input affordance ("you
// touched this"), not a depth readout.
const FLASH_MS = 600

// Idle fill: the light end of CALM_DEPTH_RAMP, so an untouched tile reads
// as flush with the paper background — this page never polls
// /api/sim/tail or tracks real unit state.
const IDLE_FRAC = 0

// Rest angle every unit is reset to when the enable overlay is dismissed —
// flush with the wall (see AGENTS.md's depth formula: 15*(1-cos(angle)),
// 0 = lowest point of stroke), so a freshly-enabled piece reads as fully
// settled rather than sitting at some arbitrary mid-stroke position.
const BASELINE_ANGLE = 0

export function TouchPage() {
  useUiTheme("calm")

  const containerRef = useRef(null)
  const canvasRef = useRef(null)
  const layoutRef = useRef(null) // { units: [{id, ring, q, r}, ...] }
  const geometryRef = useRef(null) // { size, positions: Map(id -> [x, y]) }
  const flashesRef = useRef(new Map()) // unitId -> flash-start timestamp (performance.now())
  const dispatcherRef = useRef(null)
  const pointerUnitsRef = useRef(new Map()) // pointerId -> last-hit unitId
  const rafRef = useRef(null)

  const [ready, setReady] = useState(false)
  const [notice, setNotice] = useState(null)

  // Touch mode always starts gated behind the enable overlay — no
  // persisted enabled/disabled state, so a fresh page load (or a reload
  // after leaving mid-session) always re-presents it rather than trusting
  // stale assumptions about where the real units physically are.
  const [enabled, setEnabled] = useState(false)
  const [enabling, setEnabling] = useState(false)
  const [enableError, setEnableError] = useState(null)

  // ── Load layout + dispatcher once ──────────────────────────────
  useEffect(() => {
    let cancelled = false
    dispatcherRef.current = new TouchDispatcher({
      onError: () => {
        if (cancelled) return
        setNotice("Send failed — check connection")
        setTimeout(() => setNotice(null), 3000)
      },
    })

    fetch("/api/layout")
      .then((res) => res.json())
      .then((layout) => {
        if (cancelled) return
        layoutRef.current = layout
        setReady(true)
        recomputeGeometry()
        drawFrame()
      })
      .catch(() => {
        if (!cancelled) setNotice("Failed to load layout")
      })

    return () => {
      cancelled = true
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [])

  // ── Responsive canvas sizing ─────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return

    function resize() {
      const rect = container.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1
      canvas.width = Math.max(1, Math.round(rect.width * dpr))
      canvas.height = Math.max(1, Math.round(rect.height * dpr))
      canvas.style.width = rect.width + "px"
      canvas.style.height = rect.height + "px"
      recomputeGeometry()
      drawFrame()
    }

    const ro = new ResizeObserver(resize)
    ro.observe(container)
    resize()
    return () => ro.disconnect()
  }, [ready])

  function recomputeGeometry() {
    const canvas = canvasRef.current
    const layout = layoutRef.current
    if (!canvas || !layout) return
    const { width: w, height: h } = canvas
    const units = layout.units
    const { size, offsetX, offsetY } = fitHexGrid(
      units.map((u) => [u.q, u.r]),
      w,
      h,
    )
    const positions = new Map()
    for (const u of units) {
      const [x, y] = axialToPixel(u.q, u.r, size)
      positions.set(u.id, [x + offsetX, y + offsetY])
    }
    geometryRef.current = { size, positions }
  }

  // ── Drawing ───────────────────────────────────────────────────
  function drawFrame() {
    const canvas = canvasRef.current
    const geo = geometryRef.current
    const layout = layoutRef.current
    if (!canvas || !geo || !layout) return
    const ctx = canvas.getContext("2d")
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    const hexSize = geo.size * 0.94 // small visible gap between tiles, matches SimPage
    const now = performance.now()

    for (const u of layout.units) {
      const [cx, cy] = geo.positions.get(u.id)
      const corners = hexCorners(cx, cy, hexSize)

      const flashStart = flashesRef.current.get(u.id)
      let frac = IDLE_FRAC
      if (flashStart != null) {
        const t = (now - flashStart) / FLASH_MS
        if (t >= 1) {
          flashesRef.current.delete(u.id)
        } else {
          // Ease-out decay from full brightness back to idle.
          frac = IDLE_FRAC + (1 - IDLE_FRAC) * (1 - t) * (1 - t)
        }
      }

      tracePath(ctx, corners)
      ctx.fillStyle = depthColor(frac, CALM_DEPTH_RAMP)
      ctx.fill()
      ctx.lineWidth = 1
      ctx.strokeStyle = CALM_STROKE
      ctx.stroke()
    }

    if (flashesRef.current.size > 0) {
      rafRef.current = requestAnimationFrame(drawFrame)
    } else {
      rafRef.current = null
    }
  }

  function ensureAnimating() {
    if (rafRef.current == null) {
      rafRef.current = requestAnimationFrame(drawFrame)
    }
  }

  // ── Enable overlay ──────────────────────────────────────────────
  // Dismissing the overlay is the one point where this page reaches past
  // "fire a relative turn on the tile you touched" and actually commands
  // the whole piece: stop everything (including any running mode), then
  // drive every unit flush with the wall (BASELINE_ANGLE, 0°) so the
  // dispatcher's fresh/re-touch bookkeeping (src/touch/dispatch.js) starts
  // from a physical state that actually matches it. FFN comes first for
  // the same reason server/routes/patterns.py prepends it to every pattern
  // run — a unit that was power_down()'d needs a real home before an
  // absolute move means anything.
  async function handleEnable() {
    setEnabling(true)
    setEnableError(null)
    try {
      await stopPatterns()

      // Pick up the configured default speed (server/settings.py) before
      // the reset broadcast below, so the turns this session fires
      // (src/touch/dispatch.js) run at whatever /admin/tools has set
      // rather than whatever speed happened to be last on the bus.
      // Best-effort: a failed fetch just leaves the last-set speed in
      // place rather than blocking touch mode entirely.
      let speedCmd = ""
      try {
        const { default_rpm } = await fetchSettings()
        speedCmd = `FFS${Math.round(default_rpm * 10)};`
      } catch {
        // ignore — see comment above
      }

      await sendCommands(`${speedCmd}FFN;FFA${BASELINE_ANGLE};FFG`)
      dispatcherRef.current?.reset(BASELINE_ANGLE)
      pointerUnitsRef.current.clear()
      setEnabled(true)
    } catch (err) {
      setEnableError(err.message || "Failed to enable touch mode")
    } finally {
      setEnabling(false)
    }
  }

  // ── Pointer handling ─────────────────────────────────────────
  function canvasPoint(e) {
    const canvas = canvasRef.current
    const rect = canvas.getBoundingClientRect()
    const dpr = window.devicePixelRatio || 1
    return [(e.clientX - rect.left) * dpr, (e.clientY - rect.top) * dpr]
  }

  function triggerUnit(unitId) {
    flashesRef.current.set(unitId, performance.now())
    ensureAnimating()
    dispatcherRef.current?.trigger(unitId)
  }

  /**
   * Hit-test one point and fire if it lands on a *different* tile than the
   * last one this pointer touched. Called once per raw event plus once per
   * coalesced sub-frame sample, and interpolated between consecutive points
   * — see handlePointerMove — so a fast swipe can't skip tiles between
   * animation frames.
   */
  function visitPoint(pointerId, x, y) {
    if (!enabled) return
    const geo = geometryRef.current
    if (!geo) return
    const hitId = hitTest(geo.positions, x, y, geo.size)
    if (hitId == null) return
    if (pointerUnitsRef.current.get(pointerId) === hitId) return
    pointerUnitsRef.current.set(pointerId, hitId)
    triggerUnit(hitId)
  }

  function handlePointerDown(e) {
    e.target.setPointerCapture?.(e.pointerId)
    const [x, y] = canvasPoint(e)
    visitPoint(e.pointerId, x, y)
  }

  function handlePointerMove(e) {
    if (!pointerUnitsRef.current.has(e.pointerId) && e.buttons === 0 && e.pointerType === "mouse") {
      // Ignore hover-only mouse movement; only drags (button down) or
      // touch/pen contacts should trigger tiles.
      return
    }

    const geo = geometryRef.current
    const dpr = window.devicePixelRatio || 1
    const rect = canvasRef.current.getBoundingClientRect()
    const events = e.getCoalescedEvents?.() ?? [e]

    let [prevX, prevY] = geo
      ? [(events[0].clientX - rect.left) * dpr, (events[0].clientY - rect.top) * dpr]
      : [null, null]

    for (const ev of events) {
      const x = (ev.clientX - rect.left) * dpr
      const y = (ev.clientY - rect.top) * dpr

      // Line-sample between consecutive points at roughly half-hex spacing
      // so a fast swipe that jumps several tiles between samples still
      // triggers every tile it crossed, not just the endpoints.
      if (geo) {
        const dist = Math.hypot(x - prevX, y - prevY)
        const step = Math.max(1, geo.size * 0.5)
        const steps = Math.max(1, Math.ceil(dist / step))
        for (let i = 1; i <= steps; i++) {
          const t = i / steps
          visitPoint(e.pointerId, prevX + (x - prevX) * t, prevY + (y - prevY) * t)
        }
      } else {
        visitPoint(e.pointerId, x, y)
      }
      prevX = x
      prevY = y
    }
  }

  function handlePointerEnd(e) {
    pointerUnitsRef.current.delete(e.pointerId)
  }

  // ── Render ────────────────────────────────────────────────────
  // Touch has no persistent chrome of its own to lean on (it's not part of
  // the admin TabBar) — the enable overlay is the only thing that stops
  // whatever's running before a drag can fight it, and "‹ Back" is the way
  // out to the root page.
  return (
    <div class="touch-page">
      <div class="touch-header">
        <a href="/" class="touch-header__back-link" title="Back to the simple UI">
          ‹ Back
        </a>
        <span class="touch-header__title">Touch</span>
        {!ready && <span class="touch-header__notice">Loading…</span>}
        {notice && <span class="touch-header__notice">{notice}</span>}
      </div>

      <div class="touch-canvas-wrap" ref={containerRef}>
        <canvas
          ref={canvasRef}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerEnd}
          onPointerCancel={handlePointerEnd}
        />

        {!enabled && (
          <div class="touch-overlay">
            <div class="touch-overlay__panel">
              <p class="touch-overlay__title">Ready when you are</p>
              <p class="touch-overlay__desc">
                Starting touch mode stops whatever is running and settles every tile flush with the wall.
              </p>
              {enableError && <p class="touch-overlay__error">{enableError}</p>}
              <button class="touch-overlay__button" disabled={!ready || enabling} onClick={handleEnable}>
                {enabling ? "Starting…" : "Begin"}
              </button>
            </div>
          </div>
        )}
      </div>

      <div class="touch-legend">
        <div class="touch-legend__bar" style={`background: linear-gradient(to right, ${CALM_DEPTH_RAMP.join(",")})`} />
        <span class="touch-legend__label">
          Drag or swipe across tiles — each turns and settles back flush with the wall.
        </span>
      </div>
    </div>
  )
}
