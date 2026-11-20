import { describe, expect, it } from 'vitest'
import { formatServerTime, parseServerTime } from './time'

describe('parseServerTime', () => {
  it('reads the form the server sends, UTC with its offset', () => {
    expect(parseServerTime('2026-11-16T06:06:07.865439+00:00').toISOString()).toBe(
      '2026-11-16T06:06:07.865Z'
    )
  })

  it('reads an offset-less time as UTC, not local time', () => {
    expect(parseServerTime('2026-11-16T06:06:07.865439').toISOString()).toBe(
      '2026-11-16T06:06:07.865Z'
    )
  })

  it('keeps any other explicit offset', () => {
    expect(parseServerTime('2026-11-16T06:06:07+02:00').toISOString()).toBe(
      '2026-11-16T04:06:07.000Z'
    )
    expect(parseServerTime('2026-11-16T06:06:07Z').toISOString()).toBe(
      '2026-11-16T06:06:07.000Z'
    )
  })

  it('returns null for nothing or garbage', () => {
    expect(parseServerTime(null)).toBeNull()
    expect(parseServerTime('not a date')).toBeNull()
  })
})

describe('formatServerTime', () => {
  it('shows the value itself when it is not a time, and nothing for nothing', () => {
    expect(formatServerTime('not a date')).toBe('not a date')
    expect(formatServerTime(null)).toBe('')
  })
})
