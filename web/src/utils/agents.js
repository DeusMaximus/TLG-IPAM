import { parseServerTime } from './time'

// Display helpers for the Agents page. Everything an agent reports (version,
// platform, hostname, interfaces) is untrusted text: it is only ever rendered as
// React text, never as markup or a link.

// The agent checks in every 60 s (api/app/agent_protocol.py CHECKIN_INTERVAL_S)
export const CHECKIN_INTERVAL_S = 60
// Up to three missed check-ins is still "online"; past 15 minutes it is "offline"
export const ONLINE_WITHIN_MS = 3 * CHECKIN_INTERVAL_S * 1000
export const LATE_WITHIN_MS = 15 * 60 * 1000

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

// The list answer with the browser's time it arrived at. An enrolment token's time left
// is the server's `expires_in_s`, less the time since then by this browser: a difference
// of two readings of one clock, which a browser clock set wrong does not move (#143).
export const received = (answer, at = Date.now()) => ({ ...answer, receivedAt: at })

// How long it has been since the list arrived, by this browser; never negative (the
// page reads its clock just before it asks for the list, so `now` can be the earlier)
export const sinceReceived = (receivedAt, now = Date.now()) =>
  typeof receivedAt === 'number' ? Math.max(0, now - receivedAt) : 0

// The milliseconds an enrolment token has left: what the server said it had when it
// answered (`expires_in_s`, by the server's own clock, which is the one the exchange
// checks), less `elapsed`. Null for any token the server did not call live (used,
// revoked, expired, an agent token), and once that time has run out. The token's
// `expires_at` is never compared with the browser's clock.
export function enrolmentLeft(token, elapsed = 0) {
  if (!token || token.kind !== 'enrol' || token.revoked_at || token.used_at) return null
  if (typeof token.expires_in_s !== 'number') return null
  const left = token.expires_in_s * 1000 - Math.max(0, elapsed)
  return left > 0 ? left : null
}

// The enrolment token the server still calls live, whatever the agent's state: the
// one Revoke revokes. An agent holds at most one, since re-enrol revokes the one it
// replaces.
export const liveEnrolment = (agent, elapsed = 0) =>
  (agent.tokens || []).find((t) => enrolmentLeft(t, elapsed) !== null) || null

// An enrolment token the agent can exchange now: live, and its agent not disabled,
// since the exchange refuses a disabled agent's (`services/enrolment`). A disabled
// agent's live token is blocked until the agent is enabled (`blockedEnrolment`).
export const openEnrolment = (agent, elapsed = 0) =>
  agent.state === 'disabled' ? null : liveEnrolment(agent, elapsed)

export const blockedEnrolment = (agent, elapsed = 0) =>
  agent.state === 'disabled' ? liveEnrolment(agent, elapsed) : null

// Every enrolment token open now: one row per agent of the "Pending enrolments" list
// (spec §5.8), listed until it is used or expires by the server's clock. A disabled
// agent's is left out: its exchange would be refused.
export function pendingEnrolments(agents, elapsed = 0) {
  return (agents || [])
    .map((agent) => ({ agent, token: openEnrolment(agent, elapsed) }))
    .filter((p) => p.token)
}

// How long an open enrolment token has left: "in 42m", "in 30s"; null once it is due
export function timeLeft(token, elapsed = 0) {
  const left = enrolmentLeft(token, elapsed)
  if (left === null) return null
  const s = Math.floor(left / 1000)
  if (s <= 0) return null
  if (s < 60) return `in ${s}s`
  const m = Math.floor(s / 60)
  if (m < 120) return `in ${m}m`
  return `in ${Math.floor(m / 60)}h`
}
