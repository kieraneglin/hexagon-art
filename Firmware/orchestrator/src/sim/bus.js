// src/sim/bus.js — Wire-protocol parser, mirroring
// hexagons/comm.cpp's parseLine() exactly: arg ranges, sign
// requirements, and the silent-discard rules a real unit applies. This
// is the authority the simulator's command log uses to flag anything a
// real unit would drop on the floor without ever telling anyone.
//
// A command here is already a single atomic "<ID><CMD><ARGS>" string —
// splitting a multi-command payload on \r\n/;  happens upstream
// (server/routes/send.py, orchestrator.py's one-call-per-command
// functions) before it ever reaches the tap, so there is exactly one
// command per tap entry already.

export const BROADCAST_ID = 0xff

const HEX_RE = /^[0-9A-Fa-f]{2}$/

function parseUInt(str) {
  // Mirrors comm.cpp's parseUInt: optional leading + or -, then only
  // digits, value 0-65535. Never throws — returns {ok:false} instead.
  if (!str) return { ok: false }
  let i = 0
  let negative = false
  if (str[0] === "+") i = 1
  else if (str[0] === "-") {
    negative = true
    i = 1
  }
  if (i >= str.length) return { ok: false } // lone sign, nothing after it
  const digits = str.slice(i)
  if (!/^[0-9]+$/.test(digits)) return { ok: false }
  const value = parseInt(digits, 10)
  if (value > 65535) return { ok: false }
  return { ok: true, negative, value }
}

/**
 * Parse one raw command string exactly as hexagons/comm.cpp does.
 *
 * Returns `{ ok: true, id, type, ...fields }` on success — `type` is one
 * of MOVE_INCREMENTAL, MOVE_ABSOLUTE, HOME, CONDITIONAL_HOME, STOP, CLEAR,
 * POWER_DOWN, SET_SPEED, WAIT, GO, CLOCK_NUDGE — or `{ ok: false, reason }`
 * for anything a real unit would silently discard. R (repeat) is not
 * modeled — nothing in this repo emits it yet.
 */
export function parseCommand(raw) {
  const s = (raw || "").trim()
  if (s.length < 3) return { ok: false, reason: "too short (need 2 hex id + 1 command letter)" }

  const idStr = s.slice(0, 2)
  if (!HEX_RE.test(idStr)) return { ok: false, reason: "id is not 2 hex chars" }
  const id = parseInt(idStr, 16)

  const cmdChar = s[2].toUpperCase()
  const args = s.slice(3)

  switch (cmdChar) {
    case "M": {
      if (args.length < 2) return { ok: false, reason: "M needs an explicit sign + digits" }
      if (args[0] !== "+" && args[0] !== "-") return { ok: false, reason: "M needs an explicit + or -" }
      const p = parseUInt(args)
      if (!p.ok || p.value === 0) return { ok: false, reason: "M param must be 1-65535" }
      return { ok: true, id, type: "MOVE_INCREMENTAL", degrees: p.value, forward: !p.negative }
    }
    case "A": {
      if (args.length === 0) return { ok: false, reason: "A needs digits" }
      if (args[0] === "+" || args[0] === "-") return { ok: false, reason: "A rejects a sign" }
      const p = parseUInt(args)
      if (!p.ok || p.value > 359) return { ok: false, reason: "A param must be 0-359" }
      return { ok: true, id, type: "MOVE_ABSOLUTE", angle: p.value }
    }
    case "H":
      if (args.length !== 0) return { ok: false, reason: "H takes no args" }
      return { ok: true, id, type: "HOME" }
    case "N":
      if (args.length !== 0) return { ok: false, reason: "N takes no args" }
      return { ok: true, id, type: "CONDITIONAL_HOME" }
    case "X":
      if (args.length !== 0) return { ok: false, reason: "X takes no args" }
      return { ok: true, id, type: "STOP" }
    case "P":
      if (args.length !== 0) return { ok: false, reason: "P takes no args" }
      return { ok: true, id, type: "POWER_DOWN" }
    case "S": {
      if (args.length === 0) return { ok: false, reason: "S needs digits" }
      if (args[0] === "+" || args[0] === "-") return { ok: false, reason: "S rejects a sign" }
      const p = parseUInt(args)
      if (!p.ok || p.value < 10 || p.value > 300) {
        return { ok: false, reason: "S param must be 10-300 (tenths of RPM)" }
      }
      return { ok: true, id, type: "SET_SPEED", tenths: p.value }
    }
    case "W": {
      if (args.length === 0) return { ok: false, reason: "W needs digits" }
      if (args[0] === "+" || args[0] === "-") return { ok: false, reason: "W rejects a sign" }
      const p = parseUInt(args)
      if (!p.ok || p.value < 1) return { ok: false, reason: "W param must be 1-65535" }
      return { ok: true, id, type: "WAIT", ms: p.value }
    }
    case "G":
      if (args.length !== 0) return { ok: false, reason: "G takes no args" }
      return { ok: true, id, type: "GO" }
    case "C":
      if (args.length !== 0) return { ok: false, reason: "C takes no args" }
      return { ok: true, id, type: "CLEAR" }
    case "K":
      if (args.length !== 1 || (args[0] !== "+" && args[0] !== "-")) {
        return { ok: false, reason: "K takes exactly one + or -" }
      }
      return { ok: true, id, type: "CLOCK_NUDGE", faster: args[0] === "+" }
    default:
      return { ok: false, reason: `unknown command letter '${cmdChar}'` }
  }
}

/**
 * Which populated unit IDs a parsed command targets. Broadcast (FF)
 * expands to every populated unit; a unicast id targets itself only if
 * a unit is actually wired at that address — an unpopulated-but-valid
 * address (e.g. 0x40 when only 0x01-0x3D are wired) is not a protocol
 * error, it's just addressed to nobody.
 */
export function targetsFor(parsed, populatedIds) {
  if (!parsed.ok) return []
  if (parsed.id === BROADCAST_ID) return populatedIds
  return populatedIds.includes(parsed.id) ? [parsed.id] : []
}
