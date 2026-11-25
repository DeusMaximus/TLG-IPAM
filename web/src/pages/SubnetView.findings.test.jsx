import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import * as ipam from '../api/ipam'
import { REFRESH_MS } from '../components/ScanSettings'
import { ToastProvider, TreeProvider } from '../context'
import SubnetView from './SubnetView'

vi.mock('../api/ipam', () => ({
  listSections: vi.fn(),
  listSubnets: vi.fn(),
  listDeviceTypes: vi.fn(),
  getSubnet: vi.fn(),
  listHosts: vi.fn(),
  bulkDeleteHosts: vi.fn(),
  deleteSubnet: vi.fn(),
  createReservedRange: vi.fn(),
  updateReservedRange: vi.fn(),
  deleteReservedRange: vi.fn(),
  listScanTargets: vi.fn(),
  createScanTarget: vi.fn(),
  updateScanTarget: vi.fn(),
  requestScan: vi.fn(),
  listAgents: vi.fn(),
  listFindings: vi.fn(),
  acceptFindings: vi.fn(),
  dismissFindings: vi.fn(),
  HOST_STATUSES: ['active', 'reserved', 'dhcp', 'offline'],
}))

vi.mock('../components/DeviceIcon', () => ({ default: ({ type }) => <span>{type}</span> }))

const clone = (v) => JSON.parse(JSON.stringify(v))

// The scope the server would report for 10.20.0.0/24 with the pool (.100–.200) not
// scanned, each range with its bounds as integers (services/scans.target_out). The
// integers are written out here, as the server sends them: nothing in web/ parses an
// address, these fixtures included.
const SCOPE_WITHOUT_POOL = [
  { start: '10.20.0.1', end: '10.20.0.99', start_int: 169082881, end_int: 169082979 },
  { start: '10.20.0.201', end: '10.20.0.254', start_int: 169083081, end_int: 169083134 },
]
const SCOPE_WITH_POOL = [
  { start: '10.20.0.1', end: '10.20.0.254', start_int: 169082881, end_int: 169083134 },
]

// each fixture address's `ip_int`, as the server would send it
const IP_INT = {
  '10.20.0.1': 169082881,
  '10.20.0.5': 169082885,
  '10.20.0.6': 169082886,
  '10.20.0.39': 169082919,
  '10.20.0.40': 169082920,
  '10.20.0.41': 169082921,
  '10.20.0.45': 169082925,
  '10.20.0.99': 169082979,
  '10.20.0.100': 169082980,
  '10.20.0.150': 169083030,
  '10.20.0.151': 169083031,
  '10.20.0.200': 169083080,
  '10.20.0.201': 169083081,
  '10.20.0.254': 169083134,
}

const ipInt = (ip) => {
  if (!(ip in IP_INT)) throw new Error(`add ${ip} to IP_INT`)
  return IP_INT[ip]
}

function host(id, ip, extra = {}) {
  return {
    id,
    subnet_id: 'sub-1',
    ip,
    ip_int: ipInt(ip),
    hostname: `host-${id}`,
    domain: null,
    fqdn: `host-${id}.home.arpa`,
    mac: null,
    device_type: 'server',
    status: 'active',
    owner: null,
    notes: null,
    last_seen: null,
    last_seen_source: null,
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-01T00:00:00',
    ...extra,
  }
}

function finding(type, ip, observed, extra = {}) {
  return {
    subnet_id: 'sub-1',
    ip,
    type,
    observed,
    ip_int: ipInt(ip),
    host_id: null,
    dynamic: false,
    proposed: {},
    fingerprint: `fp-${type}-${ip}`,
    ...extra,
  }
}

let db

function seed({ subnet = {}, target = {}, hosts, findings } = {}) {
  db = {
    subnet: {
      id: 'sub-1',
      section_id: 'sec-1',
      cidr: '10.20.0.0/24',
      vlan_id: 20,
      name: 'Lab',
      domain: 'home.arpa',
      gateway: null,
      dns_servers: null,
      dhcp_start: '10.20.0.100',
      dhcp_end: '10.20.0.200',
      notes: null,
      used: 3,
      usable: 254,
      free: 150,
      reserved_ranges: [],
      ...subnet,
    },
    target:
      target === null
        ? null
        : {
            id: 'tgt-1',
            subnet_id: 'sub-1',
            agent_id: 'agent-1',
            enabled: true,
            methods: ['icmp', 'arp', 'rdns'],
            scan_dhcp_pool: false,
            interval_minutes: 60,
            scan_requested_at: null,
            current_job_id: null,
            job_issued_at: null,
            job_methods: null,
            created_at: '2026-11-01T06:00:00',
            last_scan_at: '2026-11-16T06:00:00',
            last_scan_duration_ms: 1000,
            last_scan_complete: true,
            scope: SCOPE_WITHOUT_POOL,
            ...target,
          },
    hosts: hosts ?? [
      host('h1', '10.20.0.5', { mac: 'aa:aa:aa:aa:aa:01' }),
      host('h2', '10.20.0.6'),
    ],
    findings: findings ?? [
      finding('mac_mismatch', '10.20.0.5', 'bb:bb:bb:bb:bb:01', {
        host_id: 'h1',
        proposed: { mac: 'bb:bb:bb:bb:bb:01' },
      }),
      finding('unknown_host', '10.20.0.40', 'cc:cc:cc:cc:cc:40', {
        proposed: { status: 'active', mac: 'cc:cc:cc:cc:cc:40', hostname: 'printer' },
      }),
      finding('unknown_host', '10.20.0.41', '', { proposed: { status: 'active' } }),
    ],
  }
}

const subnetOut = () => ({
  ...clone(db.subnet),
  scan_dhcp_pool: db.target ? db.target.scan_dhcp_pool : null,
})

function wireServer() {
  ipam.listSections.mockImplementation(async () => ({ data: [{ id: 'sec-1', name: 'Home' }] }))
  ipam.listDeviceTypes.mockImplementation(async () => ({ data: [] }))
  ipam.listSubnets.mockImplementation(async () => ({ data: [subnetOut()] }))
  ipam.getSubnet.mockImplementation(async () => ({ data: subnetOut() }))
  ipam.listHosts.mockImplementation(async () => ({ data: clone(db.hosts), total: db.hosts.length }))
  ipam.listAgents.mockImplementation(async () => ({
    data: [{ id: 'agent-1', name: 'lab-vlan20', state: 'active' }],
  }))
  ipam.listScanTargets.mockImplementation(async () => ({ data: db.target ? [clone(db.target)] : [] }))
  ipam.listFindings.mockImplementation(async (subnetId, { includeDynamic } = {}) => {
    const data = db.findings.filter((f) => f.subnet_id === subnetId && (includeDynamic || !f.dynamic))
    return { data: clone(data), total: data.length }
  })
}

function GoTo() {
  const navigate = useNavigate()
  return <button onClick={() => navigate('/subnets/sub-2')}>go to sub-2</button>
}

function renderView() {
  return render(
    <MemoryRouter initialEntries={['/subnets/sub-1']}>
      <ToastProvider>
        <TreeProvider>
          <GoTo />
          <Routes>
            <Route path="/subnets/:subnetId" element={<SubnetView />} />
          </Routes>
        </TreeProvider>
      </ToastProvider>
    </MemoryRouter>
  )
}

const findPanel = () => screen.findByRole('region', { name: 'Findings' })
const panel = () => screen.getByRole('region', { name: 'Findings' })
const hostRow = (ip) =>
  within(screen.getByRole('table', { name: 'Hosts' }))
    .getAllByRole('row')
    .find((r) => within(r).queryByText(ip))

beforeEach(() => {
  localStorage.setItem('tlg-ipam-key', 'test-key')
  seed()
  wireServer()
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
  localStorage.clear()
})

describe('acceptance: a skipped accept shows its reason', () => {
  it('sends the key with its fingerprint, and shows the server’s reason when it is skipped', async () => {
    const user = userEvent.setup()
    ipam.acceptFindings.mockResolvedValue({
      data: {
        accepted: 0,
        skipped: 1,
        errors: 0,
        results: [
          { index: 0, subnet_id: 'sub-1', ip: '10.20.0.5', type: 'mac_mismatch', status: 'skipped', reason: 'changed' },
        ],
      },
    })
    renderView()
    const group = await within(await findPanel()).findByRole('group', { name: 'MAC mismatch' })
    const listsBefore = ipam.listFindings.mock.calls.length

    await user.click(within(group).getByRole('button', { name: 'Replace MAC 10.20.0.5' }))

    // the key exactly as listed, its fingerprint included, and nothing else
    expect(ipam.acceptFindings).toHaveBeenCalledWith([
      {
        subnet_id: 'sub-1',
        ip: '10.20.0.5',
        type: 'mac_mismatch',
        observed: 'bb:bb:bb:bb:bb:01',
        fingerprint: 'fp-mac_mismatch-10.20.0.5',
      },
    ])
    const status = await within(panel()).findByRole('status')
    expect(status).toHaveTextContent('0 accepted, 1 not accepted.')
    expect(status).toHaveTextContent('10.20.0.5 (MAC mismatch): skipped, changed')
    expect(status).toHaveTextContent('it changed since it was listed, so nothing was written')
    // the list is reloaded, so it shows the finding as it is now
    await waitFor(() => expect(ipam.listFindings.mock.calls.length).toBeGreaterThan(listsBefore))
    // the reason stays on screen after the reload, until it is closed
    await waitFor(() => expect(within(panel()).getByRole('status')).toBeInTheDocument())
    await user.click(within(panel()).getByRole('button', { name: 'Close result' }))
    expect(within(panel()).queryByRole('status')).toBeNull()
  })

  it('shows a per-record error with its reason, and a reason it has no wording for as sent', async () => {
    const user = userEvent.setup()
    ipam.acceptFindings.mockResolvedValue({
      data: {
        accepted: 1,
        skipped: 1,
        errors: 1,
        results: [
          { index: 0, ip: '10.20.0.40', type: 'unknown_host', status: 'accepted', host_id: 'new' },
          { index: 1, ip: '10.20.0.41', type: 'unknown_host', status: 'error', reason: 'ip: 10.20.0.41 is already allocated' },
        ],
      },
    })
    renderView()
    const group = await within(await findPanel()).findByRole('group', { name: 'Unknown hosts' })
    await user.click(within(group).getByRole('button', { name: 'Accept all 2' }))
    await user.click(within(group).getByRole('button', { name: /Confirm: create host × 2/ }))
    expect(ipam.acceptFindings).toHaveBeenCalledTimes(1)
    expect(ipam.acceptFindings.mock.calls[0][0].map((k) => k.ip)).toEqual(['10.20.0.40', '10.20.0.41'])
    const status = await within(panel()).findByRole('status')
    expect(status).toHaveTextContent('1 accepted, 1 not accepted.')
    expect(status).toHaveTextContent('10.20.0.41 (Unknown hosts): error, ip: 10.20.0.41 is already allocated')
  })

  it('a request refused whole (4xx) is reported as not sent, with nothing reloaded', async () => {
    const user = userEvent.setup()
    ipam.dismissFindings.mockRejectedValue(new ApiError('VALIDATION_ERROR', 'findings: too many', 422))
    renderView()
    const group = await within(await findPanel()).findByRole('group', { name: 'MAC mismatch' })
    const lists = ipam.listFindings.mock.calls.length
    await user.click(within(group).getByRole('button', { name: 'Dismiss 10.20.0.5' }))
    const status = await within(panel()).findByRole('status')
    expect(status).toHaveTextContent('0 dismissed, 1 not sent.')
    expect(status).toHaveTextContent('The request failed: findings: too many')
    expect(status).not.toHaveTextContent('unknown')
    expect(ipam.listFindings.mock.calls.length).toBe(lists)
  })

  it('an accept that fails with no answer is reported as unknown, and reloads the hosts and the tree', async () => {
    const user = userEvent.setup()
    // the server committed the record, then failed (or its answer was lost)
    ipam.acceptFindings.mockImplementation(async () => {
      db.hosts[0].mac = 'bb:bb:bb:bb:bb:01'
      db.findings = db.findings.filter((f) => f.type !== 'mac_mismatch')
      throw new ApiError('HTTP_502', 'Bad Gateway', 502)
    })
    renderView()
    const group = await within(await findPanel()).findByRole('group', { name: 'MAC mismatch' })
    await waitFor(() => expect(ipam.listSubnets).toHaveBeenCalled())
    const tree = ipam.listSubnets.mock.calls.length
    const hosts = ipam.listHosts.mock.calls.length
    await user.click(within(group).getByRole('button', { name: 'Replace MAC 10.20.0.5' }))
    const status = await within(panel()).findByRole('status')
    expect(status).toHaveTextContent('0 accepted, 1 unknown.')
    expect(status).toHaveTextContent('The request failed: Bad Gateway — 1 finding(s) were sent with no answer')
    // what the server wrote shows
    await waitFor(() => expect(within(hostRow('10.20.0.5')).getByText('bb:bb:bb:bb:bb:01')).toBeInTheDocument())
    expect(ipam.listSubnets.mock.calls.length).toBeGreaterThan(tree)
    expect(ipam.listHosts.mock.calls.length).toBeGreaterThan(hosts)
    expect(within(panel()).queryByRole('group', { name: 'MAC mismatch' })).toBeNull()
  })

  it('a dismiss that fails with no answer is reported as unknown, and reloads the findings', async () => {
    const user = userEvent.setup()
    ipam.dismissFindings.mockRejectedValue(new TypeError('Failed to fetch'))
    renderView()
    const group = await within(await findPanel()).findByRole('group', { name: 'MAC mismatch' })
    const lists = ipam.listFindings.mock.calls.length
    await user.click(within(group).getByRole('button', { name: 'Dismiss 10.20.0.5' }))
    const status = await within(panel()).findByRole('status')
    expect(status).toHaveTextContent('0 dismissed, 1 unknown.')
    await waitFor(() => expect(ipam.listFindings.mock.calls.length).toBeGreaterThan(lists))
  })
})

describe('forward DNS findings (#20)', () => {
  it('lists an fdns_mismatch as informational: what the name resolves to, nothing to accept, and dismiss', async () => {
    const user = userEvent.setup()
    seed({
      findings: [
        finding('fdns_mismatch', '10.20.0.5', 'unresolved', { host_id: 'h1' }),
        finding('fdns_mismatch', '10.20.0.6', '10.20.0.99, 10.20.0.100', { host_id: 'h2' }),
      ],
    })
    ipam.dismissFindings.mockResolvedValue({
      data: { dismissed: 1, skipped: 0, errors: 0, results: [{ index: 0, status: 'dismissed' }] },
    })
    renderView()
    const group = await within(await findPanel()).findByRole('group', { name: 'Forward DNS mismatch' })
    expect(within(group).getByText('does not resolve')).toBeInTheDocument()
    expect(within(group).getByText('resolves to 10.20.0.99, 10.20.0.100')).toBeInTheDocument()
    expect(within(group).getByText('host-h1.home.arpa at 10.20.0.5')).toBeInTheDocument()
    // the server refuses an accept: there is no accept button, single or bulk
    expect(within(group).queryByRole('button', { name: /Accept/ })).toBeNull()
    expect(within(group).getAllByRole('button')).toHaveLength(2)
    await user.click(within(group).getByRole('button', { name: 'Dismiss 10.20.0.5' }))
    expect(ipam.dismissFindings).toHaveBeenCalledWith([
      {
        subnet_id: 'sub-1',
        ip: '10.20.0.5',
        type: 'fdns_mismatch',
        observed: 'unresolved',
        fingerprint: 'fp-fdns_mismatch-10.20.0.5',
      },
    ])
    expect(ipam.acceptFindings).not.toHaveBeenCalled()
  })
})

describe('findings panel', () => {
  it('shows a count badge and groups findings by type, in a fixed order', async () => {
    renderView()
    expect(await screen.findByRole('button', { name: '3 scan findings' })).toBeInTheDocument()
    const groups = within(await findPanel()).getAllByRole('group')
    expect(groups.map((g) => g.getAttribute('aria-label'))).toEqual(['Unknown hosts', 'MAC mismatch'])
    const mac = within(panel()).getByRole('group', { name: 'MAC mismatch' })
    // recorded, seen, and what accepting writes
    expect(within(mac).getByText('aa:aa:aa:aa:aa:01')).toBeInTheDocument()
    expect(within(mac).getAllByText('bb:bb:bb:bb:bb:01')).toHaveLength(2)
    // one finding: no bulk button
    expect(within(mac).queryByRole('button', { name: /Accept all/ })).toBeNull()
    const unknown = within(panel()).getByRole('group', { name: 'Unknown hosts' })
    expect(within(unknown).getByText('no MAC')).toBeInTheDocument()
    expect(within(unknown).getAllByText('no host record')).toHaveLength(2)
  })

  it('an accepted finding reloads the hosts, the findings and the tree', async () => {
    const user = userEvent.setup()
    ipam.acceptFindings.mockImplementation(async () => {
      db.hosts[0].mac = 'bb:bb:bb:bb:bb:01'
      db.findings = db.findings.filter((f) => f.type !== 'mac_mismatch')
      return {
        data: {
          accepted: 1,
          skipped: 0,
          errors: 0,
          results: [{ index: 0, ip: '10.20.0.5', type: 'mac_mismatch', status: 'accepted', host_id: 'h1' }],
        },
      }
    })
    renderView()
    const group = await within(await findPanel()).findByRole('group', { name: 'MAC mismatch' })
    const tree = ipam.listSubnets.mock.calls.length
    const hosts = ipam.listHosts.mock.calls.length
    await user.click(within(group).getByRole('button', { name: 'Replace MAC 10.20.0.5' }))
    await waitFor(() =>
      expect(within(panel()).queryByRole('group', { name: 'MAC mismatch' })).toBeNull()
    )
    expect(ipam.listSubnets.mock.calls.length).toBeGreaterThan(tree)
    expect(ipam.listHosts.mock.calls.length).toBeGreaterThan(hosts)
    expect(await screen.findByRole('button', { name: '2 scan findings' })).toBeInTheDocument()
    expect(within(hostRow('10.20.0.5')).getByText('bb:bb:bb:bb:bb:01')).toBeInTheDocument()
  })

  it('dismiss sends the key and reloads only the findings', async () => {
    const user = userEvent.setup()
    ipam.dismissFindings.mockImplementation(async () => {
      db.findings = db.findings.filter((f) => f.ip !== '10.20.0.41')
      return {
        data: {
          dismissed: 1,
          skipped: 0,
          errors: 0,
          results: [{ index: 0, ip: '10.20.0.41', type: 'unknown_host', status: 'dismissed' }],
        },
      }
    })
    renderView()
    const group = await within(await findPanel()).findByRole('group', { name: 'Unknown hosts' })
    await waitFor(() => expect(ipam.listSubnets).toHaveBeenCalled())
    const tree = ipam.listSubnets.mock.calls.length
    await user.click(within(group).getByRole('button', { name: 'Dismiss 10.20.0.41' }))
    expect(ipam.dismissFindings).toHaveBeenCalledWith([
      {
        subnet_id: 'sub-1',
        ip: '10.20.0.41',
        type: 'unknown_host',
        observed: '',
        fingerprint: 'fp-unknown_host-10.20.0.41',
      },
    ])
    expect(await screen.findByRole('button', { name: '2 scan findings' })).toBeInTheDocument()
    expect(ipam.listSubnets.mock.calls.length).toBe(tree)
  })

  it('a reload that fails after a write says the list may be out of date', async () => {
    const user = userEvent.setup()
    ipam.dismissFindings.mockImplementation(async () => {
      ipam.listFindings.mockRejectedValue(new ApiError('HTTP_502', 'Bad Gateway', 502))
      return {
        data: {
          dismissed: 1,
          skipped: 0,
          errors: 0,
          results: [{ index: 0, ip: '10.20.0.41', type: 'unknown_host', status: 'dismissed' }],
        },
      }
    })
    renderView()
    const group = await within(await findPanel()).findByRole('group', { name: 'Unknown hosts' })
    expect(within(panel()).queryByRole('alert')).toBeNull()
    await user.click(within(group).getByRole('button', { name: 'Dismiss 10.20.0.41' }))
    expect(await within(panel()).findByRole('alert')).toHaveTextContent(
      'Findings could not be reloaded: Bad Gateway. The list below may be out of date.'
    )
    expect(within(panel()).getByRole('status')).toHaveTextContent('1 dismissed.')
  })

  it('a host list or scan target reload that fails says so', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderView()
    await within(await findPanel()).findByRole('group', { name: 'MAC mismatch' })
    await waitFor(() => expect(ipam.listScanTargets).toHaveBeenCalled())
    ipam.listScanTargets.mockRejectedValue(new ApiError('HTTP_502', 'Bad Gateway', 502))
    await vi.advanceTimersByTimeAsync(REFRESH_MS)
    const scanning = screen.getByRole('region', { name: 'Scanning' })
    expect(await within(scanning).findByRole('alert')).toHaveTextContent('Could not be reloaded: Bad Gateway')

    ipam.listHosts.mockRejectedValue(new ApiError('HTTP_502', 'Bad Gateway', 502))
    ipam.dismissFindings.mockResolvedValue({ data: { dismissed: 1, skipped: 0, errors: 0, results: [] } })
    ipam.acceptFindings.mockResolvedValue({
      data: { accepted: 1, skipped: 0, errors: 0, results: [{ index: 0, status: 'accepted' }] },
    })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await user.click(within(panel()).getByRole('button', { name: 'Replace MAC 10.20.0.5' }))
    expect(
      await screen.findByText('Could not be reloaded: Bad Gateway. The list may be out of date.')
    ).toBeInTheDocument()
    // the last list stays
    expect(hostRow('10.20.0.5')).toBeTruthy()
  })

  it('keeps the answer on screen when the last finding of a subnet with no scan target goes', async () => {
    seed({
      target: null,
      findings: [finding('unknown_host', '10.20.0.41', '', { proposed: { status: 'active' } })],
    })
    ipam.acceptFindings.mockImplementation(async () => {
      db.findings = []
      return {
        data: {
          accepted: 1,
          skipped: 0,
          errors: 0,
          results: [{ index: 0, ip: '10.20.0.41', type: 'unknown_host', status: 'accepted', host_id: 'h9' }],
        },
      }
    })
    const user = userEvent.setup()
    renderView()
    const group = await within(await findPanel()).findByRole('group', { name: 'Unknown hosts' })
    await user.click(within(group).getByRole('button', { name: 'Create host 10.20.0.41' }))
    expect(await within(panel()).findByText(/None: scans agree with the records/)).toBeInTheDocument()
    expect(within(panel()).getByRole('status')).toHaveTextContent('1 accepted.')
  })

  it('makes a domain override visible before it is accepted', async () => {
    seed({
      hosts: [host('h1', '10.20.0.5', { hostname: 'nas', fqdn: 'nas.home.arpa' })],
      findings: [
        finding('name_mismatch', '10.20.0.5', 'nas.example.com', {
          host_id: 'h1',
          proposed: { hostname: 'nas', domain: 'example.com' },
        }),
        finding('unknown_host', '10.20.0.40', '', {
          proposed: { status: 'active', hostname: 'cam', domain: 'iot.example.com' },
        }),
        finding('unknown_host', '10.20.0.41', '', {
          proposed: { status: 'active', hostname: 'tv' },
        }),
      ],
    })
    const user = userEvent.setup()
    renderView()
    const name = await within(await findPanel()).findByRole('group', { name: 'Name mismatch' })
    expect(within(name).getByTestId('domain-override')).toHaveTextContent(
      'Sets a domain override: example.com, so the FQDN is nas.example.com. The host follows the subnet’s domain, home.arpa, now.'
    )
    expect(within(name).getByText('nas.home.arpa')).toBeInTheDocument()
    const unknown = within(panel()).getByRole('group', { name: 'Unknown hosts' })
    expect(within(unknown).getAllByTestId('domain-override')).toHaveLength(1)
    // and the bulk confirmation counts them
    await user.click(within(unknown).getByRole('button', { name: 'Accept all 2' }))
    expect(within(unknown).getByText('1 set a domain override.')).toBeInTheDocument()
  })

  it('renders what the network reported as text, never as markup', async () => {
    seed({
      findings: [
        finding('name_missing', '10.20.0.6', '<img src=x onerror=alert(1)>', {
          host_id: 'h2',
          proposed: { hostname: '<b>x</b>' },
        }),
      ],
    })
    const { container } = renderView()
    const group = await within(await findPanel()).findByRole('group', { name: 'Name missing' })
    expect(within(group).getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(within(group).getByText('<b>x</b>')).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
  })

  it('shows a stale finding’s time read as UTC, and accepting it marks the host offline', async () => {
    seed({
      findings: [
        finding('stale', '10.20.0.6', 'never', { host_id: 'h2', proposed: { status: 'offline' } }),
      ],
    })
    renderView()
    const group = await within(await findPanel()).findByRole('group', { name: 'Stale' })
    expect(within(group).getByText('never seen')).toBeInTheDocument()
    expect(within(group).getByText('status active')).toBeInTheDocument()
    expect(within(group).getByRole('button', { name: 'Mark offline 10.20.0.6' })).toBeInTheDocument()
  })

  it('has no panel or badge for a subnet with no scan target and no findings', async () => {
    seed({ target: null, findings: [] })
    renderView()
    expect(await screen.findByRole('region', { name: 'Scanning' })).toBeInTheDocument()
    await waitFor(() => expect(ipam.listFindings).toHaveBeenCalled())
    expect(screen.queryByRole('region', { name: 'Findings' })).toBeNull()
    expect(screen.queryByRole('button', { name: /scan finding/ })).toBeNull()
  })

  it('says so when scans agree with the records', async () => {
    seed({ findings: [] })
    renderView()
    expect(await within(await findPanel()).findByText(/None: scans agree with the records/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /scan finding/ })).toBeNull()
  })

  it('does not show the last subnet’s findings while the next one loads', async () => {
    const user = userEvent.setup()
    let release
    renderView()
    await within(await findPanel()).findByRole('group', { name: 'MAC mismatch' })
    ipam.getSubnet.mockImplementation(async (id) => ({ data: { ...subnetOut(), id, name: 'Lab 2' } }))
    ipam.listFindings.mockImplementation(() => new Promise((resolve) => (release = resolve)))
    await user.click(screen.getByRole('button', { name: 'go to sub-2' }))
    await screen.findByRole('heading', { name: 'Lab 2' })
    expect(screen.queryByRole('group', { name: 'MAC mismatch' })).toBeNull()
    expect(screen.queryByRole('button', { name: /scan finding/ })).toBeNull()
    release({ data: [] })
  })
})

describe('show dynamic', () => {
  const dynamic = () =>
    finding('unknown_host', '10.20.0.150', 'dd:dd:dd:dd:dd:50', {
      dynamic: true,
      proposed: { status: 'dhcp', mac: 'dd:dd:dd:dd:dd:50' },
    })

  it('is not offered while pool scanning is off', async () => {
    renderView()
    await within(await findPanel()).findByRole('group', { name: 'Unknown hosts' })
    expect(within(panel()).queryByRole('checkbox', { name: /Show dynamic/ })).toBeNull()
    expect(ipam.listFindings).toHaveBeenCalledWith('sub-1', { includeDynamic: false })
  })

  it('asks for dynamic findings when ticked, and marks them', async () => {
    seed({ target: { scan_dhcp_pool: true, scope: SCOPE_WITH_POOL } })
    db.findings.push(dynamic())
    const user = userEvent.setup()
    renderView()
    const box = await within(await findPanel()).findByRole('checkbox', { name: /Show dynamic/ })
    expect(box).toHaveClass('tlg-check')
    expect(box).not.toBeChecked()
    expect(within(panel()).queryByText('10.20.0.150')).toBeNull()

    await user.click(box)

    expect(ipam.listFindings).toHaveBeenLastCalledWith('sub-1', { includeDynamic: true })
    const row = (await within(panel()).findByText('10.20.0.150')).closest('tr')
    expect(within(row).getByText('dynamic')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '4 scan findings' })).toBeInTheDocument()
  })
})

describe('last seen on host rows', () => {
  it('reads dates as UTC with their source, and "not scanned" outside the scope the server reports', async () => {
    const ago = new Date(Date.UTC(2026, 10, 15, 23, 30))
    seed({
      hosts: [
        host('h1', '10.20.0.5', {
          last_seen: ago.toISOString().replace('Z', ''), // as SQLite returns it
          last_seen_source: 'scan',
        }),
        host('h2', '10.20.0.6'),
        // in the pool, which is not scanned: outside every range of the target's scope
        host('h3', '10.20.0.150'),
        host('h4', '10.20.0.151', { last_seen: '2026-10-01T12:00:00', last_seen_source: 'manual' }),
      ],
      findings: [],
    })
    renderView()
    await screen.findByRole('table', { name: 'Hosts' })
    await waitFor(() => expect(ipam.listScanTargets).toHaveBeenCalled())
    await screen.findByRole('region', { name: 'Findings' }) // the target has loaded
    const seen = within(hostRow('10.20.0.5')).getByText(ago.toLocaleDateString())
    expect(seen.closest('[role="cell"]')).toHaveAttribute('title', `${ago.toLocaleString()} (seen by a scan)`)
    expect(within(hostRow('10.20.0.6')).getAllByRole('cell').at(-1)).toHaveTextContent(/^—$/)
    await waitFor(() =>
      expect(within(hostRow('10.20.0.150')).getAllByRole('cell').at(-1)).toHaveTextContent(/^not scanned$/)
    )
    expect(hostRow('10.20.0.151')).toHaveTextContent(
      `${new Date(Date.UTC(2026, 9, 1, 12)).toLocaleDateString()} · not scanned`
    )
    expect(within(hostRow('10.20.0.5')).queryByText(/not scanned/)).toBeNull()
  })

  it('a host in a range excluded from scanning reads "not scanned"; one beside it does not', async () => {
    // the scope the server reports with 10.20.0.40–10.20.0.44 excluded (pool scanned)
    const scope = [
      { start: '10.20.0.1', end: '10.20.0.39', start_int: ipInt('10.20.0.1'), end_int: ipInt('10.20.0.39') },
      { start: '10.20.0.45', end: '10.20.0.254', start_int: ipInt('10.20.0.45'), end_int: ipInt('10.20.0.254') },
    ]
    seed({
      target: { scan_dhcp_pool: true, scope },
      hosts: [host('h1', '10.20.0.41'), host('h2', '10.20.0.45'), host('h3', '10.20.0.150')],
      findings: [],
    })
    renderView()
    await screen.findByRole('table', { name: 'Hosts' })
    await waitFor(() =>
      expect(within(hostRow('10.20.0.41')).getAllByRole('cell').at(-1)).toHaveTextContent(/^not scanned$/)
    )
    expect(within(hostRow('10.20.0.45')).getAllByRole('cell').at(-1)).toHaveTextContent(/^—$/)
    // the pool is scanned here, so a host in it is in scope
    expect(within(hostRow('10.20.0.150')).getAllByRole('cell').at(-1)).toHaveTextContent(/^—$/)
  })

  it('claims nothing about a host when the scope ranges carry no integer bounds', async () => {
    seed({
      target: { scope: [{ start: '10.20.0.1', end: '10.20.0.99' }] },
      hosts: [host('h1', '10.20.0.150')],
      findings: [],
    })
    renderView()
    await screen.findByRole('table', { name: 'Hosts' })
    await screen.findByRole('region', { name: 'Findings' }) // the target has loaded
    expect(within(hostRow('10.20.0.150')).getAllByRole('cell').at(-1)).toHaveTextContent(/^—$/)
    expect(screen.queryByText('not scanned')).toBeNull()
  })

  it('a date set by hand on a host of a subnet with no scan target reads as not scanned', async () => {
    seed({
      target: null,
      findings: [],
      hosts: [host('h1', '10.20.0.5', { last_seen: '2026-10-01T12:00:00', last_seen_source: 'manual' })],
    })
    renderView()
    await screen.findByRole('table', { name: 'Hosts' })
    await waitFor(() =>
      expect(hostRow('10.20.0.5')).toHaveTextContent(
        `${new Date(Date.UTC(2026, 9, 1, 12)).toLocaleDateString()} · not scanned`
      )
    )
  })

  it('reads "not scanned" for every host when the subnet has no scan target', async () => {
    seed({ target: null, findings: [] })
    renderView()
    await screen.findByText('10.20.0.5')
    await waitFor(() => expect(within(hostRow('10.20.0.5')).getByText('not scanned')).toBeInTheDocument())
    expect(within(hostRow('10.20.0.6')).getByText('not scanned')).toBeInTheDocument()
  })

  it('claims nothing about scope when the scan targets could not be loaded', async () => {
    ipam.listScanTargets.mockRejectedValue(new ApiError('HTTP_502', 'Bad Gateway', 502))
    renderView()
    await screen.findByRole('table', { name: 'Hosts' })
    await waitFor(() => expect(ipam.listScanTargets).toHaveBeenCalled())
    expect(screen.queryByText('not scanned')).toBeNull()
    // the last-seen cell: no date, and no claim either way
    expect(within(hostRow('10.20.0.6')).getAllByRole('cell').at(-1)).toHaveTextContent(/^—$/)
  })

  it('a scan that finishes while the view is open reloads the hosts and the findings', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderView()
    await within(await findPanel()).findByRole('group', { name: 'MAC mismatch' })
    await waitFor(() => expect(ipam.listScanTargets).toHaveBeenCalled())
    const hosts = ipam.listHosts.mock.calls.length
    const findings = ipam.listFindings.mock.calls.length

    // nothing changed: the periodic reload of the target reloads nothing else
    await vi.advanceTimersByTimeAsync(REFRESH_MS)
    expect(ipam.listHosts.mock.calls.length).toBe(hosts)
    expect(ipam.listFindings.mock.calls.length).toBe(findings)

    db.target.last_scan_at = '2026-11-16T07:00:00'
    db.hosts[1].last_seen = '2026-11-16T07:00:05'
    db.hosts[1].last_seen_source = 'scan'
    await vi.advanceTimersByTimeAsync(REFRESH_MS)
    await waitFor(() => expect(ipam.listHosts.mock.calls.length).toBe(hosts + 1))
    expect(ipam.listFindings.mock.calls.length).toBe(findings + 1)
    expect(
      await within(hostRow('10.20.0.6')).findByText(new Date(Date.UTC(2026, 10, 16, 7, 0, 5)).toLocaleDateString())
    ).toBeInTheDocument()
  })
})
