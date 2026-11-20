import { describe, expect, it } from 'vitest'
import {
  activeToken,
  AHEAD_WITHIN_MS,
  agentStatus,
  containerCommand,
  relativeTime,
  shellQuote,
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

describe('containerCommand', () => {
  it('runs the agent with host networking, NET_RAW, the URL and the token', () => {
    const cmd = containerCommand({
      url: 'https://ipam.home.arpa',
      token: 'tlga_abc-DEF_123',
      version: '0.2.0',
    })
    expect(cmd).toContain('--network host')
    expect(cmd).toContain('--cap-add NET_RAW')
    expect(cmd).toContain('-e IPAM_URL=https://ipam.home.arpa')
    expect(cmd).toContain('-e IPAM_AGENT_TOKEN=tlga_abc-DEF_123')
    expect(cmd).toMatch(/tlg-ipam-agent:0\.2\.0$/)
  })

  it('leaves a placeholder when the server version is unknown', () => {
    expect(containerCommand({ url: 'http://x', token: 't', version: undefined })).toMatch(
      /:<version>$/
    )
  })

  it('quotes anything with shell metacharacters', () => {
    expect(shellQuote('plain-1.2')).toBe('plain-1.2')
    expect(shellQuote("a b'c")).toBe(`'a b'\\''c'`)
    expect(shellQuote('$(id)')).toBe(`'$(id)'`)
  })
})
