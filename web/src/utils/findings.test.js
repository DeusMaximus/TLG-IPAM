import { describe, expect, it } from 'vitest'
import {
  chunk,
  domainOverride,
  findingKey,
  groupFindings,
  lastSeenCell,
  mergeReports,
  notDone,
  observedText,
  proposedFields,
  scopeChecker,
} from './findings'

const f = (type, ip, extra = {}) => ({
  subnet_id: 's',
  ip,
  type,
  observed: '',
  ip_int: 0,
  host_id: null,
  dynamic: false,
  proposed: {},
  fingerprint: 'fp',
  ...extra,
})

describe('grouping and keys', () => {
  it('groups by type in a fixed order, then types it does not know, keeping the server’s order', () => {
    const groups = groupFindings([
      f('stale', '10.0.0.9'),
      f('fdns_mismatch', '10.0.0.8'),
      f('unknown_host', '10.0.0.2'),
      f('stale', '10.0.0.3'),
    ])
    expect(groups.map((g) => g.type.id)).toEqual(['unknown_host', 'stale', 'fdns_mismatch'])
    expect(groups[1].findings.map((x) => x.ip)).toEqual(['10.0.0.9', '10.0.0.3'])
    expect(groups[2].type.label).toBe('fdns_mismatch')
  })

  it('a key is the four fields and the fingerprint, nothing else', () => {
    expect(findingKey(f('mac_missing', '10.0.0.2', { observed: 'aa', ip_int: 2, host_id: 'h' }))).toEqual({
      subnet_id: 's',
      ip: '10.0.0.2',
      type: 'mac_missing',
      observed: 'aa',
      fingerprint: 'fp',
    })
  })

  it('splits a batch at the server’s limit of 1,000 keys', () => {
    const parts = chunk(Array.from({ length: 2001 }, (_, i) => i))
    expect(parts.map((p) => p.length)).toEqual([1000, 1000, 1])
    expect(chunk([])).toEqual([])
  })
})

describe('per-record reports', () => {
  it('merges reports with indexes counted across requests', () => {
    const merged = mergeReports(
      [
        { accepted: 1, skipped: 1, errors: 0, results: [{ index: 0, status: 'accepted' }, { index: 1, status: 'skipped', reason: 'changed' }] },
        { accepted: 0, skipped: 0, errors: 1, results: [{ index: 0, status: 'error', reason: 'bad' }] },
      ],
      [2, 1],
      'accepted'
    )
    expect(merged).toMatchObject({ accepted: 1, skipped: 1, errors: 1 })
    expect(merged.results.map((r) => r.index)).toEqual([0, 1, 2])
  })

  it('lists what was not done with the server’s reason, naming a record it could not read by what was sent', () => {
    const sent = [f('mac_mismatch', '10.0.0.5'), f('stale', '10.0.0.6')]
    const lines = notDone(
      {
        results: [
          { index: 0, status: 'skipped', reason: 'changed', ip: '10.0.0.5', type: 'mac_mismatch' },
          { index: 1, status: 'error', reason: 'record must be an object' },
        ],
      },
      sent,
      'accepted'
    )
    expect(lines).toMatchObject([
      { ip: '10.0.0.5', type: 'MAC mismatch', status: 'skipped', reason: 'changed' },
      { ip: '10.0.0.6', type: 'Stale', status: 'error', reason: 'record must be an object', explanation: null },
    ])
    expect(lines[0].explanation).toMatch(/changed since it was listed/)
    const dismissed = notDone({ results: [{ index: 0, status: 'skipped', reason: 'already dismissed' }] }, sent, 'dismissed')
    expect(dismissed[0].explanation).toBe('it was already dismissed.')
    expect(notDone({ results: [{ index: 0, status: 'skipped' }] }, sent, 'accepted')[0].reason).toBe('no reason given')
  })
})

describe('what a finding shows', () => {
  it('lists the proposed fields in a fixed order', () => {
    expect(
      proposedFields(f('unknown_host', '10.0.0.2', { proposed: { status: 'dhcp', mac: 'aa', domain: 'example.com', hostname: 'nas' } }))
    ).toEqual([
      ['hostname', 'nas'],
      ['domain', 'example.com'],
      ['MAC', 'aa'],
      ['status', 'dhcp'],
    ])
  })

  it('names a domain override and the domain the host follows now', () => {
    const name = f('name_mismatch', '10.0.0.2', { proposed: { hostname: 'nas', domain: 'example.com' } })
    expect(domainOverride(name, { domain: 'home.arpa' }, { domain: null })).toEqual({
      domain: 'example.com',
      current: 'home.arpa',
      inherited: true,
      fqdn: 'nas.example.com',
    })
    expect(domainOverride(name, { domain: 'home.arpa' }, { domain: 'lab.home.arpa' })).toMatchObject({
      current: 'lab.home.arpa',
      inherited: false,
    })
    expect(domainOverride(name, { domain: null }, null)).toMatchObject({ current: null })
    expect(domainOverride(f('name_missing', '10.0.0.2', { proposed: { hostname: 'nas' } }), {}, null)).toBeNull()
  })

  it('reads a stale time as UTC, and says when a host was never seen', () => {
    expect(observedText(f('stale', '10.0.0.2', { observed: 'never' }))).toBe('never seen')
    const t = '2026-10-01T12:00:00+00:00'
    expect(observedText(f('stale', '10.0.0.2', { observed: t }))).toBe(
      `last seen ${new Date(Date.UTC(2026, 9, 1, 12)).toLocaleString()}`
    )
    expect(observedText(f('unknown_host', '10.0.0.2', { observed: '' }))).toBe('no MAC')
  })
})

describe('scope and last seen', () => {
  it('says a host is outside scope from the integer bounds the server sends', () => {
    expect(scopeChecker({ loaded: true, target: null })({ ip: '10.0.0.1' })).toBe('out')
    expect(scopeChecker({ loaded: false, target: null })({ ip: '10.0.0.1' })).toBeNull()
    // 10.0.0.1–10.0.0.9 and 10.0.0.20–10.0.0.20, as the server would send them
    const target = {
      subnet_id: 's',
      scope: [
        { start: '10.0.0.1', end: '10.0.0.9', start_int: 167772161, end_int: 167772169 },
        { start: '10.0.0.20', end: '10.0.0.20', start_int: 167772180, end_int: 167772180 },
      ],
    }
    const scopeOf = scopeChecker({ loaded: true, target })
    expect(scopeOf({ subnet_id: 's', ip_int: 167772161 })).toBeNull() // first in scope
    expect(scopeOf({ subnet_id: 's', ip_int: 167772169 })).toBeNull() // last of a range
    expect(scopeOf({ subnet_id: 's', ip_int: 167772180 })).toBeNull() // a one-address range
    expect(scopeOf({ subnet_id: 's', ip_int: 167772170 })).toBe('out') // between ranges
    expect(scopeOf({ subnet_id: 's', ip_int: 167772200 })).toBe('out') // past the last
    // a host of another subnet, or one without its integer, is not judged
    expect(scopeOf({ subnet_id: 'other', ip_int: 167772170 })).toBeNull()
    expect(scopeOf({ subnet_id: 's', ip: '10.0.0.10' })).toBeNull()
    // an empty scope scans nothing
    expect(scopeChecker({ loaded: true, target: { subnet_id: 's', scope: [] } })({ subnet_id: 's', ip_int: 1 })).toBe('out')
  })

  it('claims nothing when a range lacks its integer bounds', () => {
    const target = { subnet_id: 's', scope: [{ start: '10.0.0.1', end: '10.0.0.9' }] }
    expect(scopeChecker({ loaded: true, target })({ subnet_id: 's', ip_int: 167772200 })).toBeNull()
    const unsafe = { subnet_id: 's', scope: [{ start_int: 0, end_int: 2 ** 60 }] }
    expect(scopeChecker({ loaded: true, target: unsafe })({ subnet_id: 's', ip_int: 1 })).toBeNull()
  })

  it('the last-seen cell', () => {
    expect(lastSeenCell({ last_seen: null }, 'out')).toMatchObject({ text: 'not scanned', notScanned: true })
    expect(lastSeenCell({ last_seen: null }, null)).toMatchObject({ text: '—' })
    const at = new Date(Date.UTC(2026, 10, 15, 23, 30))
    const cell = lastSeenCell({ last_seen: '2026-11-15T23:30:00.123456', last_seen_source: 'manual' }, null)
    expect(cell).toMatchObject({ text: at.toLocaleDateString(), source: 'manual', notScanned: false })
    expect(cell.title).toBe(`${new Date(Date.UTC(2026, 10, 15, 23, 30, 0, 123)).toLocaleString()} (set by hand)`)
  })
})
