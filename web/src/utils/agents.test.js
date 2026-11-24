import { describe, expect, it } from 'vitest'
import {
  activeToken,
  AHEAD_WITHIN_MS,
  agentStatus,
  blockedEnrolment,
  enrolmentLeft,
  liveEnrolment,
  openEnrolment,
  pendingEnrolments,
  received,
  relativeTime,
  sinceReceived,
  timeLeft,
} from './agents'

const NOW = Date.parse('2026-11-16T12:00:00Z')
const ago = (s) => new Date(NOW - s * 1000).toISOString()

describe('agentStatus', () => {
  const agent = (over) => ({ state: 'active', last_checkin_at: null, ...over })

  it('goes online, late, offline with the age of the last check-in', () => {
    expect(agentStatus(agent({ last_checkin_at: ago(30) }), NOW)).toBe('online')
    expect(agentStatus(agent({ last_checkin_at: ago(180) }), NOW)).toBe('online')
    expect(agentStatus(agent({ last_checkin_at: ago(181) }), NOW)).toBe('late')
    expect(agentStatus(agent({ last_checkin_at: ago(900) }), NOW)).toBe('late')
    expect(agentStatus(agent({ last_checkin_at: ago(901) }), NOW)).toBe('offline')
  })

  it('says never for an agent that has not checked in', () => {
    expect(agentStatus(agent(), NOW)).toBe('never')
  })

  it('does not call an agent online when its last check-in is dated ahead of this clock', () => {
    // inside the tolerance: just checked in
    expect(agentStatus(agent({ last_checkin_at: ago(-30) }), NOW)).toBe('online')
    expect(agentStatus(agent({ last_checkin_at: ago(-AHEAD_WITHIN_MS / 1000) }), NOW)).toBe('online')
    // past it: how fresh the check-in is is not known
    expect(agentStatus(agent({ last_checkin_at: ago(-AHEAD_WITHIN_MS / 1000 - 1) }), NOW)).toBe('ahead')
    expect(agentStatus(agent({ last_checkin_at: ago(-86400) }), NOW)).toBe('ahead')
    expect(agentStatus(agent({ state: 'disabled', last_checkin_at: ago(-86400) }), NOW)).toBe(
      'disabled'
    )
  })

  it('shows disabled whatever the last check-in', () => {
    expect(agentStatus(agent({ state: 'disabled', last_checkin_at: ago(5) }), NOW)).toBe(
      'disabled'
    )
  })
})

describe('relativeTime', () => {
  it('formats seconds, minutes, hours and days', () => {
    expect(relativeTime(null, NOW)).toBe('never')
    expect(relativeTime(ago(42), NOW)).toBe('42s ago')
    expect(relativeTime(ago(5 * 60), NOW)).toBe('5m ago')
    expect(relativeTime(ago(3 * 3600), NOW)).toBe('3h ago')
    expect(relativeTime(ago(3 * 86400), NOW)).toBe('3d ago')
    expect(relativeTime(ago(86400), NOW)).toBe('24h ago')
  })

  it('reads a time a little ahead as now, and one further ahead as itself, never "0s ago"', () => {
    expect(relativeTime(ago(-30), NOW)).toBe('0s ago')
    expect(relativeTime(ago(-AHEAD_WITHIN_MS / 1000), NOW)).toBe('0s ago')
    const ahead = '2026-11-17T12:00:00+00:00'
    expect(relativeTime(ahead, NOW)).toBe(
      `at ${new Date(Date.parse(ahead)).toLocaleString()}, ahead of this browser’s clock`
    )
    // read as a server time: no offset is UTC
    expect(relativeTime('2026-11-17T12:00:00', NOW)).toBe(relativeTime(ahead, NOW))
  })
})

describe('activeToken', () => {
  it('finds the unrevoked agent token, if any', () => {
    const live = { kind: 'agent', token_prefix: 'tlga_abcdefgh', revoked_at: null }
    const dead = { kind: 'agent', token_prefix: 'tlga_zzzzzzzz', revoked_at: '2026-11-16T00:00:00' }
    expect(activeToken({ tokens: [dead, live] })).toBe(live)
    expect(activeToken({ tokens: [dead] })).toBeNull()
    expect(activeToken({ tokens: [] })).toBeNull()
  })
})

// An enrolment token as the server lists it: `expires_in_s` is what the server's own
// clock gave it left when it answered (`agents.expires_in_s`), null once not live
const enrol = (over) => ({
  kind: 'enrol',
  token_prefix: 'tlge_abcdefgh',
  expires_at: '2026-11-16T12:30:00+00:00',
  expires_in_s: 1800,
  used_at: null,
  revoked_at: null,
  ...over,
})

describe('enrolmentLeft', () => {
  it('counts down from what the server said, by the time since the answer', () => {
    expect(enrolmentLeft(enrol(), 0)).toBe(1800 * 1000)
    expect(enrolmentLeft(enrol(), 60 * 1000)).toBe(1740 * 1000)
    expect(enrolmentLeft(enrol(), 1800 * 1000)).toBeNull()
    // a negative wait (the page read its clock just before asking) counts as none
    expect(enrolmentLeft(enrol(), -5000)).toBe(1800 * 1000)
  })

  it('is null for a token the server does not call live', () => {
    expect(enrolmentLeft(enrol({ expires_in_s: null }))).toBeNull()
    expect(enrolmentLeft(enrol({ expires_in_s: 0 }))).toBeNull()
    expect(enrolmentLeft(enrol({ expires_in_s: undefined }))).toBeNull()
    expect(enrolmentLeft(enrol({ used_at: '2026-11-16T12:10:00' }))).toBeNull()
    expect(enrolmentLeft(enrol({ revoked_at: '2026-11-16T12:10:00' }))).toBeNull()
    expect(enrolmentLeft({ kind: 'agent', revoked_at: null, expires_in_s: null })).toBeNull()
    expect(enrolmentLeft(null)).toBeNull()
  })
})

// The server's clock at 12:00 when it answered. The browser's clock does not enter the
// verdict: only the time since the answer arrived, a difference of two of its readings.
describe('an open enrolment, whichever side of the server’s clock the browser’s is', () => {
  const SERVER_AT = Date.parse('2026-11-16T12:00:00Z')
  const agent = (token, state = 'pending') => ({ id: 'a', state, tokens: [token] })

  // a minute either side, and the issue's cases: a browser at 12:31 with an expiry of
  // 12:30, and at 11:58 with an expiry of 11:59
  const MIN = 60 * 1000
  for (const skew of [-31 * MIN, -2 * MIN, -MIN, 0, MIN, 31 * MIN]) {
    const browserAt = SERVER_AT + skew
    const answer = received({ data: [] }, browserAt)

    it(`keeps a live token listed (browser ${skew / 1000}s off)`, () => {
      // expires 12:30 by the server's clock
      const live = enrol({ expires_at: '2026-11-16T12:30:00+00:00', expires_in_s: 1800 })
      // a minute after the answer: 12:01 by the server's clock, 12:02 or 12:00 by the
      // browser's, and either way not 12:31
      const elapsed = sinceReceived(answer.receivedAt, browserAt + 60 * 1000)
      expect(openEnrolment(agent(live), elapsed)).toBe(live)
      expect(pendingEnrolments([agent(live)], elapsed)).toHaveLength(1)
      expect(timeLeft(live, elapsed)).toBe('in 29m')
    })

    it(`shows an expired token as not open (browser ${skew / 1000}s off)`, () => {
      // expired 11:59 by the server's clock: a browser at 11:58 does not keep it
      const expired = enrol({ expires_at: '2026-11-16T11:59:00+00:00', expires_in_s: null })
      const elapsed = sinceReceived(answer.receivedAt, browserAt)
      expect(openEnrolment(agent(expired), elapsed)).toBeNull()
      expect(pendingEnrolments([agent(expired)], elapsed)).toEqual([])
      expect(timeLeft(expired, elapsed)).toBeNull()
    })
  }
})

describe('openEnrolment', () => {
  it('is an enrolment token the server calls live, of an agent that is not disabled', () => {
    const open = enrol()
    const agent = (tokens, state = 'pending') => ({ state, tokens })
    expect(openEnrolment(agent([{ kind: 'agent', revoked_at: null }, open]))).toBe(open)
    expect(openEnrolment(agent([enrol({ used_at: '2026-11-16T12:10:00', expires_in_s: null })]))).toBeNull()
    expect(openEnrolment(agent([enrol({ revoked_at: '2026-11-16T12:10:00', expires_in_s: null })]))).toBeNull()
    expect(openEnrolment(agent([enrol({ expires_in_s: null })]))).toBeNull()
    expect(openEnrolment(agent([]))).toBeNull()
  })

  it('leaves out a disabled agent’s token, which the exchange refuses, and calls it blocked', () => {
    const token = enrol()
    const disabled = { state: 'disabled', tokens: [token] }
    expect(openEnrolment(disabled)).toBeNull()
    expect(blockedEnrolment(disabled)).toBe(token)
    // still live, so Revoke can still revoke it
    expect(liveEnrolment(disabled)).toBe(token)
    expect(blockedEnrolment({ state: 'pending', tokens: [token] })).toBeNull()
    expect(blockedEnrolment({ state: 'disabled', tokens: [enrol({ expires_in_s: null })] })).toBeNull()
  })
})

describe('pendingEnrolments', () => {
  it('lists each agent with an open enrolment token until it is used or expires', () => {
    const open = { id: 'a', state: 'pending', tokens: [enrol()] }
    const used = { id: 'b', state: 'active', tokens: [enrol({ used_at: '2026-11-16T12:10:00', expires_in_s: null })] }
    const expired = { id: 'c', state: 'pending', tokens: [enrol({ expires_in_s: null })] }
    const revoked = { id: 'd', state: 'pending', tokens: [enrol({ revoked_at: '2026-11-16T12:10:00', expires_in_s: null })] }
    // an active agent being re-enrolled holds its agent token and the new one
    const reEnrolling = { id: 'e', state: 'active', tokens: [{ kind: 'agent', revoked_at: null }, enrol()] }
    // a never-enrolled agent disabled before it enrolled: its exchange is refused
    const disabled = { id: 'f', state: 'disabled', tokens: [enrol()] }
    const found = pendingEnrolments([open, used, expired, revoked, reEnrolling, disabled])
    expect(found.map((p) => p.agent.id)).toEqual(['a', 'e'])
    expect(found[0].token.token_prefix).toBe('tlge_abcdefgh')
    // and it drops out once the time the server gave has passed
    expect(pendingEnrolments([open], 1800 * 1000)).toEqual([])
    expect(pendingEnrolments(null)).toEqual([])
  })
})

describe('timeLeft', () => {
  it('says how long an open token has left, and nothing once it is due', () => {
    expect(timeLeft(enrol({ expires_in_s: 1800 }))).toBe('in 30m')
    expect(timeLeft(enrol({ expires_in_s: 1800 }), 30 * 1000)).toBe('in 29m')
    expect(timeLeft(enrol({ expires_in_s: 45 }))).toBe('in 45s')
    expect(timeLeft(enrol({ expires_in_s: 3 * 3600 }))).toBe('in 3h')
    expect(timeLeft(enrol({ expires_in_s: 1 }), 500)).toBeNull()
    expect(timeLeft(enrol({ expires_in_s: 45 }), 45 * 1000)).toBeNull()
    expect(timeLeft(enrol({ expires_in_s: null }))).toBeNull()
  })

  it('never reads the expiry against the browser’s clock', () => {
    // an expiry long past by any clock, but the server said it has 10 minutes left
    expect(timeLeft(enrol({ expires_at: '2000-01-01T00:00:00', expires_in_s: 600 }))).toBe('in 10m')
  })
})

describe('sinceReceived', () => {
  it('is the time since the answer arrived, never negative, and 0 when not known', () => {
    expect(sinceReceived(1000, 61 * 1000)).toBe(60 * 1000)
    expect(sinceReceived(5000, 1000)).toBe(0)
    expect(sinceReceived(undefined, 1000)).toBe(0)
    expect(received({ data: [1] }, 42)).toEqual({ data: [1], receivedAt: 42 })
  })
})
