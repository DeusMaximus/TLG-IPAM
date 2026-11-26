import { describe, expect, it } from 'vitest'
import { access, countdown, endedOutcome, groupedCode, nameText, sameWindow, windowLeft } from './links'

describe('windowLeft', () => {
  const state = { open: true, expires_in_s: 600, askedAt: 1000, arrivedAt: 1200 }
  it('is expires_in_s less the time since the answer was asked for', () => {
    expect(windowLeft(state, 61_000)).toBe(540_000)
  })
  it('never reads expires_at or the wall clock', () => {
    expect(windowLeft({ ...state, expires_at: '2000-01-01T00:00:00+00:00' }, 1000)).toBe(599_800)
  })
  it('stops at 0, and is null for no open window', () => {
    expect(windowLeft(state, 10_000_000)).toBe(0)
    expect(windowLeft({ ...state, open: false }, 1000)).toBeNull()
    expect(windowLeft(null, 1000)).toBeNull()
  })
})

describe('countdown', () => {
  it('shows minutes and whole seconds, rounded down', () => {
    expect(countdown(600_000)).toBe('10:00')
    expect(countdown(599_999)).toBe('9:59')
    expect(countdown(5_500)).toBe('0:05')
    expect(countdown(0)).toBe('0:00')
    expect(countdown(null)).toBeNull()
  })
})

it('groups a code in fives', () => {
  expect(groupedCode('ABCDEFGHJK')).toBe('ABCDE FGHJK')
  expect(groupedCode(null)).toBe('')
})

it('tells a replacement by its window id', () => {
  const opened = { window_id: 'a' }
  expect(sameWindow(opened, { open: true, window_id: 'a' })).toBe(true)
  expect(sameWindow(opened, { open: true, window_id: 'b' })).toBe(false)
  expect(sameWindow(opened, { open: false, window_id: null })).toBe(false)
  expect(sameWindow({}, { open: true, window_id: undefined })).toBe(false)
})

describe('access', () => {
  it('says what a link may do now, and what was chosen', () => {
    expect(access({ can_write: false, writes: false }).label).toBe('Read-only')
    expect(access({ can_write: true, writes: true }).label).toBe('Read and write')
    const off = access({ can_write: true, writes: false, revoked_at: null })
    expect(off.label).toBe('Read-only now')
    expect(off.note).toMatch(/MCP_REMOTE_WRITES/)
    expect(access({ can_write: true, writes: false, revoked_at: 'x' }).note).toBeNull()
  })
})

it('names a link with no kept name by what is known', () => {
  expect(nameText({ client_name: 'X', client_registered: true })).toBeNull()
  expect(nameText({ client_name: null, client_registered: true })).toMatch(/no name/)
  expect(nameText({ client_name: null, client_registered: false })).toMatch(/registration deleted/)
})

describe('endedOutcome', () => {
  const mine = { window_id: 'a' }
  it('is null while its own window is open', () => {
    expect(endedOutcome(mine, { open: true, window_id: 'a' })).toBeNull()
  })
  it('takes an end for its own only when the end names its window', () => {
    for (const ended of ['linked', 'expired', 'cancelled', 'replaced', 'failed']) {
      expect(endedOutcome(mine, { open: false, ended, ended_window_id: 'a' })).toBe(ended)
      expect(endedOutcome(mine, { open: false, ended, ended_window_id: 'b' })).toBe('unknown')
    }
  })
  it('knows nothing of an end when another window is open, or none is recorded', () => {
    expect(endedOutcome(mine, { open: true, window_id: 'b' })).toBe('superseded')
    expect(endedOutcome(mine, { open: false, ended: null, ended_window_id: null })).toBe('unknown')
    expect(endedOutcome({}, { open: false, ended: 'linked', ended_window_id: undefined })).toBe('unknown')
  })
})
