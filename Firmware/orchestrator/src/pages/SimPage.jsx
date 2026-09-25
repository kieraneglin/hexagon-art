import { useEffect, useRef, useState } from "preact/hooks"
import { SimClock } from "../sim/clock.js"
import { STROKE_MM } from "../sim/unit.js"
import { depthColor, hexCorners, tracePath, axialToPixel, fitHexGrid, hitTest, DEPTH_RAMP } from "../sim/render.js"
import { Log } from "../components/Log.jsx"

const RATES = [0.25, 0.5, 1, 2, 4]

// Mirrors src/index.css's custom properties — canvas drawing can't
// reference CSS vars directly, so these are kept in sync by hand.
const COLORS = {
  text: "#c9d1d9",
  textDim: "#8b949e",
  bg: "#0d1117",
  border: "rgba(139, 148, 158, 0.35)",
  green: "#3fb950",
  red: "#f85149",
  amber: "#d29922",
  blue: "#58a6ff",
}

function formatElapsed(ms, originMs) {
  if (originMs == null) return "0.0s"
  return `${((ms - originMs) / 1000).toFixed(1)}s`
}

export function SimPage() {
  const containerRef = useRef(null)
  const canvasRef = useRef(null)
  const clockRef = useRef(null)
  const frameRef = useRef(new Map()) // unitId -> UnitState, latest computed frame
  const geometryRef = useRef(null) // {size, positions: Map(id -> [x,y])}
  const selectedIdRef = useRef(null)
  const overlaysRef = useRef({ id: true, depth: false, angle: false, queue: false })

  const [ready, setReady] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  const [inspector, setInspector] = useState(null)
  const [overlays, setOverlays] = useState(overlaysRef.current)
  const [playing, setPlaying] = useState(true)
  const [liveMode, setLiveMode] = useState(true)
  const [rate, setRate] = useState(1)
  const [playheadMs, setPlayheadMs] = useState(0)
  const [range, setRange] = useState({ min: 0, max: 0 })
  const [resetNotice, setResetNotice] = useState(null)
  const [logEntries, setLogEntries] = useState([])

  useEffect(() => {
    selectedIdRef.current = selectedId
    drawFrame()
  }, [selectedId])

  useEffect(() => {
    overlaysRef.current = overlays
    drawFrame()
  }, [overlays])

  // ── Boot the clock ───────────────────────────────────────────
  useEffect(() => {
    const clock = new SimClock()
    clockRef.current = clock

    const unsubFrame = clock.onFrame((frame, tMs) => {
      frameRef.current = frame
      setPlayheadMs(tMs)
      setRange({ min: clock.earliestMs, max: clock.latestMs })
      drawFrame()
      if (selectedIdRef.current != null) {
        setInspector(frame.get(selectedIdRef.current) || null)
      }
    })
    const unsubReset = clock.onReset((reason) => {
      setResetNotice(reason)
      setTimeout(() => setResetNotice(null), 4000)
    })

    clock.start().then(() => {
      setReady(true)
      recomputeGeometry()
      drawFrame()
    })

    const logTimer = setInterval(() => {
      setLogEntries(clock.allCommands.slice(-150))
    }, 500)

    return () => {
      unsubFrame()
      unsubReset()
      clock.stop()
      clearInterval(logTimer)
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
    const clock = clockRef.current
    if (!canvas || !clock?.layout) return
    const { width: w, height: h } = canvas
    const units = clock.layout.units
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

  function drawFrame() {
    const canvas = canvasRef.current
    const geo = geometryRef.current
    const clock = clockRef.current
    if (!canvas || !geo || !clock?.layout) return
    const ctx = canvas.getContext("2d")
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    const hexSize = geo.size * 0.94 // small visible gap between tiles
    const ov = overlaysRef.current

    for (const u of clock.layout.units) {
      const [cx, cy] = geo.positions.get(u.id)
      const state = frameRef.current.get(u.id)
      const frac = state ? state.depthMm / STROKE_MM : 0
      const corners = hexCorners(cx, cy, hexSize)

      tracePath(ctx, corners)
      ctx.fillStyle = depthColor(frac)
      ctx.fill()

      const isSelected = selectedIdRef.current === u.id
      ctx.lineWidth = isSelected ? 3 : 1
      ctx.strokeStyle = isSelected ? COLORS.blue : COLORS.border
      ctx.stroke()

      if (state?.homing) {
        ctx.save()
        ctx.strokeStyle = COLORS.amber
        ctx.lineWidth = 2
        ctx.setLineDash([4, 3])
        ctx.stroke()
        ctx.restore()
      }

      const labelParts = []
      if (ov.id) labelParts.push(u.id.toString(16).toUpperCase().padStart(2, "0"))
      if (ov.depth && state) labelParts.push(`${state.depthMm.toFixed(0)}mm`)
      if (ov.angle && state) labelParts.push(`${state.angleDeg.toFixed(0)}°`)
      if (ov.queue && state?.queueLength > 0) labelParts.push(`q${state.queueLength}`)

      if (labelParts.length && hexSize > 8) {
        ctx.fillStyle = frac > 0.55 ? COLORS.bg : COLORS.text
        ctx.font = `${Math.max(9, hexSize * 0.26)}px "SF Mono", "Fira Code", monospace`
        ctx.textAlign = "center"
        ctx.textBaseline = "middle"
        labelParts.forEach((line, i) => {
          ctx.fillText(line, cx, cy + (i - (labelParts.length - 1) / 2) * hexSize * 0.32)
        })
      }

      if (state?.moving) {
        ctx.beginPath()
        ctx.arc(cx, cy - hexSize * 0.62, Math.max(2, hexSize * 0.09), 0, Math.PI * 2)
        ctx.fillStyle = COLORS.green
        ctx.fill()
      }
    }
  }

  function handleCanvasClick(e) {
    const canvas = canvasRef.current
    const geo = geometryRef.current
    const clock = clockRef.current
    if (!canvas || !geo || !clock?.layout) return
    const rect = canvas.getBoundingClientRect()
    const dpr = window.devicePixelRatio || 1
    const x = (e.clientX - rect.left) * dpr
    const y = (e.clientY - rect.top) * dpr

    const hitId = hitTest(geo.positions, x, y, geo.size)
    if (hitId != null) {
      setSelectedId(hitId)
      setInspector(frameRef.current.get(hitId) || null)
    } else {
      setSelectedId(null)
      setInspector(null)
    }
  }

  // ── Transport controls ───────────────────────────────────────
  function togglePlay() {
    const clock = clockRef.current
    if (!clock) return
    if (liveMode) {
      clock.scrubTo(playheadMs)
      setLiveMode(false)
      setPlaying(false)
    } else if (playing) {
      clock.pause()
      setPlaying(false)
    } else {
      clock.play()
      setPlaying(true)
    }
  }

  function goLive() {
    clockRef.current?.goLive()
    setLiveMode(true)
    setPlaying(true)
  }

  function handleRateChange(e) {
    const r = Number(e.target.value)
    clockRef.current?.setRate(r)
    setRate(r)
  }

  function handleScrub(e) {
    const ms = Number(e.target.value)
    clockRef.current?.scrubTo(ms)
    setLiveMode(false)
    setPlaying(false)
    setPlayheadMs(ms)
    drawFrame()
  }

  function toggleOverlay(key) {
    setOverlays((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  // ── Render ────────────────────────────────────────────────────
  const displayLog = logEntries.map((e) => ({
    cls: e.parsed.ok ? "sent" : "error",
    text: e.parsed.ok ? `> ${e.raw}` : `> ${e.raw}  (${e.parsed.reason})`,
  }))

  return (
    <div class="sim-page">
      <div class="sim-header">
        <span class="sim-header__title">Hexagon Simulator</span>
        {!ready && <span class="sim-header__notice">Connecting…</span>}
        {resetNotice && <span class="sim-header__notice sim-header__notice--warn">{resetNotice}</span>}
        {liveMode ? (
          <span class="sim-header__badge sim-header__badge--live">● LIVE</span>
        ) : (
          <button class="sim-header__live-btn" onClick={goLive}>
            ▶ Return to live
          </button>
        )}
      </div>

      <div class="sim-body">
        <div class="sim-canvas-wrap" ref={containerRef}>
          <canvas ref={canvasRef} onClick={handleCanvasClick} />
        </div>

        <div class="sim-sidebar">
          <div class="sim-section">
            <h3 class="sim-section__heading">Overlays</h3>
            <label class="sim-checkbox">
              <input type="checkbox" checked={overlays.id} onChange={() => toggleOverlay("id")} />
              Unit ID
            </label>
            <label class="sim-checkbox">
              <input type="checkbox" checked={overlays.depth} onChange={() => toggleOverlay("depth")} />
              Depth (mm)
            </label>
            <label class="sim-checkbox">
              <input type="checkbox" checked={overlays.angle} onChange={() => toggleOverlay("angle")} />
              Angle (°)
            </label>
            <label class="sim-checkbox">
              <input type="checkbox" checked={overlays.queue} onChange={() => toggleOverlay("queue")} />
              Queue depth
            </label>

            <div class="sim-legend">
              <div class="sim-legend__bar" style={`background: linear-gradient(to right, ${DEPTH_RAMP.join(",")})`} />
              <div class="sim-legend__labels">
                <span>0mm</span>
                <span>30mm</span>
              </div>
            </div>
          </div>

          <div class="sim-section sim-section--inspector">
            <h3 class="sim-section__heading">
              Inspector{selectedId != null && ` — ${selectedId.toString(16).toUpperCase().padStart(2, "0")}`}
            </h3>
            {selectedId == null && <p class="status-text">Click a unit to inspect it.</p>}
            {selectedId != null && inspector && (
              <dl class="sim-inspector">
                <dt>State</dt>
                <dd>{inspector.runState}</dd>
                <dt>Angle</dt>
                <dd>{inspector.angleDeg.toFixed(1)}°</dd>
                <dt>Depth</dt>
                <dd>{inspector.depthMm.toFixed(1)} mm</dd>
                <dt>RPM</dt>
                <dd>{inspector.rpm.toFixed(1)}</dd>
                <dt>Queue</dt>
                <dd>{inspector.queueLength} / 64</dd>
                <dt>Moving</dt>
                <dd>{inspector.moving ? "yes" : "no"}</dd>
                <dt>Homing</dt>
                <dd>{inspector.homing ? "yes" : "no"}</dd>
                <dt>Needs Home</dt>
                <dd class={inspector.needsHome ? "sim-inspector__warn" : undefined}>
                  {inspector.needsHome ? "yes — power_down()'d, next move homes first" : "no"}
                </dd>
                {inspector.overflowCount > 0 && (
                  <>
                    <dt>Overflows</dt>
                    <dd class="sim-inspector__warn">{inspector.overflowCount}</dd>
                  </>
                )}
              </dl>
            )}
          </div>

          <div class="sim-section sim-section--log">
            <h3 class="sim-section__heading">Command Log</h3>
            <Log entries={displayLog} />
          </div>
        </div>
      </div>

      <div class="sim-transport">
        <button class="sim-transport__play" onClick={togglePlay} disabled={!ready}>
          {liveMode ? "⏸" : playing ? "⏸" : "▶"}
        </button>
        <select class="sim-transport__rate" value={rate} onChange={handleRateChange} disabled={liveMode}>
          {RATES.map((r) => (
            <option key={r} value={r}>
              {r}×
            </option>
          ))}
        </select>
        <input
          class="sim-transport__scrub"
          type="range"
          min={range.min}
          max={range.max}
          value={playheadMs}
          onInput={handleScrub}
          disabled={!ready || range.max <= range.min}
        />
        <span class="sim-transport__time">{formatElapsed(playheadMs, range.min)}</span>
      </div>
    </div>
  )
}
