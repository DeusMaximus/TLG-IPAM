import { describe, expect, it } from 'vitest'
import {
  activeToken,
  agentServerUrl,
  AHEAD_WITHIN_MS,
  agentStatus,
  containerCommand,
  enrolCommand,
  openEnrolment,
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

  it('leaves a placeholder for a token the page does not hold', () => {
    const cmd = containerCommand({ url: 'https://ipam.home.arpa', version: '0.2.0' })
    expect(cmd).toContain('-e IPAM_AGENT_TOKEN=<agent token> \\')
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

describe('enrolCommand', () => {
  it('posts the enrolment token as the bearer to the enrol route', () => {
    const cmd = enrolCommand({ url: 'https://ipam.home.arpa', token: 'tlge_abc-DEF_123' })
    expect(cmd).toContain('curl -fsS -X POST https://ipam.home.arpa/api/v1/agent/enrol \\')
    expect(cmd).toContain("-H 'Authorization: Bearer tlge_abc-DEF_123'")
    expect(cmd).toContain("-H 'Content-Type: application/json' -d '{}'")
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

describe('agentServerUrl', () => {
  const at = (url) => {
    const u = new URL(url)
    return agentServerUrl({ protocol: u.protocol, hostname: u.hostname, origin: u.origin })
  }

  it('gives an https origin, or a plain-http one only on a loopback host', () => {
    expect(at('https://ipam.home.arpa')).toBe('https://ipam.home.arpa')
    expect(at('https://10.0.0.2:8443')).toBe('https://10.0.0.2:8443')
    expect(at('http://localhost:8080')).toBe('http://localhost:8080')
    expect(at('http://127.0.0.1:8080')).toBe('http://127.0.0.1:8080')
    expect(at('http://[::1]:8080')).toBe('http://[::1]:8080')
  })

  it('gives nothing for plain http across a network', () => {
    expect(at('http://ipam.home.arpa')).toBeNull()
    expect(at('http://10.0.0.2:8080')).toBeNull()
    expect(at('http://127.0.0.1.example.com')).toBeNull()
  })
})
