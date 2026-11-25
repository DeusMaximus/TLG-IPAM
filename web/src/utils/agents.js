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

// An enrolment the server refused for the agent's protocol (`enrol_refusal`, #175):
// what the owner is told, from the server's code alone, which says which way it is.
// The token was not spent, so the same install, with the matching release, enrols.
const ENROL_REFUSALS = {
  AGENT_TOO_OLD:
    'its agent release is older than the server. Install the release that matches the server',
  AGENT_TOO_NEW:
    'its agent release is newer than the server. Update the server, or install the agent release that matches it',
}

// The server keeps these refusals in its memory alone (`agents.enrol_refusals`), so a
// restart forgets them until the agent tries again; the page says so where they appear
export const ENROL_REFUSAL_RETENTION =
  'A refusal for the agent’s release is shown here and in its row until the server restarts, and again after the agent’s next attempt. A systemd install tries once: its output says why it was refused.'

// The refusal to show for an agent: none while it is disabled, whose token the server
// refuses whatever it runs (the server already gives none then)
export function shownEnrolRefusal(agent) {
  return agent.state === 'disabled' ? null : agent.enrol_refusal || null
}

export function enrolRefusalText(refusal, now = Date.now()) {
  if (!refusal) return null
  const why = ENROL_REFUSALS[refusal.code] ?? 'its agent release speaks another protocol than the server'
  return `Enrolment refused ${relativeTime(refusal.refused_at, now)}: ${why}; the enrolment token was not used.`
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

// A clock that only moves forward, at the rate time passes, whatever the wall clock
// does: an enrolment token's time left is measured with it, never with `Date.now()`,
// which the owner or NTP can step back or forward (#143). The wall clock stays the one
// the status dot and the check-in ages read.
export const monotonicNow = () => performance.now()

// The list answer with the monotonic time its request was sent (`askedAt`). An
// enrolment token's time left is the server's `expires_in_s` less the time since then:
// counting from the request, not the arrival, takes the request's own time off what
// the server gave, so an answer that was slow to arrive is never shown as having more
// time left than it has.
// `arrivedAt`, when it arrived by the same clock, is the least the page's own reading of
// it can be from then on.
export const received = (answer, askedAt, arrivedAt = monotonicNow()) => ({
  ...answer,
  askedAt,
  arrivedAt,
})

// How long it has been since the list was asked for, by the monotonic clock: from
// `askedAt` to `now`, or to its arrival if `now` is a reading from before it arrived.
// Never negative, and 0 when not known.
export function sinceAsked(answer, now = monotonicNow()) {
  if (!answer || typeof answer.askedAt !== 'number') return 0
  const at = typeof answer.arrivedAt === 'number' ? Math.max(now, answer.arrivedAt) : now
  return Math.max(0, at - answer.askedAt)
}

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

// How many ms until the label `timeLeft` gives a token with `left` ms left next
// changes, or the token runs out. The label is a function of the whole seconds left
// (`s`), so it changes when `s` falls to the next value whose label differs: every
// second under a minute, at the next whole minute under two hours, at the next whole
// hour past that. 1 ms past that instant, since `s` is rounded down.
function labelChangeIn(left) {
  const s = Math.floor(left / 1000)
  if (s === 0) return left // "soon", until it runs out
  const step = s < 60 ? 1 : s < 7200 ? 60 : 3600
  // the least whole seconds with this label: the next label starts below it
  const least = Math.floor(s / step) * step
  return left - least * 1000 + 1
}

// How many ms from `elapsed` until what the page shows of any agent's enrolment token
// next changes: a label ("in 42m" → "in 41m") or the token running out. Null when no
// agent has a live one. The page schedules its next update by it, apart from the list
// refresh, so a token leaves the page when its time runs out and not at the next
// refresh, whether that refresh succeeds or not.
export function nextEnrolmentChange(agents, elapsed = 0) {
  let next = null
  for (const agent of agents || []) {
    for (const token of agent.tokens || []) {
      const left = enrolmentLeft(token, elapsed)
      if (left === null) continue
      const due = labelChangeIn(left)
      next = next === null ? due : Math.min(next, due)
    }
  }
  return next
}
