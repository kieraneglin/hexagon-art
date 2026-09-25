import { useEffect } from "preact/hooks"

// src/theme.js — switches the whole page between the admin UI's default
// (GitHub-dark) palette and the calm/warm palette used by the root page
// and Touch (see index.css's `:root[data-ui="calm"]` block).
//
// html, body { background: var(--bg) } in index.css reads the palette at
// the document root, not #app, so a wrapper-scoped class can't repaint the
// page ground — the attribute has to live on <html> itself. preact-router
// unmounts the previous route's component on navigation, so this effect's
// cleanup is what guarantees the attribute doesn't leak from a calm page
// into an admin one (or vice versa) on a client-side route change.

/** Call with "calm" from a calm-UI page component; admin pages call
 * nothing and get the default palette. */
export function useUiTheme(name) {
  useEffect(() => {
    document.documentElement.dataset.ui = name
    return () => {
      delete document.documentElement.dataset.ui
    }
  }, [name])
}
