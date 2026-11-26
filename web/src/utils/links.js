import { sinceAsked } from './agents'

// Display rules for linking a client and the Linked clients list (v0.2 spec §6.3, §6.6).
// What a client declared about itself (its name) and the host it chose are untrusted
// text: they are only ever rendered as React text, never as markup or a link.

// While the dialog's window is open it asks the server how the window stands this often:
// a window ends without the page doing anything (a link, its expiry, a replacement from
// another tab), and the dialog has to learn it
export const POLL_MS = 2000

// The Linked clients page refreshes the list (Last contact) and the window state this often
export const REFRESH_MS = 15000

// The milliseconds an open window has left: the server's `expires_in_s` (by its own
// monotonic clock, the one that ends the window) less the time since the answer was
// asked for (`sinceAsked`, the browser's monotonic clock). Never from `expires_at` and
// the wall clock. Null when the answer is no open window. 0 once it has run out by that
// reckoning: the server's `open` stays the authority on whether it is still open.
export function windowLeft(state, now) {
  if (!state || !state.open || typeof state.expires_in_s !== 'number') return null
  return Math.max(0, state.expires_in_s * 1000 - sinceAsked(state, now))
}

// "9:42", "0:05"; whole seconds left, rounded down
export function countdown(left) {
  if (left === null || left === undefined) return null
  const s = Math.floor(left / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

// The code in two groups of five, as easier to read and type; the consent page drops
// spaces and hyphens, so either way of typing it is the same code
export const groupedCode = (code) =>
  code && code.length === 10 ? `${code.slice(0, 5)} ${code.slice(5)}` : code || ''

// Whether a later answer is about the window the dialog opened: one opened elsewhere
// (another tab, the API) replaces it, and has a `window_id` of its own
export const sameWindow = (opened, state) =>
  Boolean(opened && state && state.open && opened.window_id && state.window_id === opened.window_id)

// How a window that the dialog opened ended, from the state the server gave once it
// was no longer open (`ended`), or 'replaced' when another window took its place
export const ENDED_TEXT = {
  linked: 'Linked. The client is in the list below.',
  expired:
    'The window ran out before a client was linked. Nothing was linked: open a new window to try again.',
  cancelled: 'The window was cancelled. Nothing was linked by it.',
  replaced:
    'This window was replaced by one opened elsewhere (another tab, or the API), and its code no longer works.',
  failed:
    'The approval failed on the server, which ended the window. Nothing was linked: open a new window to try again.',
  // no end recorded: the server forgets the last end when it restarts, which ends a window
  gone: 'The window is gone: the server has restarted since it was opened. Nothing was linked by it.',
}

// What a link may do now, and what was chosen when it was made. A link made read-write
// is read-only while the server has remote writes off (`MCP_REMOTE_WRITES`, #28): the
// list says both, so the owner does not take it for read-only for good
export function access(link) {
  if (link.writes) return { label: 'Read and write', note: null }
  if (link.can_write && !link.revoked_at) {
    return {
      label: 'Read-only now',
      note: 'Linked read and write; this server has remote writes off (MCP_REMOTE_WRITES), so it can only read until they are turned on.',
    }
  }
  return { label: link.can_write ? 'Read and write' : 'Read-only', note: null }
}

// What the client is called beside its host. The name is the client's own claim, shown
// as a quotation (`ClientName`); a link with none is called by what is known instead
export function nameText(link) {
  if (link.client_name) return null
  return link.client_registered ? 'gave no name this server keeps' : 'registration deleted'
}

export const liveLinks = (links) => (links || []).filter((l) => !l.revoked_at)
export const revokedLinks = (links) => (links || []).filter((l) => l.revoked_at)

// The short id the change log's `grant-id:<id>` actor begins with, for a client with no
// kept name
export const shortId = (id) => String(id || '').slice(0, 8)
