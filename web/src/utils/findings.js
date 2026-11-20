// Display helpers for scan findings (v0.2 spec §5.5, §5.8) and the last-seen column.
//
// The server decides what a finding is, what accepting it writes (`proposed`), and
// whether it still holds when it is accepted (`fingerprint`). Nothing here works any of
// that out. What a finding shows came from the network (a MAC, or a reverse-DNS name
// chosen by whatever device answered): it is only ever rendered as React text, never
// as markup or a link.

import { parseServerTime } from './time'

// The finding types the server raises, in the order the panel lists them. `action` is
// what accepting one does; `stale` is informational, and accepting it sets the host
// offline.
export const FINDING_TYPES = [
  {
    id: 'unknown_host',
    label: 'Unknown hosts',
    hint: 'answering, with no host record',
    action: 'Create host',
  },
  {
    id: 'mac_missing',
    label: 'MAC missing',
    hint: 'a scan saw a MAC; the host has none',
    action: 'Set MAC',
  },
  {
    id: 'mac_mismatch',
    label: 'MAC mismatch',
    hint: 'a scan saw a different MAC',
    action: 'Replace MAC',
  },
  {
    id: 'name_missing',
    label: 'Name missing',
    hint: 'reverse DNS names the address; the host has no hostname',
    action: 'Set hostname',
  },
  {
    id: 'name_mismatch',
    label: 'Name mismatch',
    hint: 'reverse DNS names a different FQDN',
    action: 'Replace hostname',
  },
  {
    id: 'stale',
    label: 'Stale',
    hint: 'active, not seen for 30 days',
    action: 'Mark offline',
  },
]

const TYPE_BY_ID = Object.fromEntries(FINDING_TYPES.map((t) => [t.id, t]))

// A type the server adds later (#20's `fdns_mismatch`) still lists, under its own name
export const findingType = (id) =>
  TYPE_BY_ID[id] || { id, label: String(id), hint: '', action: 'Accept' }

// Findings by type, in FINDING_TYPES order, then any type this list does not know;
// within a type, in the server's order (by address)
export function groupFindings(findings) {
  const groups = new Map()
  for (const f of findings || []) {
    if (!groups.has(f.type)) groups.set(f.type, [])
    groups.get(f.type).push(f)
  }
  const known = FINDING_TYPES.filter((t) => groups.has(t.id)).map((t) => t.id)
  const other = [...groups.keys()].filter((id) => !TYPE_BY_ID[id])
  return [...known, ...other].map((id) => ({ type: findingType(id), findings: groups.get(id) }))
}

// What accept and dismiss take: the key exactly as listed, and its fingerprint
export const findingKey = (f) => ({
  subnet_id: f.subnet_id,
  ip: f.ip,
  type: f.type,
  observed: f.observed,
  fingerprint: f.fingerprint,
})

export const findingId = (f) => `${f.subnet_id}|${f.ip}|${f.type}|${f.observed}`

// Accept and dismiss take at most this many keys a request
export const KEYS_PER_REQUEST = 1000

export function chunk(list, size = KEYS_PER_REQUEST) {
  const out = []
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size))
  return out
}

// The observed value as text: a MAC, a PTR name, a time (`stale`), `never`, or empty
// (an `unknown_host` that gave no MAC)
export function observedText(f) {
  if (f.type === 'stale') {
    if (f.observed === 'never') return 'never seen'
    const date = parseServerTime(f.observed)
    return date ? `last seen ${date.toLocaleString()}` : f.observed
  }
  if (f.observed === '') return f.type === 'unknown_host' ? 'no MAC' : '—'
  return f.observed
}

// The host fields accepting writes, as [label, value] pairs in a fixed order
const PROPOSED_ORDER = ['hostname', 'domain', 'mac', 'status']
const PROPOSED_LABELS = { hostname: 'hostname', domain: 'domain', mac: 'MAC', status: 'status' }

export function proposedFields(f) {
  const proposed = f.proposed || {}
  const keys = [
    ...PROPOSED_ORDER.filter((k) => k in proposed),
    ...Object.keys(proposed).filter((k) => !PROPOSED_ORDER.includes(k)),
  ]
  return keys.map((k) => [PROPOSED_LABELS[k] || k, proposed[k] == null ? '—' : String(proposed[k])])
}

// When accepting would set the host's own domain: the PTR name is outside the domain
// the host has now (its override, else the subnet's). The server stores the first
// label as the hostname and the rest as an override (spec §5.5), so the host stops
// following the subnet's domain. The owner sees that before accepting.
export function domainOverride(f, subnet, host) {
  const proposed = f.proposed || {}
  if (!('domain' in proposed) || !proposed.domain) return null
  return {
    domain: String(proposed.domain),
    current: host?.domain || subnet?.domain || null,
    inherited: !host?.domain,
    fqdn: `${proposed.hostname}.${proposed.domain}`,
  }
}

// The record's current value for what the finding is about, for comparison
export function recordedText(f, host) {
  if (!host) return f.host_id ? 'host not loaded' : 'no host record'
  switch (f.type) {
    case 'mac_missing':
    case 'mac_mismatch':
      return host.mac || 'no MAC'
    case 'name_missing':
    case 'name_mismatch':
      return host.fqdn || host.hostname || 'no hostname'
    case 'stale':
      return `status ${host.status}`
    default:
      return host.hostname || host.ip
  }
}

// What the server's per-record reasons mean, for the owner
const REASON_TEXT = {
  changed:
    'it changed since it was listed, so nothing was written. The list now shows it as it is.',
  'already dismissed': 'it was already dismissed.',
}

// The records of a per-record report that were not done, with their reason as the
// server gave it. `sent` is the findings in the order they were sent, so a record the
// server could not read still names the finding it was.
export function notDone(report, sent, done) {
  return (report?.results || [])
    .filter((r) => r.status !== done)
    .map((r) => {
      const f = sent[r.index] || {}
      const reason = r.reason || 'no reason given'
      return {
        id: `${r.index}:${r.status}`,
        ip: r.ip || f.ip || '?',
        type: findingType(r.type || f.type).label,
        status: r.status,
        reason,
        explanation: REASON_TEXT[reason] || null,
      }
    })
}

// Merge per-record reports from several requests into one, indexes counted across all
export function mergeReports(reports, sizes, done) {
  const merged = { [done]: 0, skipped: 0, errors: 0, results: [] }
  let offset = 0
  reports.forEach((report, i) => {
    merged[done] += report[done] || 0
    merged.skipped += report.skipped || 0
    merged.errors += report.errors || 0
    for (const r of report.results || []) merged.results.push({ ...r, index: r.index + offset })
    offset += sizes[i]
  })
  return merged
}

// --- Scan scope and last seen ---

// Whether each host is outside the subnet's scan scope: 'out', or null when the server
// has not said. The UI never works scope out, and does no address arithmetic (IP maths
// stays in the backend's `ipnet.py`): the scan target's `scope` ranges carry address
// strings only, so they cannot be compared with a host here. What the server's answers
// do settle is a subnet with no scan target, which scans nothing. `targetState` is
// { loaded, target }; while the targets have not loaded, nothing is known.
export function scopeChecker({ loaded, target }) {
  if (loaded && !target) return () => 'out'
  return () => null
}

const SOURCE_TEXT = { scan: 'seen by a scan', manual: 'set by hand' }

// The last-seen cell of a host row. `scope` is 'out' or null (not known).
// `last_seen` is the later of the value set by hand and the scan's (spec §5.4), and
// `last_seen_source` says which.
export function lastSeenCell(host, scope = null) {
  const date = parseServerTime(host.last_seen)
  const notScanned = scope === 'out'
  if (!date) {
    if (notScanned) return { text: 'not scanned', title: 'Outside this subnet’s scan scope', notScanned }
    return { text: '—', title: null, notScanned }
  }
  const source = SOURCE_TEXT[host.last_seen_source]
  return {
    text: date.toLocaleDateString(),
    title: `${date.toLocaleString()}${source ? ` (${source})` : ''}`,
    source: host.last_seen_source || null,
    notScanned,
  }
}
