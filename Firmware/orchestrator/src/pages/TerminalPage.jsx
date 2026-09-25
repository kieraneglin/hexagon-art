import { useState, useRef, useEffect } from "preact/hooks"
import { sendCommands, fetchMacros } from "../api.js"
import { Log } from "../components/Log.jsx"
import { MacrosDrawer } from "../components/MacrosDrawer.jsx"

export function TerminalPage() {
  const [logEntries, setLogEntries] = useState([])
  const [status, setStatus] = useState("Ready")
  const [macros, setMacros] = useState({})
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [inputVal, setInputVal] = useState("")
  const historyRef = useRef([])
  const historyIdx = useRef(-1)
  const inputRef = useRef(null)

  // ── Helpers ──────────────────────────────────────────────────
  function append(cls, text) {
    setLogEntries((prev) => [...prev, { cls, text }])
  }

  function clearLog() {
    setLogEntries([])
    focusInput()
  }

  function focusInput() {
    inputRef.current?.focus()
  }

  // ── localStorage history ──────────────────────────────────────
  const LS_KEY = "hexagon:cmdHistory"
  const LS_MAX = 200 // max entries to persist

  function loadHistory() {
    try {
      return JSON.parse(localStorage.getItem(LS_KEY) || "[]")
    } catch {
      return []
    }
  }

  function persistHistory(hist) {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(hist.slice(-LS_MAX)))
    } catch {
      // storage full or unavailable — silently ignore
    }
  }

  // ── Init ─────────────────────────────────────────────────────
  useEffect(() => {
    // Warn if not served from the orchestrator itself
    if (location.protocol === "file:" || location.hostname === "localhost" || location.hostname === "127.0.0.1") {
      append("error", `⚠ Page loaded from ${location.origin} — /api/* calls will fail.`)
      append("error", "  Open http://hexagons.local instead.")
    }

    append("banner", "══ Hexagon Orchestrator ══")
    append("banner", "Type commands below.  Arrow keys: history.  Use ';' between commands.")
    append("banner", "Broadcast helpers:  FFG  FFX  FFC  FFH  FFW<ms>  FFN  FFP")
    append("info", "")

    // Seed in-memory history from localStorage so arrow-key navigation
    // works across page reloads
    historyRef.current = loadHistory()

    fetchMacros()
      .then((data) => setMacros(data.macros || {}))
      .catch((err) => append("error", `Failed to load macros: ${err.message}`))

    focusInput()
  }, []) // intentionally empty — runs once on mount

  // ── Send ─────────────────────────────────────────────────────
  async function handleSend(raw) {
    if (!raw.trim()) {
      return
    }
    historyRef.current.push(raw)
    historyIdx.current = -1
    persistHistory(historyRef.current)
    setInputVal("")

    try {
      const data = await sendCommands(raw)
      append("sent", `> ${raw}`)
      setStatus(`Sent ${data.sent} command${data.sent !== 1 ? "s" : ""}`)
    } catch (err) {
      append("error", `Connection error: ${err.message}`)
      setStatus("Offline")
    }
  }

  // ── Input handlers ───────────────────────────────────────────
  function handleInput(e) {
    // Uppercase live
    const el = e.target
    const pos = el.selectionStart
    const val = el.value.toUpperCase()
    setInputVal(val)
    requestAnimationFrame(() => el.setSelectionRange(pos, pos))
  }

  function handleKeyDown(e) {
    if (e.key === "Enter") {
      e.preventDefault()
      handleSend(inputVal)
      return
    }

    const hist = historyRef.current
    if (e.key === "ArrowUp") {
      e.preventDefault()
      if (hist.length > 0 && historyIdx.current < hist.length - 1) {
        historyIdx.current++
        const val = hist[hist.length - 1 - historyIdx.current]
        setInputVal(val)
        requestAnimationFrame(() => {
          const el = inputRef.current
          el?.setSelectionRange(val.length, val.length)
        })
      }
    } else if (e.key === "ArrowDown") {
      e.preventDefault()
      if (historyIdx.current > 0) {
        historyIdx.current--
        const val = hist[hist.length - 1 - historyIdx.current]
        setInputVal(val)
        requestAnimationFrame(() => {
          const el = inputRef.current
          el?.setSelectionRange(val.length, val.length)
        })
      } else if (historyIdx.current === 0) {
        historyIdx.current = -1
        setInputVal("")
      }
    } else if (e.key === "Escape") {
      // Handled globally for macro editor; nothing to do here
    }
  }

  // ── Click-to-populate from log ───────────────────────────────
  function handleClickEntry(cmd) {
    setInputVal(cmd)
    requestAnimationFrame(() => {
      const el = inputRef.current
      if (!el) {
        return
      }
      el.focus()
      el.setSelectionRange(cmd.length, cmd.length)
    })
  }

  // ── Render ───────────────────────────────────────────────────
  return (
    <div class="page">
      <Log entries={logEntries} onClickFocusInput={focusInput} onClickEntry={handleClickEntry} />

      <MacrosDrawer
        macros={macros}
        onRun={(cmds) => handleSend(cmds)}
        onLog={append}
        onChange={setMacros}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
      />

      <div class="input-line">
        <span class="input-line__prompt">&gt;</span>
        <input
          ref={inputRef}
          class="input-line__cmd"
          type="text"
          placeholder="01M+90"
          value={inputVal}
          onInput={handleInput}
          onKeyDown={handleKeyDown}
          autocomplete="off"
          autocorrect="off"
          autocapitalize="characters"
          spellcheck={false}
          autofocus
        />
        <div class="input-line__actions">
          <button
            class={`input-line__macros${drawerOpen ? " input-line__macros--active" : ""}`}
            onClick={() => setDrawerOpen((o) => !o)}
            title="Toggle macros"
          >
            Macros
          </button>
          <button class="input-line__clear" onClick={clearLog} title="Clear log">
            Clear
          </button>
          <button class="input-line__send" onClick={() => handleSend(inputVal)}>
            Send
          </button>
        </div>
      </div>

      <div class="status-bar">
        <span>{status}</span>
        <span />
      </div>
    </div>
  )
}
