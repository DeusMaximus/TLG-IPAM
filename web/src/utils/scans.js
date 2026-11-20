// Display helpers for a subnet's scanning settings (v0.2 spec §5.2, §5.3, §5.8).
// The server decides everything about scanning: which addresses are in scope
// (`scope` on the scan target, from services/scans.scan_scope), which methods a job
// runs, and whether a settings change is valid. Nothing here works any of that out.

// The four methods the server accepts (spec §5.2), in the order it stores them
export const SCAN_METHODS = [
  { id: 'icmp', label: 'ICMP', hint: 'ping sweep; works across a router' },
  { id: 'arp', label: 'ARP', hint: 'alive and MAC; only on the agent’s own subnet' },
  { id: 'rdns', label: 'Reverse DNS', hint: 'PTR name for each live address' },
  { id: 'fdns', label: 'Forward DNS', hint: 'check each host’s FQDN resolves to its IP' },
]

// What a new scan target starts with: the server's defaults (ScanTargetCreate)
export const NEW_TARGET = {
  agent_id: null,
  enabled: true,
  methods: ['icmp', 'arp', 'rdns'],
  scan_dhcp_pool: false,
  interval_minutes: null,
}

// The editable settings of a target (or of a new one) as form state
export function settingsForm(target) {
  const t = target || NEW_TARGET
  return {
    agent_id: t.agent_id || '',
    enabled: Boolean(t.enabled),
    methods: [...(t.methods || [])],
    scan_dhcp_pool: Boolean(t.scan_dhcp_pool),
    interval: t.interval_minutes == null ? '' : String(t.interval_minutes),
  }
}

// Form state as the body of POST/PUT /scan-targets. A blank interval is null
// (manual only); anything else goes as typed, and the server says if it is wrong.
export function settingsBody(form) {
  const interval = form.interval.trim()
  return {
    agent_id: form.agent_id || null,
    enabled: form.enabled,
    methods: SCAN_METHODS.map((m) => m.id).filter((id) => form.methods.includes(id)),
    scan_dhcp_pool: form.scan_dhcp_pool,
    interval_minutes: interval === '' ? null : Number(interval),
  }
}

export function settingsChanged(target, form) {
  return JSON.stringify(settingsForm(target)) !== JSON.stringify({
    ...form,
    methods: SCAN_METHODS.map((m) => m.id).filter((id) => form.methods.includes(id)),
  })
}

// A subnet has a DHCP pool when both ends are set; without one the pool setting is moot
export const hasDhcpPool = (subnet) => Boolean(subnet?.dhcp_start && subnet?.dhcp_end)

export const targetForSubnet = (targets, subnetId) =>
  (targets || []).find((t) => t.subnet_id === subnetId) || null

// What the target's agent can run for it, as the server reports it in
// `runnable_methods`: the methods a job would be issued with now, from the interfaces
// the agent reported at its last check-in (services/jobs.job_methods, the rule
// check-in issues jobs by; spec §5.2). Nothing here works out whether the agent is on
// the subnet.
//   'unscannable' — no job would be issued: no liveness method the agent can run
//   'no-mac'      — `arp` is chosen and would be dropped from the job
//   null          — neither, or the server does not know (no agent, or one that has
//                   never checked in): then the UI says nothing
export function agentLimit(target) {
  const runnable = target?.runnable_methods
  if (!Array.isArray(runnable)) return null
  if (runnable.length === 0) return 'unscannable'
  if ((target.methods || []).includes('arp') && !runnable.includes('arp')) return 'no-mac'
  return null
}

export function formatDuration(ms) {
  if (ms == null || Number.isNaN(Number(ms))) return null
  const n = Math.max(0, Number(ms))
  if (n < 1000) return `${Math.round(n)} ms`
  const s = n / 1000
  if (s < 60) return `${s.toFixed(1)} s`
  const total = Math.round(s)
  const m = Math.floor(total / 60)
  const rest = String(total % 60).padStart(2, '0')
  if (m < 60) return `${m} min ${rest} s`
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`
}

// The server's scope ranges, `[{start, end}]`, as text; a single address reads once
export const scopeRangeText = (r) => (r.start === r.end ? r.start : `${r.start}–${r.end}`)
