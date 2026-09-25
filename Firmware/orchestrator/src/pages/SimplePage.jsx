import { useState } from "preact/hooks"
import { useUiTheme } from "../theme.js"
import { runMode, runPattern, stopPatterns } from "../api.js"

// The root page — a small, deliberately params-free surface for whoever's
// standing in front of the piece, as opposed to /admin's terminal/patterns/
// modes/tools pages built for developing on it. Four modes, Home, Stop, and
// a link out to Touch and to /admin. Nothing here takes a parameter; every
// button starts its mode/pattern at its MODE_META/PATTERN_META default.
//
// This mode list is a hardcoded subset, not a GET /api/modes fetch: "life"
// is deliberately left off (it's not one of the four the piece cycles
// through unattended in auto_config.py, and isn't meant as a walk-up-and-
// press option), and the blurbs below are written for a visitor rather
// than reused verbatim from MODE_META's more technical descriptions.
const MODES = [
  { id: "wave", label: "Wave", blurb: "A wave sweeps across the piece, edge to edge." },
  { id: "ripple", label: "Ripple", blurb: "Rings radiate outward from the centre." },
  { id: "random", label: "Random", blurb: "Each tile shimmers on its own." },
  { id: "auto", label: "Auto", blurb: "Cycles slowly through all of the above." },
]

export function SimplePage() {
  useUiTheme("calm")

  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)

  function flash(msg) {
    setNotice(msg)
    setTimeout(() => setNotice(null), 3000)
  }

  async function handleMode(id, label) {
    setBusy(true)
    flash(`Starting ${label}…`)
    try {
      await runMode(id, {})
    } catch {
      flash(`${label} failed to start`)
    } finally {
      setBusy(false)
    }
  }

  async function handleHome() {
    setBusy(true)
    flash("Homing…")
    try {
      await runPattern("home_wave")
    } catch {
      flash("Home failed")
    } finally {
      setBusy(false)
    }
  }

  async function handleStop() {
    setBusy(true)
    flash("Stopping…")
    try {
      await stopPatterns()
    } catch {
      flash("Stop failed")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div class="calm-page">
      <div class="calm-header">
        <span class="calm-header__title">Hexagon</span>
        <a class="calm-link" href="/touch">
          Touch
        </a>
      </div>

      <div class="calm-body">
        {notice && <div class="calm-notice">{notice}</div>}

        <div class="calm-grid">
          {MODES.map((m) => (
            <button key={m.id} class="calm-btn" disabled={busy} onClick={() => handleMode(m.id, m.label)}>
              <span class="calm-btn__label">{m.label}</span>
              <span class="calm-btn__blurb">{m.blurb}</span>
            </button>
          ))}
        </div>

        <div class="calm-actions">
          <button class="calm-btn calm-btn--home" disabled={busy} onClick={handleHome}>
            Home
          </button>
          <button class="calm-btn calm-btn--stop" disabled={busy} onClick={handleStop}>
            Stop
          </button>
        </div>
      </div>

      <div class="calm-footer">
        <a class="calm-link calm-link--muted" href="/settings">
          Settings
        </a>
        <a class="calm-link calm-link--muted" href="/admin">
          Advanced ›
        </a>
      </div>
    </div>
  )
}
