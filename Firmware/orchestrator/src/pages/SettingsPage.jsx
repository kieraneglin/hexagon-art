import { useEffect, useState } from "preact/hooks"
import { useUiTheme } from "../theme.js"
import { fetchSchedule, saveSchedule } from "../api.js"

// A small curated fallback for browsers with no Intl.supportedValuesOf
// (Safari < 16.4, older Firefox/Chrome) — Intl.supportedValuesOf("timeZone")
// covers everything else with the full IANA list, so this only needs to be
// enough to not leave someone stranded on very old hardware. The current
// value is always included even if it isn't in either list (see
// buildTimezoneOptions), so a schedule saved from a modern browser never
// renders as a blank/invalid select on an old one.
const FALLBACK_TIMEZONES = [
  "America/Vancouver",
  "America/Los_Angeles",
  "America/Denver",
  "America/Chicago",
  "America/New_York",
  "America/Anchorage",
  "Pacific/Honolulu",
  "UTC",
  "Europe/London",
  "Europe/Paris",
]

function buildTimezoneOptions(current) {
  let zones
  try {
    zones = Intl.supportedValuesOf("timeZone")
  } catch {
    zones = FALLBACK_TIMEZONES
  }
  const set = new Set(zones)
  if (current) set.add(current)
  return Array.from(set).sort()
}

/** Mirrors server/schedule.py's is_on_at() — compares wall-clock
 * "HH:MM" against the configured window, handling the overnight wrap.
 * Used only to warn before a save that would immediately trigger the
 * shutdown sequence; the server is the actual authority. */
function isOnAt(nowLocalIso, onTime, offTime) {
  if (!nowLocalIso || onTime === offTime) return null
  const now = nowLocalIso.slice(11, 16) // "HH:MM" out of an ISO datetime
  if (onTime < offTime) return now >= onTime && now < offTime
  return now >= onTime || now < offTime
}

function formatTransition(next) {
  if (!next) return null
  const when = new Date(next.at)
  const label = next.state === "on" ? "turns on" : "turns off"
  return `Next ${label} ${when.toLocaleString(undefined, {
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
  })}`
}

export function SettingsPage() {
  useUiTheme("calm")

  const [loaded, setLoaded] = useState(false)
  const [enabled, setEnabled] = useState(false)
  const [onTime, setOnTime] = useState("09:00")
  const [offTime, setOffTime] = useState("22:00")
  const [timezone, setTimezone] = useState("America/Vancouver")
  const [tzOptions, setTzOptions] = useState([])
  const [clockSynced, setClockSynced] = useState(true)
  const [nowLocal, setNowLocal] = useState(null)
  const [nextTransition, setNextTransition] = useState(null)
  const [scheduleSaving, setScheduleSaving] = useState(false)
  const [tzSaving, setTzSaving] = useState(false)
  const [status, setStatus] = useState(null)

  function load() {
    fetchSchedule()
      .then((data) => {
        setEnabled(data.enabled)
        setOnTime(data.on_time)
        setOffTime(data.off_time)
        setTimezone(data.timezone)
        setTzOptions(buildTimezoneOptions(data.timezone))
        setClockSynced(data.clock_synced)
        setNowLocal(data.now_local)
        setNextTransition(data.next_transition)
        setLoaded(true)
      })
      .catch(() => setStatus("Failed to load schedule."))
  }

  useEffect(() => {
    load()
  }, [])

  function handleSaveSchedule() {
    if (onTime === offTime) {
      setStatus("On and off time must differ.")
      return
    }

    // Warn before a save that would immediately shut the piece down —
    // enabling the schedule (or editing its times) while outside the
    // configured on-hours acts on the very next ~30s tick, with no other
    // confirmation anywhere in this flow.
    if (enabled) {
      const wouldBeOn = isOnAt(nowLocal, onTime, offTime)
      if (wouldBeOn === false) {
        const proceed = window.confirm(
          "It's currently outside the scheduled on-hours. Saving will begin the shutdown " +
            "sequence (home, then power down) within about 30 seconds. Continue?",
        )
        if (!proceed) return
      }
    }

    setScheduleSaving(true)
    saveSchedule({ enabled, on_time: onTime, off_time: offTime })
      .then((data) => {
        setNextTransition(data.next_transition)
        setStatus(enabled ? "Schedule saved." : "Schedule saved (disabled — piece stays on around the clock).")
      })
      .catch((err) => setStatus(err.message || "Save failed."))
      .finally(() => setScheduleSaving(false))
  }

  function handleSaveTimezone() {
    setTzSaving(true)
    saveSchedule({ timezone })
      .then(() => setStatus(`Time zone set to ${timezone}.`))
      .catch((err) => setStatus(err.message || "Save failed."))
      .finally(() => setTzSaving(false))
  }

  function handleUseDeviceTimezone() {
    try {
      const detected = Intl.DateTimeFormat().resolvedOptions().timeZone
      setTzOptions((opts) => (opts.includes(detected) ? opts : [...opts, detected].sort()))
      setTimezone(detected)
    } catch {
      setStatus("Couldn't detect this device's time zone.")
    }
  }

  return (
    <div class="calm-page">
      <div class="calm-header">
        <a class="calm-link" href="/">
          ‹ Back
        </a>
        <span class="calm-header__title">Settings</span>
      </div>

      <div class="calm-body calm-body--top">
        {status && <div class="calm-notice">{status}</div>}

        {!clockSynced && (
          <div class="calm-warning">
            This device's clock hasn't synced with the internet yet, so the schedule below won't take
            effect until it does — the piece keeps running in the meantime rather than risk staying dark
            all day for an unrelated network problem.
          </div>
        )}

        <div class="calm-section">
          <div class="calm-section__heading">Schedule</div>
          <div class="calm-section__hint">
            Automatically start each morning and shut down each night.
            {nowLocal && ` It's currently ${new Date(nowLocal).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })} here.`}
          </div>

          <label class="calm-field calm-field--row">
            <span class="calm-field__label">Enabled</span>
            <input
              type="checkbox"
              class="calm-toggle"
              checked={enabled}
              disabled={!loaded || scheduleSaving}
              onChange={(e) => setEnabled(e.target.checked)}
            />
          </label>

          <label class="calm-field">
            <span class="calm-field__label">On time</span>
            <input
              type="time"
              class="calm-field__control"
              value={onTime}
              disabled={!loaded || scheduleSaving}
              onInput={(e) => setOnTime(e.target.value)}
            />
          </label>

          <label class="calm-field">
            <span class="calm-field__label">Off time</span>
            <input
              type="time"
              class="calm-field__control"
              value={offTime}
              disabled={!loaded || scheduleSaving}
              onInput={(e) => setOffTime(e.target.value)}
            />
          </label>

          {nextTransition && enabled && (
            <div class="calm-section__hint">{formatTransition(nextTransition)}</div>
          )}

          <button class="calm-btn calm-btn--save" disabled={!loaded || scheduleSaving} onClick={handleSaveSchedule}>
            {scheduleSaving ? "Saving…" : "Save schedule"}
          </button>
        </div>

        <div class="calm-section">
          <div class="calm-section__heading">Time zone</div>
          <div class="calm-section__hint">Used to interpret the on/off times above.</div>

          <label class="calm-field">
            <span class="calm-field__label">Time zone</span>
            <select
              class="calm-field__control"
              value={timezone}
              disabled={!loaded || tzSaving}
              onChange={(e) => setTimezone(e.target.value)}
            >
              {tzOptions.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
            </select>
          </label>

          <button class="calm-link" onClick={handleUseDeviceTimezone} disabled={!loaded || tzSaving}>
            Use this device's time zone
          </button>

          <button class="calm-btn calm-btn--save" disabled={!loaded || tzSaving} onClick={handleSaveTimezone}>
            {tzSaving ? "Saving…" : "Save time zone"}
          </button>
        </div>
      </div>
    </div>
  )
}
