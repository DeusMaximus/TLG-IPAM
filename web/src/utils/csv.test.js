import { describe, expect, it } from 'vitest'
import { BODY_MAX, IMPORT_ROWS_MAX, importSize } from './csv'

const bodyBytes = (hosts) => new TextEncoder().encode(JSON.stringify({ hosts })).length
const row = (i, notes = '') => ({ ip: `10.0.${i >> 8}.${i & 255}`, hostname: `h${i}`, notes })

describe('import size', () => {
  it('measures the body exactly as the request sends it, in UTF-8 bytes', () => {
    for (const hosts of [[], [row(1)], [row(1, '😀'.repeat(50)), row(2, 'é'), row(3)]]) {
      expect(importSize(hosts).bytes).toBe(bodyBytes(hosts))
    }
  })

  it('an import within the cap needs no split', () => {
    const hosts = Array.from({ length: IMPORT_ROWS_MAX }, (_, i) => row(i, 'n'.repeat(1000)))
    expect(bodyBytes(hosts)).toBeLessThanOrEqual(BODY_MAX)
    expect(importSize(hosts)).toEqual({ bytes: bodyBytes(hosts), splitAt: null })
  })

  it('over the cap, names the most rows every file of consecutive rows can hold', () => {
    // long notes in the middle of the file: the split must fit the heaviest run of rows
    const hosts = Array.from({ length: 900 }, (_, i) =>
      row(i, i >= 300 && i < 600 ? '😀'.repeat(4096) : 'n'.repeat(100)),
    )
    const { bytes, splitAt } = importSize(hosts)
    expect(bytes).toBe(bodyBytes(hosts))
    expect(bytes).toBeGreaterThan(BODY_MAX)
    expect(splitAt).toBeGreaterThan(0)
    for (let start = 0; start < hosts.length; start += splitAt) {
      expect(bodyBytes(hosts.slice(start, start + splitAt))).toBeLessThanOrEqual(BODY_MAX)
    }
    const heaviest = hosts.slice(300, 300 + splitAt + 1)
    expect(bodyBytes(heaviest)).toBeGreaterThan(BODY_MAX)
  })

  it('a split never holds more rows than one import takes', () => {
    const hosts = Array.from({ length: 3000 }, (_, i) => row(i, 'n'.repeat(800)))
    expect(bodyBytes(hosts)).toBeGreaterThan(BODY_MAX)
    expect(importSize(hosts).splitAt).toBe(IMPORT_ROWS_MAX)
  })

  it('a row over the cap by itself cannot be split to fit', () => {
    const hosts = [row(1), row(2, 'n'.repeat(BODY_MAX))]
    expect(importSize(hosts).splitAt).toBe(0)
  })
})
