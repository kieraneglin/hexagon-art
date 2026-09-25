import { useState, useEffect, useRef } from "preact/hooks"
import { normaliseCommands, saveMacro, deleteMacro } from "../api.js"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Group the macros object into an ordered array of { group, macros[] }.
 * Each macros[] entry is { name, commands, group }.
 * Named groups come first, sorted alphabetically.
 * Ungrouped macros appear last under the empty-string key.
 */
function groupMacros(macros) {
  const map = {} // group -> [{ name, commands, group }]

  for (const [name, value] of Object.entries(macros)) {
    const commands = typeof value === "string" ? value : value.commands
    const group = typeof value === "string" ? "" : (value.group ?? "")
    if (!map[group]) map[group] = []
    map[group].push({ name, commands, group })
  }

  // Sort each group's macros by name
  for (const arr of Object.values(map)) {
    arr.sort((a, b) => a.name.localeCompare(b.name))
  }

  // Sort group keys: named groups first (alpha), ungrouped last
  const keys = Object.keys(map).sort((a, b) => {
    if (a === "" && b !== "") return 1
    if (a !== "" && b === "") return -1
    return a.localeCompare(b)
  })

  return keys.map((g) => ({ group: g, macros: map[g] }))
}

// ---------------------------------------------------------------------------
// MacrosDrawer
// ---------------------------------------------------------------------------

/**
 * Props:
 *   macros:   { [name]: { commands, group } | string }  — raw from API
 *   onRun:    (commandString) => void
 *   onLog:    (cls, text) => void
 *   onChange: (newMacros) => void
 *   open:     boolean
 *   onClose:  () => void
 */
export function MacrosDrawer({ macros, onRun, onLog, onChange, open, onClose }) {
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState("")
  const [editCmds, setEditCmds] = useState("")
  const [editGroup, setEditGroup] = useState("")
  const nameRef = useRef(null)

  // Collapse state: { [groupKey]: boolean }
  const [collapsed, setCollapsed] = useState({})

  // Focus macro name when editor opens
  useEffect(() => {
    if (editing) {
      requestAnimationFrame(() => nameRef.current?.focus())
    }
  }, [editing])

  // Close on Escape
  useEffect(() => {
    if (!open) return
    function onKey(e) {
      if (e.key === "Escape") {
        if (editing) closeEditor()
        else onClose()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, editing])

  // ── Editor helpers ──────────────────────────────────────────

  function openEditor() {
    setEditName("")
    setEditCmds("")
    setEditGroup("")
    setEditing(true)
  }

  function closeEditor() {
    setEditing(false)
  }

  async function handleSave() {
    const name = editName.trim()
    const raw = editCmds.trim()
    const group = editGroup.trim()
    if (!name || !raw) return

    const commands = normaliseCommands(raw.replace(/\n/g, ";")).join("\r\n") + "\r\n"
    try {
      await saveMacro(name, commands, group)
      onChange({ ...macros, [name]: { commands, group } })
      onLog("info", `Macro "${name}" saved.`)
      closeEditor()
    } catch (err) {
      onLog("error", `Failed to save macro: ${err.message}`)
    }
  }

  async function handleDelete(name) {
    if (!confirm(`Delete macro "${name}"?`)) return
    try {
      await deleteMacro(name)
      const next = { ...macros }
      delete next[name]
      onChange(next)
      onLog("info", `Macro "${name}" deleted.`)
    } catch (err) {
      onLog("error", `Failed to delete: ${err.message}`)
    }
  }

  function handleRun(entry) {
    if (!entry.commands) return
    onLog("info", `[MACRO: ${entry.name}]`)
    // Convert CRLF-joined commands to semicolon-separated so the history
    // entry is a readable single-line string that can be re-run verbatim.
    const asLine = entry.commands
      .replace(/\r\n/g, ";")
      .replace(/\n/g, ";")
      .replace(/;+$/, "") // strip trailing semicolons
      .trim()
    onRun(asLine)
  }

  function toggleGroup(key) {
    setCollapsed((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  // ── Render ───────────────────────────────────────────────────

  const groups = groupMacros(macros)
  const totalCount = Object.keys(macros).length

  return (
    <>
      {/* Backdrop */}
      {open && <div class="drawer-backdrop" onClick={onClose} />}

      {/* Drawer panel */}
      <div class={`macros-drawer${open ? " macros-drawer--open" : ""}`}>
        <div class="macros-drawer__header">
          <span class="macros-drawer__title">
            Macros
            {totalCount > 0 && <span class="macros-drawer__count">{totalCount}</span>}
          </span>
          <div style="display:flex;gap:6px;align-items:center">
            <button class="macros-drawer__add-btn" onClick={openEditor}>
              + New
            </button>
            <button class="macros-drawer__close-btn" onClick={onClose} title="Close macros">
              ✕
            </button>
          </div>
        </div>

        <div class="macros-drawer__body">
          {editing && (
            <div class="macro-editor macro-editor--drawer">
              <input
                ref={nameRef}
                type="text"
                placeholder="Macro name"
                maxLength={40}
                value={editName}
                onInput={(e) => setEditName(e.target.value)}
              />
              <input
                type="text"
                placeholder="Group (optional)"
                maxLength={40}
                value={editGroup}
                onInput={(e) => setEditGroup(e.target.value)}
              />
              <textarea
                placeholder={"Commands — one per line\n01M+90\n02M-45\nFFG"}
                value={editCmds}
                onInput={(e) => setEditCmds(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault()
                    handleSave()
                  }
                }}
              />
              <div class="macro-editor__row">
                <button class="macro-editor__btn" onClick={handleSave}>
                  Save
                </button>
                <button class="macro-editor__btn macro-editor__btn--cancel" onClick={closeEditor}>
                  Cancel
                </button>
              </div>
            </div>
          )}

          {groups.length === 0 && !editing && (
            <p class="macros-drawer__empty">No macros yet. Hit "+ New" to create one.</p>
          )}

          {groups.map(({ group, macros: entries }) => {
            const key = group || ""
            const label = group || "Ungrouped"
            const isCollapsed = !!collapsed[key]
            return (
              <div key={key} class="macro-group">
                <button
                  class="macro-group__header"
                  onClick={() => toggleGroup(key)}
                  title={isCollapsed ? "Expand" : "Collapse"}
                >
                  <span class="macro-group__chevron">{isCollapsed ? "▶" : "▼"}</span>
                  <span class="macro-group__label">{label}</span>
                  <span class="macro-group__count">{entries.length}</span>
                </button>
                {!isCollapsed && (
                  <div class="macro-group__list">
                    {entries.map((entry) => (
                      <div key={entry.name} class="macro-row">
                        <button class="macro-row__run" title={`Run: ${entry.name}`} onClick={() => handleRun(entry)}>
                          {entry.name}
                        </button>
                        <button
                          class="macro-row__delete"
                          title={`Delete: ${entry.name}`}
                          onClick={() => handleDelete(entry.name)}
                        >
                          ×
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </>
  )
}
