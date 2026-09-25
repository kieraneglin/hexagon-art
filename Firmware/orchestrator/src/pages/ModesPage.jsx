import { useState, useEffect } from "preact/hooks"
import { fetchModes, fetchModeStatus, runMode, stopMode } from "../api.js"

// Modes poll their own running-state periodically (unlike Patterns, which
// fire-and-forget) so a mode started from another browser — or left
// running from an earlier session — still shows as running here.
const STATUS_POLL_MS = 3000

// ---------------------------------------------------------------------------
// ModeCard — renders one mode with its param inputs and a start/stop toggle
// ---------------------------------------------------------------------------
function ModeCard({ mode, isRunning, runningParams, runningDetail, busy, onStart, onStop }) {
  const params = mode.params || []

  // Store raw strings while typing; parse only when starting. Mirrors
  // PatternCard's approach (src/pages/PatternsPage.jsx).
  const [values, setValues] = useState(() => Object.fromEntries(params.map((p) => [p.key, String(p.default)])))

  function handleChange(key, raw) {
    setValues((prev) => ({ ...prev, [key]: raw }))
  }

  function parsedValues() {
    return Object.fromEntries(
      params.map((p) => {
        const raw = values[p.key]
        const parsed = p.type === "float" ? parseFloat(raw) : parseInt(raw, 10)
        return [p.key, isNaN(parsed) ? p.default : parsed]
      }),
    )
  }

  return (
    <div class={`pattern-card${isRunning ? " pattern-card--running" : ""}`}>
      <div class="pattern-card__header">
        <span class="pattern-card__label">
          {mode.label}
          {isRunning && (
            <span class="pattern-card__badge">
              Running{runningDetail?.segment ? ` · ${runningDetail.segment}` : ""}
            </span>
          )}
        </span>
        {mode.description && <span class="pattern-card__desc">{mode.description}</span>}
      </div>

      {params.length > 0 && (
        <div class="pattern-card__params">
          {params.map((p) => (
            <label key={p.key} class="pattern-param">
              <span class="pattern-param__label">{p.label}</span>
              <input
                class="pattern-param__input"
                type="text"
                inputmode="decimal"
                value={isRunning ? String(runningParams?.[p.key] ?? values[p.key]) : values[p.key]}
                disabled={isRunning}
                onInput={(e) => handleChange(p.key, e.target.value)}
              />
            </label>
          ))}
        </div>
      )}

      {isRunning ? (
        <button class="pattern-card__run pattern-card__run--stop" disabled={busy} onClick={onStop}>
          {busy ? "…" : "■ Stop"}
        </button>
      ) : (
        <button class="pattern-card__run" disabled={busy} onClick={() => onStart(mode.id, parsedValues())}>
          {busy ? "…" : "▶ Start"}
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ModesPage
// ---------------------------------------------------------------------------
export function ModesPage() {
  const [modes, setModes] = useState([])
  const [status, setStatus] = useState("Ready")
  const [loadError, setLoadError] = useState(null)
  const [busy, setBusy] = useState(false)
  // Mirrors GET /api/modes/status's shape: { running: name|null, params, started_at }.
  // `detail` is only ever present when running === "auto" (see server/modes/auto.py) —
  // { segment, index, phase } naming which sub-mode auto is actually driving right now.
  const [running, setRunning] = useState({ running: null, params: {}, started_at: null, detail: null })

  // ── Load mode list on mount ───────────────────────────────────
  useEffect(() => {
    fetchModes()
      .then((data) => setModes(data.modes || []))
      .catch((err) => setLoadError(err.message))
  }, [])

  // ── Poll running-mode status ──────────────────────────────────
  useEffect(() => {
    let cancelled = false
    function poll() {
      fetchModeStatus()
        .then((data) => {
          if (!cancelled) setRunning(data)
        })
        .catch(() => {})
    }
    poll()
    const id = setInterval(poll, STATUS_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  // ── Start / stop a mode ───────────────────────────────────────
  async function handleStart(id, params) {
    setBusy(true)
    setStatus(`Starting: ${id}…`)
    try {
      await runMode(id, params)
      setRunning({ running: id, params, started_at: Date.now() / 1000, detail: null })
      setStatus(`Running: ${id}`)
    } catch (err) {
      setStatus(`Error: ${err.message}`)
    } finally {
      setBusy(false)
    }
  }

  async function handleStop() {
    setBusy(true)
    setStatus("Stopping…")
    try {
      await stopMode()
      setRunning({ running: null, params: {}, started_at: null, detail: null })
      setStatus("Stopped")
    } catch (err) {
      setStatus(`Error: ${err.message}`)
    } finally {
      setBusy(false)
    }
  }

  // ── Render ────────────────────────────────────────────────────
  return (
    <div class="page tools-page">
      <div class="tools-section">
        <h2 class="tools-section__heading">Modes</h2>

        {loadError && <p class="status-text status-text--error">Could not load modes: {loadError}</p>}
        {!loadError && modes.length === 0 && <p class="status-text">No modes available.</p>}

        <div class="pattern-grid">
          {modes.map((m) => (
            <ModeCard
              key={m.id}
              mode={m}
              isRunning={running.running === m.id}
              runningParams={running.running === m.id ? running.params : null}
              runningDetail={running.running === m.id ? running.detail : null}
              busy={busy}
              onStart={handleStart}
              onStop={handleStop}
            />
          ))}
        </div>
      </div>

      <div class="status-bar">
        <span>{status}</span>
        <span />
      </div>
    </div>
  )
}
