// The activity feed and "Last seen" show a server time as the browser's local time
// (#65). The browser's zone is set away from UTC for this file: Asia/Kolkata, +05:30
// with no daylight saving, where the instant below falls on the next day.
import { render, screen } from '@testing-library/react'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import ActivityItem from '../components/ActivityItem'
import HostDrawer from '../components/HostDrawer'
import HostTable from '../components/HostTable'

vi.mock('../components/DeviceIcon', () => ({ default: ({ type }) => <span>{type}</span> }))
vi.mock('../context', () => ({
  useToast: () => () => {},
  useTree: () => ({ subnets: [], deviceTypes: [], refreshTree: () => {} }),
}))

const ZONE = 'Asia/Kolkata'
// 2026-11-15 20:00 UTC is 2026-11-16 01:30 in Kolkata
const INSTANT = new Date(Date.UTC(2026, 10, 15, 20, 0, 0))
// as the server sends it, and as SQLite returned it before the server added the offset
const FORMS = ['2026-11-15T20:00:00+00:00', '2026-11-15T20:00:00']

const local = (options) => INSTANT.toLocaleString(undefined, { ...options, timeZone: ZONE })
const LOCAL_TIME = local()
const LOCAL_DATE = INSTANT.toLocaleDateString(undefined, { timeZone: ZONE })

let previous
beforeAll(() => {
  previous = process.env.TZ
  process.env.TZ = ZONE
})
afterAll(() => {
  if (previous === undefined) delete process.env.TZ
  else process.env.TZ = previous
})

const host = (last_seen) => ({
  id: 'h1',
  ip: '10.0.0.5',
  ip_int: 167772165,
  subnet_id: 'subnet-1',
  hostname: 'pbs-01',
  fqdn: null,
  mac: null,
  device_type: 'server',
  status: 'active',
  last_seen,
  last_seen_source: 'scan',
})

describe('server times in a zone away from UTC', () => {
  it('runs in that zone, where local time is not UTC wall-clock time', () => {
    expect(INSTANT.getTimezoneOffset()).toBe(-330)
    expect(INSTANT.getDate()).toBe(16)
    expect(LOCAL_TIME).not.toBe(INSTANT.toLocaleString(undefined, { timeZone: 'UTC' }))
    expect(LOCAL_DATE).not.toBe(INSTANT.toLocaleDateString(undefined, { timeZone: 'UTC' }))
  })

  it.each(FORMS)('the activity feed shows %s as local time', (ts) => {
    render(
      <ActivityItem change={{ id: 'c1', ts, summary: 'host pbs-01 created', source: 'ui', actor: null }} />,
    )
    expect(screen.getByText(LOCAL_TIME)).toBeInTheDocument()
  })

  it.each(FORMS)('the hosts table shows "Last seen" %s as the local date', (lastSeen) => {
    render(<HostTable hosts={[host(lastSeen)]} />)
    const cell = screen.getByText(LOCAL_DATE)
    expect(cell.closest('[title]')).toHaveAttribute('title', `${LOCAL_TIME} (seen by a scan)`)
  })

  it.each(FORMS)('the host drawer shows "Last seen" %s as local time', (lastSeen) => {
    render(<HostDrawer host={host(lastSeen)} onClose={() => {}} onSaved={() => {}} />)
    expect(screen.getByText(`Last seen ${LOCAL_TIME} (by a scan)`)).toBeInTheDocument()
  })
})
