import { describe, expect, it } from 'vitest'
import {
  activeToken,
  AHEAD_WITHIN_MS,
  agentStatus,
  openEnrolment,
  pendingEnrolments,
  relativeTime,
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

describe('openEnrolment', () => {
  const now = Date.parse('2026-11-16T06:30:00Z')
  const enrol = (over) => ({
    kind: 'enrol',
    expires_at: '2026-11-16T07:00:00',
    used_at: null,
    revoked_at: null,
    ...over,
  })

  it('is an enrolment token that is unrevoked, unused and unexpired', () => {
    const open = enrol()
    expect(openEnrolment({ tokens: [{ kind: 'agent', revoked_at: null }, open] }, now)).toBe(open)
    expect(openEnrolment({ tokens: [enrol({ used_at: '2026-11-16T06:10:00' })] }, now)).toBeNull()
    expect(openEnrolment({ tokens: [enrol({ revoked_at: '2026-11-16T06:10:00' })] }, now)).toBeNull()
    expect(openEnrolment({ tokens: [enrol({ expires_at: '2026-11-16T06:30:00' })] }, now)).toBeNull()
    expect(openEnrolment({ tokens: [] }, now)).toBeNull()
  })
})

describe('pendingEnrolments', () => {
  const now = Date.parse('2026-11-16T06:30:00Z')
  const enrol = (over) => ({
    kind: 'enrol',
    token_prefix: 'tlge_abcdefgh',
    expires_at: '2026-11-16T07:00:00',
    used_at: null,
    revoked_at: null,
    ...over,
  })

  it('lists each agent with an open enrolment token until it is used or expires', () => {
    const open = { id: 'a', tokens: [enrol()] }
    const used = { id: 'b', tokens: [enrol({ used_at: '2026-11-16T06:10:00' })] }
    const expired = { id: 'c', tokens: [enrol({ expires_at: '2026-11-16T06:29:59' })] }
    const revoked = { id: 'd', tokens: [enrol({ revoked_at: '2026-11-16T06:10:00' })] }
    // an active agent being re-enrolled holds its agent token and the new one
    const reEnrolling = { id: 'e', tokens: [{ kind: 'agent', revoked_at: null }, enrol()] }
    const found = pendingEnrolments([open, used, expired, revoked, reEnrolling], now)
    expect(found.map((p) => p.agent.id)).toEqual(['a', 'e'])
    expect(found[0].token.token_prefix).toBe('tlge_abcdefgh')
    expect(pendingEnrolments(null, now)).toEqual([])
  })
})

describe('timeLeft', () => {
  const now = Date.parse('2026-11-16T06:30:00Z')
  it('says how long an open token has left, and nothing once it is due', () => {
    expect(timeLeft('2026-11-16T07:00:00', now)).toBe('in 30m')
    expect(timeLeft('2026-11-16T06:30:45', now)).toBe('in 45s')
    expect(timeLeft('2026-11-16T09:30:00+00:00', now)).toBe('in 3h')
    expect(timeLeft('2026-11-16T06:30:00', now)).toBeNull()
    expect(timeLeft(null, now)).toBeNull()
  })
})
