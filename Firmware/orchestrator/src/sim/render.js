// src/sim/render.js — Pure geometry/color helpers for the sim canvas.
// Kept separate from SimPage.jsx so the math is testable without Preact.

// Sequential "blue" ramp from the dataviz skill's reference palette
// (references/palette.md), reordered darkest-first for this app's dark
// surface (#0d1117) — the skill calls this "flips anchor in dark": the
// near-zero end blends toward the dark surface, the high end pops light,
// which is the reverse of the light-surface default (light->dark).
// Same 13 hex values as the skill's light-mode ramp, just walked in the
// opposite direction. Lightness is monotonic across the ramp, which is
// the actual validation criterion for a sequential (not categorical)
// palette — see the skill's validator output.
export const DEPTH_RAMP = [
  "#0d366b",
  "#104281",
  "#184f95",
  "#1c5cab",
  "#256abf",
  "#2a78d6",
  "#3987e5",
  "#5598e7",
  "#6da7ec",
  "#86b6ef",
  "#9ec5f4",
  "#b7d3f6",
  "#cde2fb",
]

// Sequential "sage" ramp for the calm (light) UI — src/pages/TouchPage.jsx
// only, via src/theme.js's data-ui="calm". Runs light-to-dark (the reverse
// of DEPTH_RAMP above, which is dark-first to blend into #0d1117): the
// near-zero end blends toward the calm palette's paper background
// (index.css's --bg: #f3efe6) and darkens toward a deep sage as magnitude
// rises. Lightness is monotonic across the ramp, same validation criterion
// as DEPTH_RAMP.
export const CALM_DEPTH_RAMP = [
  "#efe7d8",
  "#e3dcc6",
  "#d3d0ae",
  "#bfc696",
  "#a8bb80",
  "#8fac71",
  "#799c66",
  "#658c5c",
  "#537c52",
  "#436c48",
  "#345c3e",
  "#274c33",
  "#1c3d29",
]

// Hex-tile outline colors for the canvas pages (SimPage, TouchPage) — a
// canvas can't read CSS custom properties, so these are the JS-side
// counterparts of index.css's dark/calm --border, kept in sync by hand.
export const DEFAULT_STROKE = "rgba(139, 148, 158, 0.35)"
export const CALM_STROKE = "rgba(120, 110, 95, 0.35)"

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

function lerp(a, b, t) {
  return a + (b - a) * t
}

/** Map a 0..1 magnitude fraction to an "rgb(r,g,b)" string on `ramp`
 * (defaults to DEPTH_RAMP; pass CALM_DEPTH_RAMP for the calm UI). */
export function depthColor(frac, ramp = DEPTH_RAMP) {
  const clamped = Math.max(0, Math.min(1, frac))
  const scaled = clamped * (ramp.length - 1)
  const i0 = Math.floor(scaled)
  const i1 = Math.min(ramp.length - 1, i0 + 1)
  const t = scaled - i0
  const [r0, g0, b0] = hexToRgb(ramp[i0])
  const [r1, g1, b1] = hexToRgb(ramp[i1])
  return `rgb(${Math.round(lerp(r0, r1, t))}, ${Math.round(lerp(g0, g1, t))}, ${Math.round(lerp(b0, b1, t))})`
}

/**
 * Axial (q, r) -> pixel-space (x, y) centre, pointy-top layout.
 * Matches server/patterns/layout.py's pixel() exactly, so the sim's
 * geometry never has to be independently derived or drift from it.
 */
export function axialToPixel(q, r, size) {
  return [size * Math.sqrt(3) * (q + r / 2), size * 1.5 * r]
}

/** The six corner points of a pointy-top hexagon centred at (cx, cy). */
export function hexCorners(cx, cy, size) {
  const pts = []
  for (let i = 0; i < 6; i++) {
    const angle = ((60 * i - 30) * Math.PI) / 180
    pts.push([cx + size * Math.cos(angle), cy + size * Math.sin(angle)])
  }
  return pts
}

/**
 * Nearest-centre hit test: given a Map(unitId -> [x, y]) of tile centres in
 * the same pixel space as a pointer/click coordinate, return the id of the
 * tile the point falls in, or null if it's outside `maxDist` of the closest
 * one. On a hex lattice the Voronoi cell of each centre *is* that hexagon,
 * so nearest-centre-within-radius is an exact point-in-hexagon test, not an
 * approximation — this is shared by SimPage (click-to-inspect) and
 * TouchPage (drag-to-trigger).
 */
export function hitTest(positions, x, y, maxDist) {
  let closestId = null
  let closestDist = Infinity
  for (const [id, [cx, cy]] of positions) {
    const d = Math.hypot(cx - x, cy - y)
    if (d < closestDist) {
      closestDist = d
      closestId = id
    }
  }
  return closestId != null && closestDist <= maxDist ? closestId : null
}

export function tracePath(ctx, points) {
  ctx.beginPath()
  points.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)))
  ctx.closePath()
}

/**
 * Given a set of unit-space (q, r) centres and a target pixel canvas
 * size, compute the hex circumradius and centring offset that fits all
 * of them with a small margin. `paddingHexes` reserves extra room
 * (in hex-radius units) for overlay content drawn outside each hex, e.g.
 * the timeline/legend — pass 0 for a plain fit.
 */
export function fitHexGrid(centers, canvasWidth, canvasHeight, marginFrac = 0.06) {
  const xs = centers.map(([q, r]) => axialToPixel(q, r, 1)[0])
  const ys = centers.map(([q, r]) => axialToPixel(q, r, 1)[1])
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)

  const usableW = canvasWidth * (1 - marginFrac)
  const usableH = canvasHeight * (1 - marginFrac)

  const sizeX = usableW / (maxX - minX + Math.sqrt(3))
  const sizeY = usableH / (maxY - minY + 2)
  const size = Math.max(1, Math.min(sizeX, sizeY))

  const offsetX = canvasWidth / 2 - ((minX + maxX) / 2) * size
  const offsetY = canvasHeight / 2 - ((minY + maxY) / 2) * size

  return { size, offsetX, offsetY }
}
