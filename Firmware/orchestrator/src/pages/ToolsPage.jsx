import { useEffect, useRef, useState } from "preact/hooks"
import {
  sendCommands,
  stopPatterns,
  fetchNetwork,
  addNetwork,
  forgetNetwork,
  fetchSettings,
  saveSettings,
} from "../api.js"
import { Log } from "../components/Log.jsx"

// ---------------------------------------------------------------------------
// Hex ID helpers — shared by the checker's start/end fields and the
// reprovision tool's current/new fields.
// ---------------------------------------------------------------------------
const MIN_ID = 0x01
const MAX_ID = 0xfe // 0xFF is the broadcast address, never an individual unit
const BROADCAST_ID = "FF"

/** Sanitise free-typed input to hex chars, uppercased, capped at 2 chars. */
function sanitiseHexInput(raw) {
  return raw
    .toUpperCase()
    .replace(/[^0-9A-F]/g, "")
    .slice(0, 2)
}

/** Caret-preserving onInput handler, same idiom as the old ToolsPage. */
function handleHexInput(e, setter) {
  const pos = e.target.selectionStart
  setter(sanitiseHexInput(e.target.value))
  requestAnimationFrame(() => e.target.setSelectionRange(pos, pos))
}

/** Parse a 2-char hex string to an int, or null if empty/invalid. */
function parseId(hex) {
  if (!/^[0-9A-F]{1,2}$/.test(hex)) {
    return null
  }
  return parseInt(hex, 16)
}

/** Format an int as a 2-char uppercase hex string (same expression as
 * src/touch/dispatch.js and src/pages/SimPage.jsx). */
function toHex(n) {
  return n.toString(16).toUpperCase().padStart(2, "0")
}

// ---------------------------------------------------------------------------
// Unit ID Checker constants
// ---------------------------------------------------------------------------
const MIN_INTERVAL_MS = 100
const TURN_DEGREES = 360

// ---------------------------------------------------------------------------
// ToolsPage
// ---------------------------------------------------------------------------
// Mirrors server/settings.py's DEFAULT_RPM — shown before the initial
// GET /api/settings resolves, and offered as the "Reset" target.
const DEFAULT_RPM = 14.5

export function ToolsPage() {
  // ── Unit ID Checker state ───────────────────────────────────────
  const [startId, setStartId] = useState("01")
  const [endId, setEndId] = useState("3D")
  const [intervalMs, setIntervalMs] = useState("1000")
  const [running, setRunning] = useState(false)
  const [currentId, setCurrentId] = useState(null)
  const [progress, setProgress] = useState(null) // { index, total }
  const [logEntries, setLogEntries] = useState([])
  const [status, setStatus] = useState("Ready")

  const timerRef = useRef(null)
  const endRef = useRef(MAX_ID)
  const intervalRef = useRef(1000)

  // Kill the sweep if the page unmounts mid-run — this loop is entirely
  // browser-side, so nothing else will stop it.
  useEffect(() => {
    return () => clearTimeout(timerRef.current)
  }, [])

  function appendLog(hex, index, total) {
    setLogEntries((prev) => [...prev, { cls: "info", text: `→ ${hex}   (${index + 1} / ${total})` }])
  }

  function tick(id, startVal, total) {
    if (id > endRef.current) {
      setRunning(false)
      setCurrentId(null)
      setStatus(`Sweep complete — ${toHex(startVal)}–${toHex(endRef.current)}.`)
      return
    }

    const hex = toHex(id)
    const index = id - startVal
    setCurrentId(hex)
    setProgress({ index, total })
    appendLog(hex, index, total)

    // M+360 returns the unit to where it started, so a sweep is
    // position-neutral and needs no homing pass. A unit that was P'd
    // re-homes itself before the move (../hexagons/runner.cpp dispatch()),
    // so it just turns a few seconds late rather than silently doing
    // nothing.
    sendCommands(`${hex}M+${TURN_DEGREES}\r\n${hex}G`).catch(() => {})

    timerRef.current = setTimeout(() => tick(id + 1, startVal, total), intervalRef.current)
  }

  function startSweep() {
    const start = parseId(startId)
    const end = parseId(endId)
    const interval = parseInt(intervalMs, 10)

    if (start === null || end === null || start < MIN_ID || end > MAX_ID) {
      setStatus(`Start/End must be between ${toHex(MIN_ID)} and ${toHex(MAX_ID)}.`)
      return
    }
    if (start > end) {
      setStatus("Start must be ≤ End.")
      return
    }
    if (!Number.isFinite(interval) || interval < MIN_INTERVAL_MS) {
      setStatus(`Interval must be at least ${MIN_INTERVAL_MS} ms.`)
      return
    }

    endRef.current = end
    intervalRef.current = interval
    setLogEntries([])
    setRunning(true)
    setStatus(`Sweeping ${toHex(start)}–${toHex(end)} at ${interval} ms/unit…`)
    tick(start, start, end - start + 1)
  }

  function stopSweep() {
    clearTimeout(timerRef.current)
    setRunning(false)
    setCurrentId(null)
    setStatus("■ Sweep stopped.")
    stopPatterns().catch(() => setStatus("Stop failed."))
  }

  // ── Set Unit ID state ────────────────────────────────────────────
  const [reprovCurrent, setReprovCurrent] = useState("")
  const [reprovNew, setReprovNew] = useState("")

  function setUnitId() {
    const current = parseId(reprovCurrent)
    const next = parseId(reprovNew)

    if (current === null || next === null || current < MIN_ID || current > MAX_ID || next < MIN_ID || next > MAX_ID) {
      setStatus(`Current/New ID must be between ${toHex(MIN_ID)} and ${toHex(MAX_ID)} (not ${BROADCAST_ID}).`)
      return
    }
    if (current === next) {
      setStatus("New ID must differ from Current ID.")
      return
    }

    const curHex = toHex(current)
    const newHex = toHex(next)
    if (
      !window.confirm(
        `Reassign unit ${curHex} to address ${newHex}?\n\n` +
          `This reprovisions EVERY unit currently answering to ${curHex} — if two units ` +
          `were accidentally left on the same address, both will change.`,
      )
    ) {
      return
    }

    sendCommands(`${curHex}I${newHex}`)
      .then(() => setStatus(`Sent: ${curHex} → ${newHex}.`))
      .catch(() => setStatus("Set ID failed."))
  }

  // ── WiFi Network state ───────────────────────────────────────────
  // available starts null (not yet loaded) so the section renders nothing
  // committal — neither the "unavailable" note nor the form — until the
  // initial GET /api/network below resolves.
  const [netAvailable, setNetAvailable] = useState(null)
  const [netSaved, setNetSaved] = useState([])
  const [netScan, setNetScan] = useState([])
  const [netLoading, setNetLoading] = useState(false)
  const [wifiSsid, setWifiSsid] = useState("")
  const [wifiPassword, setWifiPassword] = useState("")
  const [wifiPriority, setWifiPriority] = useState("100")
  const [wifiSubmitting, setWifiSubmitting] = useState(false)

  function loadNetwork() {
    setNetLoading(true)
    fetchNetwork()
      .then((data) => {
        setNetAvailable(data.available)
        setNetSaved(data.saved || [])
        setNetScan(data.scan || [])
        if (data.error) setStatus(data.error)
      })
      .catch(() => setStatus("Failed to load network info."))
      .finally(() => setNetLoading(false))
  }

  useEffect(() => {
    loadNetwork()
  }, [])

  function handleAddNetwork() {
    const ssid = wifiSsid.trim()
    if (!ssid) {
      setStatus("SSID is required.")
      return
    }
    if (wifiPassword && wifiPassword.length < 8) {
      setStatus("Password must be at least 8 characters (or empty for an open network).")
      return
    }
    const priority = parseInt(wifiPriority, 10)
    if (!Number.isFinite(priority) || priority < 0 || priority > 999) {
      setStatus("Priority must be between 0 and 999.")
      return
    }
    if (
      !window.confirm(
        `Add "${ssid}" and reboot the piece now?\n\n` +
          `It'll go offline for about 90 seconds while it restarts and tries the new network.`,
      )
    ) {
      return
    }

    setWifiSubmitting(true)
    addNetwork(ssid, wifiPassword, priority)
      .then(() => setStatus(`Saved "${ssid}" — rebooting, give it ~90s and rejoin the new network.`))
      .catch((err) => setStatus(err.message || "Add network failed."))
      .finally(() => setWifiSubmitting(false))
  }

  function handleForgetNetwork(ssid) {
    if (!window.confirm(`Forget saved network "${ssid}"?`)) {
      return
    }
    forgetNetwork(ssid)
      .then(() => {
        setStatus(`Forgot "${ssid}".`)
        loadNetwork()
      })
      .catch((err) => setStatus(err.message || "Forget failed."))
  }

  // ── Motor Speed state ────────────────────────────────────────────
  const [rpmRange, setRpmRange] = useState(null) // {min_rpm, max_rpm} once loaded
  const [rpmValue, setRpmValue] = useState(String(DEFAULT_RPM))
  const [rpmSaving, setRpmSaving] = useState(false)

  function loadSpeed() {
    fetchSettings()
      .then((data) => {
        setRpmRange({ min_rpm: data.min_rpm, max_rpm: data.max_rpm })
        setRpmValue(String(data.default_rpm))
      })
      .catch(() => setStatus("Failed to load motor speed."))
  }

  useEffect(() => {
    loadSpeed()
  }, [])

  function handleSaveSpeed() {
    if (!rpmRange) return
    const rpm = parseFloat(rpmValue)
    if (!Number.isFinite(rpm) || rpm < rpmRange.min_rpm || rpm > rpmRange.max_rpm) {
      setStatus(`Speed must be between ${rpmRange.min_rpm} and ${rpmRange.max_rpm} RPM.`)
      return
    }
    setRpmSaving(true)
    saveSettings(rpm)
      .then((data) => {
        setRpmValue(String(data.default_rpm))
        setStatus(`Motor speed set to ${data.default_rpm} RPM.`)
      })
      .catch((err) => setStatus(err.message || "Save failed."))
      .finally(() => setRpmSaving(false))
  }

  function handleResetSpeed() {
    setRpmValue(String(DEFAULT_RPM))
    setRpmSaving(true)
    saveSettings(DEFAULT_RPM)
      .then((data) => setStatus(`Motor speed reset to ${data.default_rpm} RPM.`))
      .catch((err) => setStatus(err.message || "Reset failed."))
      .finally(() => setRpmSaving(false))
  }

  // ── Render ─────────────────────────────────────────────────────
  return (
    <div class="page tools-page">
      {/* Motor Speed */}
      <div class="tools-section">
        <h2 class="tools-section__heading">Motor Speed</h2>
        <p class="tools-section__hint">
          Default speed (RPM) every unit runs at — modes, patterns, and the Home button all pick this up. The firmware
          default, {DEFAULT_RPM} RPM, is a resonance-minimising value for this motor/gearing — slower isn't cooler:
          since the drive is constant-voltage, a slower move draws the same current for longer, so it burns more energy
          per move, not less.
        </p>

        <div class="target-row">
          <label for="rpm-value">Speed (RPM)</label>
          <input
            id="rpm-value"
            class="target-row__input"
            type="number"
            min={rpmRange?.min_rpm}
            max={rpmRange?.max_rpm}
            step={0.5}
            value={rpmValue}
            disabled={!rpmRange || rpmSaving}
            onInput={(e) => setRpmValue(e.target.value)}
          />
          {rpmRange && (
            <span class="tools-section__hint">
              {rpmRange.min_rpm}–{rpmRange.max_rpm} RPM
            </span>
          )}
        </div>

        <div class="tool-btn-grid">
          <button class="tool-btn tool-btn--green" onClick={handleSaveSpeed} disabled={!rpmRange || rpmSaving}>
            <span class="tool-btn__title">{rpmSaving ? "Saving…" : "Save"}</span>
            <span class="tool-btn__desc">Applies immediately, even mid-mode</span>
          </button>
          <button class="tool-btn" onClick={handleResetSpeed} disabled={!rpmRange || rpmSaving}>
            <span class="tool-btn__title">Reset</span>
            <span class="tool-btn__desc">Back to the firmware default, {DEFAULT_RPM} RPM</span>
          </button>
        </div>
      </div>

      {/* Unit ID Checker */}
      <div class="tools-section">
        <h2 class="tools-section__heading">Unit ID Checker</h2>
        <p class="tools-section__hint">
          Sequentially turns each unit in range 360°, one at a time, so you can watch the wall and match the tile that
          moves to the ID shown below.
        </p>

        <div class="target-row">
          <label for="checker-start">Start</label>
          <input
            id="checker-start"
            class="target-row__input"
            type="text"
            value={startId}
            maxLength={2}
            disabled={running}
            onInput={(e) => handleHexInput(e, setStartId)}
            autocomplete="off"
            spellcheck={false}
          />
          <label for="checker-end">End</label>
          <input
            id="checker-end"
            class="target-row__input"
            type="text"
            value={endId}
            maxLength={2}
            disabled={running}
            onInput={(e) => handleHexInput(e, setEndId)}
            autocomplete="off"
            spellcheck={false}
          />
          <label for="checker-interval">Interval (ms)</label>
          <input
            id="checker-interval"
            class="target-row__input target-row__input--wide"
            type="number"
            min={MIN_INTERVAL_MS}
            step={100}
            value={intervalMs}
            disabled={running}
            onInput={(e) => setIntervalMs(e.target.value)}
          />
        </div>

        <div class="tool-btn-grid">
          {!running ? (
            <button class="tool-btn tool-btn--green" onClick={startSweep}>
              <span class="tool-btn__title">▶ Go</span>
              <span class="tool-btn__desc">
                Sweep {startId || "–"}–{endId || "–"}, one 360° turn per unit
              </span>
            </button>
          ) : (
            <button class="tool-btn tool-btn--red tool-btn--running" onClick={stopSweep}>
              <span class="tool-btn__title">■ Stop</span>
              <span class="tool-btn__desc">Halt the sweep and stop all units</span>
            </button>
          )}
        </div>

        <div class="checker-readout">
          <span class="checker-readout__id">{currentId ?? "––"}</span>
          {progress && (
            <span class="checker-readout__progress">
              {progress.index + 1} / {progress.total}
            </span>
          )}
        </div>

        <div class="checker-log">
          <Log entries={logEntries} />
        </div>
      </div>

      {/* Set Unit ID */}
      <div class="tools-section tools-section--danger">
        <h2 class="tools-section__heading">Set Unit ID</h2>
        <p class="tools-section__hint">
          Reprovisions a unit's address (sends <code>&lt;Current&gt;I&lt;New&gt;</code>), persisted to EEPROM. Broadcast
          (FF) is not a valid target for either field.
        </p>

        <div class="target-row">
          <label for="reprov-current">Current ID</label>
          <input
            id="reprov-current"
            class="target-row__input"
            type="text"
            value={reprovCurrent}
            maxLength={2}
            disabled={running}
            onInput={(e) => handleHexInput(e, setReprovCurrent)}
            autocomplete="off"
            spellcheck={false}
          />
          <label for="reprov-new">New ID</label>
          <input
            id="reprov-new"
            class="target-row__input"
            type="text"
            value={reprovNew}
            maxLength={2}
            disabled={running}
            onInput={(e) => handleHexInput(e, setReprovNew)}
            autocomplete="off"
            spellcheck={false}
          />
        </div>

        <div class="tool-btn-grid">
          <button class="tool-btn tool-btn--red" onClick={setUnitId} disabled={running}>
            <span class="tool-btn__title">Set ID</span>
            <span class="tool-btn__desc">Reassign the current unit to a new address</span>
          </button>
        </div>
      </div>

      {/* WiFi Network */}
      <div class="tools-section tools-section--danger">
        <h2 class="tools-section__heading">WiFi Network</h2>
        <p class="tools-section__hint">
          Mainly for when the piece has fallen back to its own open <code>hexagon-ap</code> hotspot and needs pointing
          at a real network. Saves a NetworkManager profile and reboots so it's tried at the next boot — not written
          back to <code>boot_credentials.json</code> on your dev machine, so add it there too if it should survive a
          re-image.
        </p>

        {netAvailable === false && (
          <p class="tools-section__hint">
            Network config unavailable on this host — the on-Pi <code>hexagons-netcfg</code> helper isn't installed
            (expected off the Pi; on the Pi, re-run <code>./deploy.sh --bootstrap</code>).
          </p>
        )}

        {netAvailable && (
          <>
            <div class="target-row">
              <label for="wifi-ssid">SSID</label>
              <input
                id="wifi-ssid"
                class="target-row__input target-row__input--text"
                type="text"
                list="wifi-scan-list"
                value={wifiSsid}
                maxLength={32}
                disabled={wifiSubmitting}
                onInput={(e) => setWifiSsid(e.target.value)}
                autocomplete="off"
                spellcheck={false}
              />
              <datalist id="wifi-scan-list">
                {netScan.map((n) => (
                  <option key={n.ssid} value={n.ssid} />
                ))}
              </datalist>

              <label for="wifi-password">Password</label>
              <input
                id="wifi-password"
                class="target-row__input target-row__input--text"
                type="password"
                value={wifiPassword}
                disabled={wifiSubmitting}
                onInput={(e) => setWifiPassword(e.target.value)}
                autocomplete="off"
                placeholder="(open network)"
              />

              <label for="wifi-priority">Priority</label>
              <input
                id="wifi-priority"
                class="target-row__input"
                type="number"
                min={0}
                max={999}
                step={10}
                value={wifiPriority}
                disabled={wifiSubmitting}
                onInput={(e) => setWifiPriority(e.target.value)}
              />
            </div>

            <div class="tool-btn-grid">
              <button class="tool-btn tool-btn--red" onClick={handleAddNetwork} disabled={wifiSubmitting}>
                <span class="tool-btn__title">{wifiSubmitting ? "Saving…" : "Add & Reboot"}</span>
                <span class="tool-btn__desc">Saves the profile, then reboots the piece to try it</span>
              </button>
              <button class="tool-btn" onClick={loadNetwork} disabled={netLoading}>
                <span class="tool-btn__title">{netLoading ? "Scanning…" : "Rescan"}</span>
                <span class="tool-btn__desc">Refresh the SSID suggestions above</span>
              </button>
            </div>

            {netSaved.length > 0 && (
              <div class="wifi-list">
                {netSaved.map((n) => (
                  <div key={n.ssid} class="wifi-list__row">
                    <span class="wifi-list__name">
                      {n.ssid}
                      {n.active && (
                        <span class="wifi-list__active" title="Currently connected">
                          {" "}
                          ●
                        </span>
                      )}
                    </span>
                    <span class="wifi-list__meta">priority {n.priority}</span>
                    <button class="wifi-list__forget" onClick={() => handleForgetNetwork(n.ssid)}>
                      Forget
                    </button>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      <div class="status-bar">
        <span>{status}</span>
        <span />
      </div>
    </div>
  )
}
