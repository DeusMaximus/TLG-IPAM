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

// A server time no more than this far ahead of the browser's clock is read as "now";
// one further ahead is not read as an age at all (`serverAge`). One check-in interval:
// ordinary drift between the browser's clock and the server's is seconds, and stays
// inside it, so an agent that has just checked in still reads "0s ago" and online;
// a check-in that just happened cannot be dated a whole interval ahead, so past it the
// two clocks disagree (a browser clock behind, or a server clock set back after the
// check-in) and how fresh the time is is not known. The Agents page and the Scanning
// section both judge by it.
export const AHEAD_WITHIN_MS = CHECKIN_INTERVAL_S * 1000

// How old a server time is by the browser's clock:
//   null — no time
//   { date, ahead: true } — more than AHEAD_WITHIN_MS ahead: the age is not known
//   { date, ahead: false, age } — `age` in ms, never negative
export function serverAge(value, now = Date.now()) {
  const date = parseServerTime(value)
  if (!date) return null
  const age = now - date.getTime()
  if (age < -AHEAD_WITHIN_MS) return { date, ahead: true }
  return { date, ahead: false, age: Math.max(0, age) }
}

// A time ahead of the browser's clock is shown as itself and said to be ahead, never
// as "0s ago"
export function relativeTime(value, now = Date.now()) {
  const found = serverAge(value, now)
  if (!found) return 'never'
  if (found.ahead) return `at ${found.date.toLocaleString()}, ahead of this browser’s clock`
  const s = Math.round(found.age / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

// The status dot: disabled wins; otherwise how long ago the last check-in was, and
// 'ahead' when that is not known because the time is ahead of the browser's clock
export function agentStatus(agent, now = Date.now()) {
  if (agent.state === 'disabled') return 'disabled'
  const found = serverAge(agent.last_checkin_at, now)
  if (!found) return 'never'
  if (found.ahead) return 'ahead'
  if (found.age <= ONLINE_WITHIN_MS) return 'online'
  if (found.age <= LATE_WITHIN_MS) return 'late'
  return 'offline'
}

export const STATUS_LABELS = {
  online: 'Online',
  late: 'Late',
  offline: 'Offline',
  never: 'Never checked in',
  ahead: 'Clocks disagree',
  disabled: 'Disabled',
}

// The one unrevoked agent token, if the agent still has it
export const activeToken = (agent) =>
  (agent.tokens || []).find((t) => t.kind === 'agent' && !t.revoked_at) || null

// An enrolment token the agent can still exchange: unrevoked, unused and, by this
// browser's clock, unexpired (the server decides; this only guides the page)
export function openEnrolment(agent, now = Date.now()) {
  return (
    (agent.tokens || []).find((t) => {
      if (t.kind !== 'enrol' || t.revoked_at || t.used_at) return false
      const expires = parseServerTime(t.expires_at)
      return !expires || expires.getTime() > now
    }) || null
  )
}

// Leave a value bare when it has no shell metacharacters; single-quote it otherwise
export function shellQuote(value) {
  const s = String(value)
  return /^[A-Za-z0-9_./:=@%+-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`
}

// The M2a container command (spec §5.6): host networking and CAP_NET_RAW, the
// server URL and the agent token, or a placeholder for it when the page does not hold
// it. ALLOWED_CIDRS and MAX_PPS keep their defaults.
export function containerCommand({ url, token, version }) {
  return [
    'docker run -d --name tlg-ipam-agent --restart unless-stopped \\',
    '  --network host --cap-add NET_RAW \\',
    `  -e IPAM_URL=${shellQuote(url)} \\`,
    `  -e IPAM_AGENT_TOKEN=${token ? shellQuote(token) : '<agent token>'} \\`,
    `  ${AGENT_IMAGE}:${version ? shellQuote(version) : '<version>'}`,
  ].join('\n')
}

// The server URL an agent may be given (the agent's own `config.parse_url` rule):
// `https`, or plain `http` only to a loopback host, where nothing crosses a network.
// A page served over plain `http` from any other host gets null, so no command built
// from it sends a token in clear.
const LOOPBACK_HOST = /^(localhost|127(\.\d{1,3}){3}|\[::1\])$/i
export const SERVER_URL_PLACEHOLDER = 'https://<server>'
export function agentServerUrl({ protocol, hostname, origin }) {
  if (protocol === 'https:') return origin
  if (protocol === 'http:' && LOOPBACK_HOST.test(hostname)) return origin
  return null
}

// The enrolment exchange by hand (spec §5.7, POST /api/v1/agent/enrol), until the
// agent makes it itself on first start (#22). Its answer carries the agent token,
// `{"data": {"agent_id": …, "agent_token": "tlga_…"}}`.
export function enrolCommand({ url, token }) {
  return [
    `curl -fsS -X POST ${shellQuote(`${url}/api/v1/agent/enrol`)} \\`,
    `  -H ${shellQuote(`Authorization: Bearer ${token}`)} \\`,
    `  -H 'Content-Type: application/json' -d '{}'`,
  ].join('\n')
}
