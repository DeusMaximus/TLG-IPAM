// The one way the UI reads a datetime the server sent. The server sends every
// datetime in UTC with its offset ("2026-11-16T06:06:07.865439+00:00", brief §4.3).
// Older values can still arrive without one: audit snapshots written before the
// server added it, as SQLite returned them. Every time the server writes is UTC, so
// an offset-less value is read as UTC too, never as local time, which is what
// `new Date(value)` would do. The fraction is cut to milliseconds, the precision
// Date.parse is specified for.
export function parseServerTime(value) {
  if (!value) return null
  let s = String(value).replace(/(\.\d{3})\d+/, '$1')
  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(s)) s += 'Z'
  const ms = Date.parse(s)
  return Number.isNaN(ms) ? null : new Date(ms)
}

// A server datetime as the browser's local date and time; the value itself when it
// is not a time, so nothing is hidden.
export function formatServerTime(value) {
  return parseServerTime(value)?.toLocaleString() ?? value ?? ''
}
