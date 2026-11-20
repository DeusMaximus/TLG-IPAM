import { parseServerTime } from './time'

// Display helpers for the Agents page. Everything an agent reports (version,
// platform, hostname, interfaces) is untrusted text: it is only ever rendered as
// React text, never as markup or a link.

// The agent checks in every 60 s (api/app/agent_protocol.py CHECKIN_INTERVAL_S)
export const CHECKIN_INTERVAL_S = 60
// Up to three missed check-ins is still "online"; past 15 minutes it is "offline"
export const ONLINE_WITHIN_MS = 3 * CHECKIN_INTERVAL_S * 1000
export const LATE_WITHIN_MS = 15 * 60 * 1000

// The agent image, from the project's GitHub container registry (spec §5.6),
// tagged with the server's version. The release pipeline that publishes it is #24.
export const AGENT_IMAGE = 'ghcr.io/deusmaximus/tlg-ipam-agent'

export function relativeTime(value, now = Date.now()) {
  const date = parseServerTime(value)
  if (!date) return 'never'
  const s = Math.max(0, Math.round((now - date.getTime()) / 1000))
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

// The status dot: disabled wins; otherwise how long ago the last check-in was
export function agentStatus(agent, now = Date.now()) {
  if (agent.state === 'disabled') return 'disabled'
  const last = parseServerTime(agent.last_checkin_at)
  if (!last) return 'never'
  const age = now - last.getTime()
  if (age <= ONLINE_WITHIN_MS) return 'online'
  if (age <= LATE_WITHIN_MS) return 'late'
  return 'offline'
}

export const STATUS_LABELS = {
  online: 'Online',
  late: 'Late',
  offline: 'Offline',
  never: 'Never checked in',
  disabled: 'Disabled',
}

// The one unrevoked agent token, if the agent still has it
export const activeToken = (agent) =>
  (agent.tokens || []).find((t) => t.kind === 'agent' && !t.revoked_at) || null

// Leave a value bare when it has no shell metacharacters; single-quote it otherwise
export function shellQuote(value) {
  const s = String(value)
  return /^[A-Za-z0-9_./:=@%+-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`
}

// The M2a container command (spec §5.6): host networking and CAP_NET_RAW, the
// server URL and the agent token. ALLOWED_CIDRS and MAX_PPS keep their defaults.
export function containerCommand({ url, token, version }) {
  return [
    'docker run -d --name tlg-ipam-agent --restart unless-stopped \\',
    '  --network host --cap-add NET_RAW \\',
    `  -e IPAM_URL=${shellQuote(url)} \\`,
    `  -e IPAM_AGENT_TOKEN=${shellQuote(token)} \\`,
    `  ${AGENT_IMAGE}:${version ? shellQuote(version) : '<version>'}`,
  ].join('\n')
}
