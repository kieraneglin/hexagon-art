import { useState } from "preact/hooks"
import { normaliseCommands, saveMacro, deleteMacro } from "../api.js"

/**
 * MacrosBar — the macro strip + inline editor.
 *
 * Props:
 *   macros:    { [name]: commandString }
 *   onRun:     (commandString) => void
 *   onLog:     (cls, text) => void
 *   onChange:  (newMacros) => void   — called after save/delete
 */
export function MacrosBar({ macros, onRun, onLog, onChange }) {
  const [editing, setEditing] = useState(false)
  const [macroName, setMacroName] = useState("")
  const [macroCmds, setMacroCmds] = useState("")

  function openEditor() {
    setMacroName("")
    setMacroCmds("")
    setEditing(true)
  }
  function closeEditor() {
    setEditing(false)
  }

  async function handleSave() {
    const name = macroName.trim()
    const raw = macroCmds.trim()
    if (!name || !raw) {
      return
    }

    const commands = normaliseCommands(raw.replace(/\n/g, ";")).join("\r\n") + "\r\n"
    try {
      await saveMacro(name, commands)
      onChange({ ...macros, [name]: commands })
      onLog("info", `Macro "${name}" saved.`)
      closeEditor()
    } catch (err) {
      onLog("error", `Failed to save macro: ${err.message}`)
    }
  }

  async function handleDelete(name) {
    if (!confirm(`Delete macro "${name}"?`)) {
      return
    }
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

  function handleRun(name) {
    const cmds = macros[name]
    if (!cmds) {
      return
    }
    onLog("info", `[MACRO: ${name}]`)
    onRun(cmds)
  }

  return (
    <>
      <div class="macros-bar">
        <button class="macro-btn macro-btn--add" onClick={openEditor}>
          + New Macro
        </button>
        {Object.keys(macros)
          .sort()
          .map((name) => (
            <span key={name} style="display:inline-flex;align-items:center;gap:2px">
              <button class="macro-btn macro-btn--run" title={`Run macro: ${name}`} onClick={() => handleRun(name)}>
                {name}
              </button>
              <button
                class="macro-btn macro-btn--delete"
                title={`Delete macro: ${name}`}
                onClick={() => handleDelete(name)}
              >
                ×
              </button>
            </span>
          ))}
      </div>

      {editing && (
        <div class="macro-editor">
          <input
            type="text"
            placeholder="Macro name"
            maxLength={40}
            value={macroName}
            onInput={(e) => setMacroName(e.target.value)}
            autoFocus
          />
          <textarea
            placeholder={"Commands — one per line\n01M+90\n02M-45\nFFG"}
            value={macroCmds}
            onInput={(e) => setMacroCmds(e.target.value)}
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
    </>
  )
}
