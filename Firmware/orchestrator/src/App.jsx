import { Router } from "preact-router"
import { useState } from "preact/hooks"
import { TerminalPage } from "./pages/TerminalPage.jsx"
import { ToolsPage } from "./pages/ToolsPage.jsx"
import { PatternsPage } from "./pages/PatternsPage.jsx"
import { ModesPage } from "./pages/ModesPage.jsx"
import { TouchPage } from "./pages/TouchPage.jsx"
import { SimPage } from "./pages/SimPage.jsx"
import { SimplePage } from "./pages/SimplePage.jsx"
import { SettingsPage } from "./pages/SettingsPage.jsx"
import { sendCommands, runPattern, stopPatterns, runMode } from "./api.js"

// Everything below is the "admin" namespace — the terminal/patterns/modes/
// tools UI built for developing on the piece, as opposed to the calm root
// page (SimplePage) and Touch, which are for whoever's standing in front
// of it. See "Simple UI / Admin UI" in AGENTS.md.
const TABS = [
  { path: "/admin", label: "Terminal" },
  { path: "/admin/tools", label: "Tools" },
  { path: "/admin/patterns", label: "Patterns" },
  { path: "/admin/modes", label: "Modes" },
]

function openSimTab() {
  // Named window target: repeated clicks focus the same tab instead of
  // spawning new ones. The simulator reads commands from the server-side
  // tap (see tap.py / /api/sim/tail), not from this window directly, so
  // it picks up everything sent from any tab automatically — no
  // BroadcastChannel or other cross-tab plumbing needed.
  window.open("/admin/sim", "hexsim")
}

// Persistent top-bar actions — the one place Home/Stop/Power-down live,
// shared by every tab instead of being duplicated (and drifting) per-page.
function TabBar({ active }) {
  const [notice, setNotice] = useState(null)

  function flash(msg) {
    setNotice(msg)
    setTimeout(() => setNotice(null), 3000)
  }

  function handleHome() {
    // Unconditional home (H) on every unit, lagged by distance from centre
    // so the wave radiates smoothly outward (server/patterns/home_wave.py)
    // — eases the power-supply spike of homing all 61 units at once, same
    // idea as the sweep pattern's staggered bob wave. Unconditional rather
    // than the conditional N patterns use for their own pre-run homing:
    // this button's job is to visibly and reliably home everything on
    // demand, not to skip units that don't strictly need it.
    flash("Homing all units…")
    runPattern("home_wave").catch(() => flash("Home failed"))
  }

  function handleStop() {
    flash("Stopping all units…")
    stopPatterns().catch(() => flash("Stop failed"))
  }

  function handlePowerDown() {
    if (!window.confirm("Power down all units? Coils release — each will need to re-home before its next move.")) {
      return
    }
    flash("Powering down all units…")
    sendCommands("FFP").catch(() => flash("Power down failed"))
  }

  function handleAuto() {
    // Puts the piece back into unattended operation (server/modes/auto.py)
    // — starting "auto" is all this needs to do, since it's an ordinary
    // mode: every other stop/start path (another mode, a pattern, Stop,
    // Touch's enable flow) already disarms it, so there's no separate
    // "armed" flag to manage here.
    flash("Starting auto…")
    runMode("auto", {}).catch(() => flash("Auto failed"))
  }

  return (
    <nav class="tab-bar">
      <a href="/" class="tab-bar__back-link" title="Back to the simple UI">
        ‹ Simple
      </a>
      {TABS.map((t) => (
        <a key={t.path} href={t.path} class={`tab-bar__tab${active === t.path ? " tab-bar__tab--active" : ""}`}>
          {t.label}
        </a>
      ))}
      <div class="tab-bar__actions">
        {notice && <span class="tab-bar__notice">{notice}</span>}
        <button class="tab-bar__btn" onClick={handleHome}>
          Home
        </button>
        <button class="tab-bar__btn tab-bar__btn--stop" onClick={handleStop}>
          ■ Stop
        </button>
        <button class="tab-bar__btn tab-bar__btn--danger" onClick={handlePowerDown}>
          Power Down
        </button>
        <button class="tab-bar__btn tab-bar__btn--auto" onClick={handleAuto}>
          ▶ Auto
        </button>
      </div>
      <button class="tab-bar__sim-link" onClick={openSimTab} title="Open the simulator in a new tab">
        Sim ↗
      </button>
    </nav>
  )
}

// Each admin tab route gets the shared chrome (tab bar + shell); the sim
// route renders full-screen with no chrome at all; the calm root page and
// Touch manage their own full-page layout (see index.css's .calm-page /
// .touch-page). preact-router passes path/url/matches props to whichever
// of these matches the current location — ignored here since each
// component already knows which tab it is.
function Terminal() {
  return (
    <div id="app-inner" style="display:flex;flex-direction:column;height:100%">
      <TabBar active="/admin" />
      <TerminalPage />
    </div>
  )
}

function Tools() {
  return (
    <div id="app-inner" style="display:flex;flex-direction:column;height:100%">
      <TabBar active="/admin/tools" />
      <ToolsPage />
    </div>
  )
}

function Patterns() {
  return (
    <div id="app-inner" style="display:flex;flex-direction:column;height:100%">
      <TabBar active="/admin/patterns" />
      <PatternsPage />
    </div>
  )
}

function Modes() {
  return (
    <div id="app-inner" style="display:flex;flex-direction:column;height:100%">
      <TabBar active="/admin/modes" />
      <ModesPage />
    </div>
  )
}

export function App() {
  return (
    <Router>
      <SimplePage path="/" />
      <TouchPage path="/touch" />
      <SettingsPage path="/settings" />
      <Terminal path="/admin" />
      <Tools path="/admin/tools" />
      <Patterns path="/admin/patterns" />
      <Modes path="/admin/modes" />
      <SimPage path="/admin/sim" />
    </Router>
  )
}
