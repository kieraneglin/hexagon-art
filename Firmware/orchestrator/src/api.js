// ---------------------------------------------------------------------------
// api.js — thin wrappers around the orchestrator's HTTP API
// ---------------------------------------------------------------------------

/**
 * Normalise a raw command string:
 *   - split on semicolons or literal \r\n escape sequences
 *   - trim + uppercase each part
 *   - drop empties
 * Returns an array of clean command strings.
 */
export function normaliseCommands(raw) {
  return raw
    .replace(/\\r\\n/g, ";")
    .split(";")
    .map((s) => s.trim().toUpperCase())
    .filter((s) => s.length > 0)
}

/**
 * POST one or more commands to /api/send.
 * `raw` may contain semicolons or literal \r\n as separators.
 *
 * Returns { commands: string[], sent: number } on success,
 * or throws an Error with a human-readable message.
 */
export async function sendCommands(raw) {
  if (!raw) {
    return { sent: 0, commands: [] }
  }

  const cmds = normaliseCommands(raw)
  if (cmds.length === 0) {
    return { sent: 0, commands: [] }
  }

  const payload = cmds.join("\r\n") + "\r\n"

  const res = await fetch("/api/send", {
    method: "POST",
    headers: { "Content-Type": "text/plain" },
    body: payload,
  })

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json() // { sent: N, commands: [...] }
}

/**
 * GET /api/macros — returns { macros: { name: commandString, ... } }
 */
export async function fetchMacros() {
  const res = await fetch("/api/macros")
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * POST /api/macros — save a named macro.
 * `commands` should already be normalised (CRLF-joined).
 * `group` is optional; defaults to "".
 */
export async function saveMacro(name, commands, group = "") {
  const res = await fetch("/api/macros", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, commands, group }),
  })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * DELETE /api/macros/<name>
 */
export async function deleteMacro(name) {
  const res = await fetch("/api/macros/" + encodeURIComponent(name), {
    method: "DELETE",
  })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Patterns API
// ---------------------------------------------------------------------------

/**
 * GET /api/patterns — returns { patterns: [{ id, label, description }, ...] }
 */
export async function fetchPatterns() {
  const res = await fetch("/api/patterns")
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * POST /api/patterns/<name> — trigger a named pattern.
 * Optional `params` object is sent as JSON body (pattern kwargs).
 * Returns { ok: true, pattern: name } on success.
 */
export async function runPattern(name, params = {}) {
  const res = await fetch("/api/patterns/" + encodeURIComponent(name), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * POST /api/patterns/stop — emergency stop + clear all unit queues.
 */
export async function stopPatterns() {
  const res = await fetch("/api/patterns/stop", { method: "POST" })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Modes API — continuously-running "vibes" (server/modes/), distinct from
// the one-off Patterns bursts above: a mode keeps running until stopped.
// ---------------------------------------------------------------------------

/**
 * GET /api/modes — returns { modes: [{ id, label, description, params }, ...] }
 */
export async function fetchModes() {
  const res = await fetch("/api/modes")
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * GET /api/modes/status — returns { running: name|null, params, started_at }
 */
export async function fetchModeStatus() {
  const res = await fetch("/api/modes/status")
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * POST /api/modes/<name> — start a mode, stopping any mode already
 * running. Optional `params` object is sent as JSON body (mode kwargs).
 * Unlike runPattern, a bad param can 400 — throws with the server's
 * error message rather than a bare "HTTP 400".
 */
export async function runMode(name, params = {}) {
  const res = await fetch("/api/modes/" + encodeURIComponent(name), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.error || `HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * POST /api/modes/stop — stop the running mode (if any) + FFX/FFC.
 */
export async function stopMode() {
  const res = await fetch("/api/modes/stop", { method: "POST" })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Network API — on-Pi WiFi provisioning (server/routes/network.py). Only
// meaningful on a real Pi; simhost/run.py's dev harness reports
// {available: false} rather than erroring.
// ---------------------------------------------------------------------------

/**
 * GET /api/network — returns
 * {available, current, saved: [{ssid, priority, active}], scan: [{ssid, signal, secure}]}
 */
export async function fetchNetwork() {
  const res = await fetch("/api/network")
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * POST /api/network — add (or overwrite) a WiFi profile, then reboot the
 * Pi so hexagons-netwatch re-runs its boot-time WiFi/AP decision on it.
 * `password` may be omitted for an open network.
 */
export async function addNetwork(ssid, password, priority = 100) {
  const res = await fetch("/api/network", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ssid, password, priority }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.error || `HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * DELETE /api/network/<ssid> — forget a saved profile. No reboot.
 */
export async function forgetNetwork(ssid) {
  const res = await fetch("/api/network/" + encodeURIComponent(ssid), {
    method: "DELETE",
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.error || `HTTP ${res.status}`)
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Settings API — persisted orchestrator-wide settings (server/settings.py).
// Currently just the default motor speed (S command), surfaced on
// /admin/tools.
// ---------------------------------------------------------------------------

/**
 * GET /api/settings — returns {default_rpm, min_rpm, max_rpm}
 */
export async function fetchSettings() {
  const res = await fetch("/api/settings")
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * POST /api/settings — set the default motor speed. Applies immediately,
 * even to a piece that's mid-mode.
 */
export async function saveSettings(defaultRpm) {
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ default_rpm: defaultRpm }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.error || `HTTP ${res.status}`)
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Schedule API — daily on/off time + timezone (server/schedule.py), the
// calm UI's /settings page. Separate from Settings above: /api/settings'
// POST hard-requires default_rpm, so it can't take this shape as a patch.
// ---------------------------------------------------------------------------

/**
 * GET /api/schedule — returns
 * {enabled, on_time, off_time, timezone, last_applied, clock_synced,
 *  now_local, next_transition, scheduler}
 */
export async function fetchSchedule() {
  const res = await fetch("/api/schedule")
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * POST /api/schedule — partial patch, e.g. {enabled: true} or
 * {on_time: "08:30"}. Only the touched fields need to be included.
 */
export async function saveSchedule(patch) {
  const res = await fetch("/api/schedule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.error || `HTTP ${res.status}`)
  }
  return res.json()
}
