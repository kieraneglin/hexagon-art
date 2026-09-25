import { useEffect, useRef } from "preact/hooks"

/**
 * Log — append-only scrolling log pane.
 *
 * Props:
 *   entries:        Array<{ cls: string, text: string }>
 *   onClickFocusInput: () => void
 *   onClickEntry:   (commandText: string) => void
 *                   Called when a 'sent' line is clicked, with the raw
 *                   command text (the '> ' prefix is stripped).
 */
export function Log({ entries, onClickFocusInput, onClickEntry }) {
  const endRef = useRef(null)

  // Auto-scroll to bottom on new entries
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" })
  }, [entries])

  function handleClick(e) {
    // If the click landed on a sent line, populate the input with its command
    const line = e.target.closest(".log__line--sent")
    if (line && onClickEntry) {
      // Strip the leading '> ' prefix added when logging
      const text = line.textContent.trim().replace(/^>\s*/, "")
      if (text) {
        e.stopPropagation() // don't also trigger onClickFocusInput's plain focus
        onClickEntry(text)
        return
      }
    }
    onClickFocusInput?.()
  }

  return (
    <div class="log" onClick={handleClick}>
      {entries.map((e, i) => (
        <span
          key={i}
          class={`log__line--${e.cls}${e.cls === "sent" ? " log__line--clickable" : ""}`}
          title={e.cls === "sent" ? "Click to copy to input" : undefined}
        >
          {e.text + "\n"}
        </span>
      ))}
      <div ref={endRef} />
    </div>
  )
}
