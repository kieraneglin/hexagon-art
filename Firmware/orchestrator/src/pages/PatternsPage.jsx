import { useState, useEffect } from "preact/hooks"
import { fetchPatterns, runPattern } from "../api.js"

// ---------------------------------------------------------------------------
// PatternCard — renders one pattern with its param inputs
// ---------------------------------------------------------------------------
function PatternCard({ pattern, running, onRun }) {
  const params = pattern.params || []

  // Store raw strings while typing; parse only when sending.
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
    <div class="pattern-card">
      <div class="pattern-card__header">
        <span class="pattern-card__label">{pattern.label}</span>
        {pattern.description && <span class="pattern-card__desc">{pattern.description}</span>}
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
                value={values[p.key]}
                onInput={(e) => handleChange(p.key, e.target.value)}
              />
            </label>
          ))}
        </div>
      )}

      <button
        class={`pattern-card__run${running ? " pattern-card__run--busy" : ""}`}
        disabled={running}
        onClick={() => onRun(pattern.id, parsedValues())}
      >
        {running ? "…" : "▶ Run"}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// PatternsPage
// ---------------------------------------------------------------------------
export function PatternsPage() {
  const [patterns, setPatterns] = useState([])
  const [status, setStatus] = useState("Ready")
  const [loadError, setLoadError] = useState(null)
  const [running, setRunning] = useState(false)

  // ── Load pattern list on mount ────────────────────────────────
  useEffect(() => {
    fetchPatterns()
      .then((data) => setPatterns(data.patterns || []))
      .catch((err) => setLoadError(err.message))
  }, [])

  // ── Run a pattern ─────────────────────────────────────────────
  async function handleRun(id, params) {
    setRunning(true)
    setStatus(`Running: ${id}…`)
    try {
      await runPattern(id, params)
      setStatus(`Sent: ${id}`)
    } catch (err) {
      setStatus(`Error: ${err.message}`)
    } finally {
      setRunning(false)
    }
  }

  // ── Render ────────────────────────────────────────────────────
  // Emergency stop lives in the persistent top bar (App.jsx's TabBar) now,
  // not duplicated here.
  return (
    <div class="page tools-page">
      {/* Pattern list */}
      <div class="tools-section">
        <h2 class="tools-section__heading">Patterns</h2>

        {loadError && <p class="status-text status-text--error">Could not load patterns: {loadError}</p>}
        {!loadError && patterns.length === 0 && <p class="status-text">No patterns available.</p>}

        <div class="pattern-grid">
          {patterns.map((p) => (
            <PatternCard key={p.id} pattern={p} running={running} onRun={handleRun} />
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
