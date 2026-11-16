import { describe, expect, it } from 'vitest'
import {
  addressValue,
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
  it('reads IPv4 and IPv6 addresses, and refuses what is not one', () => {
    expect(addressValue('10.0.0.1')).toBe(167772161n)
    expect(addressValue('255.255.255.255')).toBe(4294967295n)
    expect(addressValue('10.0.0.256')).toBeNull()
    expect(addressValue('::1')).toBe(1n)
    expect(addressValue('2001:db8::1')).toBe((0x20010db8n << 96n) + 1n)
    expect(addressValue('::ffff:10.0.0.1')).toBe((0xffffn << 32n) + 167772161n)
    expect(addressValue('1:2:3:4:5:6:7:8') & 0xffffn).toBe(8n)
    for (const bad of ['', 'nas', '1:2:3', '1::2::3', '1:2:3:4:5:6:7:8:9', '12345::', null]) {
      expect(addressValue(bad)).toBeNull()
    }
  })

  it('checks a host against the server’s ranges, inclusive at both ends', () => {
    const inScope = scopeChecker({
      loaded: true,
      target: { scope: [{ start: '10.0.0.1', end: '10.0.0.99' }, { start: '10.0.0.201', end: '10.0.0.254' }] },
    })
    expect(inScope({ ip: '10.0.0.1' })).toBe('in')
    expect(inScope({ ip: '10.0.0.99' })).toBe('in')
    expect(inScope({ ip: '10.0.0.100' })).toBe('out')
    expect(inScope({ ip: '10.0.0.201' })).toBe('in')
    expect(scopeChecker({ loaded: true, target: { scope: [] } })({ ip: '10.0.0.1' })).toBe('out')
    expect(scopeChecker({ loaded: true, target: null })({ ip: '10.0.0.1' })).toBe('out')
    expect(scopeChecker({ loaded: false, target: null })({ ip: '10.0.0.1' })).toBeNull()
    // a range it cannot read: no claim either way
    expect(scopeChecker({ loaded: true, target: { scope: [{ start: 'x', end: '10.0.0.9' }] } })({ ip: '10.0.0.1' })).toBeNull()
  })

  it('the last-seen cell', () => {
    expect(lastSeenCell({ last_seen: null }, 'out')).toMatchObject({ text: 'not scanned', notScanned: true })
    expect(lastSeenCell({ last_seen: null }, 'in')).toMatchObject({ text: 'never', notScanned: false })
    expect(lastSeenCell({ last_seen: null }, null)).toMatchObject({ text: '—' })
    const at = new Date(Date.UTC(2026, 10, 15, 23, 30))
    const cell = lastSeenCell({ last_seen: '2026-11-15T23:30:00.123456', last_seen_source: 'manual' }, 'in')
    expect(cell).toMatchObject({ text: at.toLocaleDateString(), source: 'manual', notScanned: false })
    expect(cell.title).toBe(`${new Date(Date.UTC(2026, 10, 15, 23, 30, 0, 123)).toLocaleString()} (set by hand)`)
  })
})
