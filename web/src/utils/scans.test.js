import { describe, expect, it } from 'vitest'
import { ONLINE_WITHIN_MS } from './agents'
import {
  agentLimit,
  agentReport,
  formatDuration,
  hasDhcpPool,
  NEW_TARGET,
  scopeRangeText,
  settingsBody,
  settingsChanged,
  settingsForm,
} from './scans'

describe('settings form', () => {
  it('a new target starts with the server defaults: pool off, manual only', () => {
    expect(settingsBody(settingsForm(null))).toEqual(NEW_TARGET)
    expect(NEW_TARGET.scan_dhcp_pool).toBe(false)
  })

  it('round-trips a target and sends methods in the server order', () => {
    const target = {
      agent_id: 'a',
      enabled: false,
      methods: ['icmp', 'rdns'],
      scan_dhcp_pool: true,
      interval_minutes: 15,
    }
    const form = settingsForm(target)
    expect(settingsBody(form)).toEqual(target)
    expect(settingsChanged(target, form)).toBe(false)
    const reordered = { ...form, methods: ['rdns', 'icmp'] }
    expect(settingsChanged(target, reordered)).toBe(false)
    expect(settingsBody({ ...form, methods: ['fdns', 'arp', 'icmp'] }).methods).toEqual([
      'icmp',
      'arp',
      'fdns',
    ])
    expect(settingsChanged(target, { ...form, scan_dhcp_pool: false })).toBe(true)
  })

  it('a blank interval is manual only; unassigned is null', () => {
    const body = settingsBody({ ...settingsForm(null), interval: '  ', agent_id: '' })
    expect(body.interval_minutes).toBeNull()
    expect(body.agent_id).toBeNull()
    expect(settingsBody({ ...settingsForm(null), interval: '45' }).interval_minutes).toBe(45)
  })
})

describe('server-reported state', () => {
  it('reads what the agent can run from runnable_methods alone', () => {
    const base = { methods: ['icmp', 'arp', 'rdns'], runnable_methods: ['icmp', 'rdns'] }
    expect(agentLimit(base)).toBe('no-mac')
    // whether or not a job is out, and whatever the job was issued with
    expect(agentLimit({ ...base, current_job_id: 'j', job_methods: ['icmp', 'arp'] })).toBe('no-mac')
    expect(agentLimit({ ...base, runnable_methods: ['icmp', 'arp', 'rdns'] })).toBeNull()
    expect(agentLimit({ ...base, methods: ['icmp', 'rdns'] })).toBeNull()
    expect(agentLimit({ methods: ['arp'], runnable_methods: [] })).toBe('unscannable')
    // not known: no agent, or one that has never checked in
    expect(agentLimit({ ...base, runnable_methods: null })).toBeNull()
    expect(agentLimit({ ...base, runnable_methods: undefined, current_job_id: 'j', job_methods: ['icmp'] })).toBeNull()
    expect(agentLimit(null)).toBeNull()
  })

  it('a pool needs both ends', () => {
    expect(hasDhcpPool({ dhcp_start: '10.0.0.1', dhcp_end: '10.0.0.9' })).toBe(true)
    expect(hasDhcpPool({ dhcp_start: '10.0.0.1', dhcp_end: null })).toBe(false)
    expect(hasDhcpPool(null)).toBe(false)
  })

  it('formats durations and scope ranges', () => {
    expect(formatDuration(null)).toBeNull()
    expect(formatDuration(850)).toBe('850 ms')
    expect(formatDuration(12_345)).toBe('12.3 s')
    expect(formatDuration(125_000)).toBe('2 min 05 s')
    expect(formatDuration(3_900_000)).toBe('1 h 05 min')
    expect(scopeRangeText({ start: '10.0.0.5', end: '10.0.0.5' })).toBe('10.0.0.5')
    expect(scopeRangeText({ start: '10.0.0.1', end: '10.0.0.9' })).toBe('10.0.0.1–10.0.0.9')
  })
})

describe('how old what the agent can run is', () => {
  const now = Date.parse('2026-11-20T12:00:00Z')
  const target = (extra) => ({ methods: ['icmp', 'arp'], runnable_methods: ['icmp'], ...extra })

  it('dates the report by the agent’s last check-in, read as a server time', () => {
    const at = '2026-11-20T11:59:00+00:00'
    expect(agentReport(target({ agent_checkin_at: at, agent_checkin_refused: false }), now)).toEqual({
      at,
      refused: false,
      recent: true,
    })
    // a time without an offset is UTC (parseServerTime), not local time
    const bare = agentReport(target({ agent_checkin_at: '2026-11-20T11:58:00', agent_checkin_refused: false }), now)
    expect(bare.recent).toBe(true)
  })

  it('is not recent once the agent is past the online window, or its last check-in was refused', () => {
    const old = new Date(now - ONLINE_WITHIN_MS - 1000).toISOString()
    expect(agentReport(target({ agent_checkin_at: old, agent_checkin_refused: false }), now).recent).toBe(false)
    const refused = agentReport(
      target({ agent_checkin_at: '2026-11-20T11:59:30+00:00', agent_checkin_refused: true }),
      now
    )
    expect(refused).toMatchObject({ refused: true, recent: false })
  })

  it('has nothing to date when the server does not know what the agent can run', () => {
    expect(agentReport(target({ runnable_methods: null, agent_checkin_at: null }), now)).toBeNull()
    expect(agentReport(target({ agent_checkin_at: null }), now)).toBeNull()
    expect(agentReport(null, now)).toBeNull()
  })
})
