import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import * as ipam from '../api/ipam'
import { REFRESH_MS } from '../components/ScanSettings'
import { ToastProvider, TreeProvider, useTree } from '../context'
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

const clone = (v) => JSON.parse(JSON.stringify(v))

// A stand-in for the server: the subnet, its scan target and its agents, changed
// only by the write calls, and read back only by the list and get calls. Scope is
// the server's to work out; this one hard-codes what the real one would answer.
let db

const SCOPE_WITH_POOL = [
  { start: '10.20.0.1', end: '10.20.0.254', start_int: 169082881, end_int: 169083134 },
]
const SCOPE_WITHOUT_POOL = [
  { start: '10.20.0.1', end: '10.20.0.99', start_int: 169082881, end_int: 169082979 },
  { start: '10.20.0.201', end: '10.20.0.254', start_int: 169083081, end_int: 169083134 },
]

function seed({ target = {}, subnet = {} } = {}) {
  db = {
    subnet: {
      id: 'sub-1',
      section_id: 'sec-1',
      cidr: '10.20.0.0/24',
      vlan_id: 20,
      name: 'Lab',
      domain: null,
      gateway: null,
      dns_servers: null,
      dhcp_start: '10.20.0.100',
      dhcp_end: '10.20.0.200',
      notes: null,
      used: 0,
      usable: 254,
      free: 150,
      reserved_ranges: [
        {
          id: 'rr-1',
          start_ip: '10.20.0.2',
          end_ip: '10.20.0.9',
          description: 'switches',
          scan_excluded: false,
        },
      ],
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
            scan_dhcp_pool: true,
            interval_minutes: 60,
            scan_requested_at: null,
            current_job_id: null,
            job_issued_at: null,
            job_methods: null,
            created_at: '2026-11-16T06:00:00',
            last_scan_at: null,
            last_scan_duration_ms: null,
            last_scan_complete: null,
            scope: SCOPE_WITH_POOL,
            ...target,
          },
  }
}

// what GET /subnets and GET /subnets/{id} return: scan_dhcp_pool comes from the target
const subnetOut = () => ({ ...clone(db.subnet), scan_dhcp_pool: db.target ? db.target.scan_dhcp_pool : null })

function wireServer() {
  ipam.listSections.mockImplementation(async () => ({ data: [{ id: 'sec-1', name: 'Home' }] }))
  ipam.listDeviceTypes.mockImplementation(async () => ({ data: [] }))
  ipam.listSubnets.mockImplementation(async () => ({ data: [subnetOut()] }))
  ipam.getSubnet.mockImplementation(async () => ({ data: subnetOut() }))
  ipam.listHosts.mockImplementation(async () => ({ data: [], total: 0 }))
  ipam.listFindings.mockImplementation(async () => ({ data: [], total: 0 }))
  ipam.listAgents.mockImplementation(async () => ({
    data: [
      { id: 'agent-1', name: 'lab-vlan20', state: 'active' },
      { id: 'agent-2', name: 'core', state: 'disabled' },
    ],
  }))
  ipam.listScanTargets.mockImplementation(async () => ({
    data: db.target ? [clone(db.target)] : [],
  }))
  ipam.updateScanTarget.mockImplementation(async (id, body) => {
    Object.assign(db.target, body)
    db.target.scope = db.target.scan_dhcp_pool ? SCOPE_WITH_POOL : SCOPE_WITHOUT_POOL
    return { data: clone(db.target) }
  })
  ipam.createScanTarget.mockImplementation(async (body) => {
    seed({ target: { ...body, scope: SCOPE_WITHOUT_POOL }, subnet: db.subnet })
    return { data: clone(db.target) }
  })
  ipam.updateReservedRange.mockImplementation(async (subnetId, rangeId, body) => {
    const block = db.subnet.reserved_ranges.find((r) => r.id === rangeId)
    Object.assign(block, body)
    return { data: clone(block) }
  })
}

// Shows what TreeContext holds for the subnet, so a test can tell what a
// refreshTree() brought back from what the view itself refetched
function TreeProbe() {
  const { subnets } = useTree()
  const s = subnets.find((x) => x.id === 'sub-1')
  if (!s) return null
  return (
    <div data-testid="tree">
      {`pool:${s.scan_dhcp_pool} ` +
        s.reserved_ranges.map((r) => `${r.start_ip}:${r.scan_excluded ? 'excluded' : 'scanned'}`).join(' ')}
    </div>
  )
}

// Moves between subnets the way the sidebar does: SubnetView stays mounted
function GoTo() {
  const navigate = useNavigate()
  return <button onClick={() => navigate('/subnets/sub-2')}>go to sub-2</button>
}

function renderView() {
  return render(
    <MemoryRouter initialEntries={['/subnets/sub-1']}>
      <ToastProvider>
        <TreeProvider>
          <TreeProbe />
          <GoTo />
          <Routes>
            <Route path="/subnets/:subnetId" element={<SubnetView />} />
          </Routes>
        </TreeProvider>
      </ToastProvider>
    </MemoryRouter>
  )
}

const scanning = () => screen.getByRole('region', { name: 'Scanning' })

async function openSettings(user) {
  await user.click(await within(await findScanning()).findByRole('button', { name: 'Settings' }))
}

const findScanning = () => screen.findByRole('region', { name: 'Scanning' })

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

describe('acceptance: scope changes are reflected after refreshTree()', () => {
  it('turning pool scanning off saves it, refreshes the tree, and shows the server state', async () => {
    const user = userEvent.setup()
    renderView()
    await waitFor(() => expect(screen.getByTestId('tree')).toHaveTextContent('pool:true'))
    expect(await within(await findScanning()).findByText(/DHCP pool included/)).toBeInTheDocument()
    expect(within(scanning()).getByText('10.20.0.1–10.20.0.254')).toBeInTheDocument()

    await openSettings(user)
    const pool = within(scanning()).getByRole('checkbox', { name: /Scan DHCP pool/ })
    expect(pool).toBeChecked()
    expect(pool).toBeEnabled()
    expect(pool).toHaveClass('tlg-check')
    await user.click(pool)
    const treeLoadsBefore = ipam.listSubnets.mock.calls.length
    await user.click(within(scanning()).getByRole('button', { name: 'Save scanning settings' }))

    await waitFor(() => expect(ipam.updateScanTarget).toHaveBeenCalledTimes(1))
    expect(ipam.updateScanTarget.mock.calls[0][0]).toBe('tgt-1')
    expect(ipam.updateScanTarget.mock.calls[0][1]).toMatchObject({ scan_dhcp_pool: false })
    // the tree was reloaded from the server, and now carries the new value
    await waitFor(() => expect(screen.getByTestId('tree')).toHaveTextContent('pool:false'))
    expect(ipam.listSubnets.mock.calls.length).toBeGreaterThan(treeLoadsBefore)
    // the view shows what the server now reports: no pool, and the scope it computed
    await waitFor(() =>
      expect(within(scanning()).queryByText(/DHCP pool included/)).not.toBeInTheDocument()
    )
    expect(await within(scanning()).findByText(/10\.20\.0\.1–10\.20\.0\.99/)).toBeInTheDocument()
    await openSettings(user)
    expect(within(scanning()).getByRole('checkbox', { name: /Scan DHCP pool/ })).not.toBeChecked()
  })

  it('excluding a reserved range saves it, refreshes the tree, and shows it as not scanned', async () => {
    const user = userEvent.setup()
    renderView()
    await waitFor(() =>
      expect(screen.getByTestId('tree')).toHaveTextContent('10.20.0.2:scanned')
    )
    const box = await screen.findByRole('checkbox', { name: 'Scan 10.20.0.2–10.20.0.9' })
    expect(box).toBeChecked()
    expect(box).toHaveClass('tlg-check')
    const treeLoadsBefore = ipam.listSubnets.mock.calls.length

    await user.click(box)

    expect(ipam.updateReservedRange).toHaveBeenCalledWith('sub-1', 'rr-1', { scan_excluded: true })
    await waitFor(() =>
      expect(screen.getByTestId('tree')).toHaveTextContent('10.20.0.2:excluded')
    )
    expect(ipam.listSubnets.mock.calls.length).toBeGreaterThan(treeLoadsBefore)
    await waitFor(() =>
      expect(screen.getByRole('checkbox', { name: 'Scan 10.20.0.2–10.20.0.9' })).not.toBeChecked()
    )
    expect(screen.getByText('not scanned')).toBeInTheDocument()

    // and back on
    await user.click(screen.getByRole('checkbox', { name: 'Scan 10.20.0.2–10.20.0.9' }))
    expect(ipam.updateReservedRange).toHaveBeenLastCalledWith('sub-1', 'rr-1', {
      scan_excluded: false,
    })
    await waitFor(() =>
      expect(screen.getByTestId('tree')).toHaveTextContent('10.20.0.2:scanned')
    )
  })

  it('a refused range change leaves the checkbox showing the server state', async () => {
    const user = userEvent.setup()
    ipam.updateReservedRange.mockRejectedValue(new ApiError('VALIDATION_ERROR', 'nope', 422))
    renderView()
    const box = await screen.findByRole('checkbox', { name: 'Scan 10.20.0.2–10.20.0.9' })
    await user.click(box)
    expect(await screen.findByText('nope')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Scan 10.20.0.2–10.20.0.9' })).toBeChecked()
    expect(screen.getByTestId('tree')).toHaveTextContent('10.20.0.2:scanned')
  })
})

describe('scanning settings', () => {
  it('a new reserved range is scanned unless the box is unticked', async () => {
    const user = userEvent.setup()
    ipam.createReservedRange.mockResolvedValue({ data: {} })
    renderView()
    await user.click(await screen.findByRole('button', { name: /Reserve range/ }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByPlaceholderText('10.10.30.2'), '10.20.0.20')
    await user.type(within(dialog).getByPlaceholderText('10.10.30.19'), '10.20.0.29')
    const box = within(dialog).getByRole('checkbox', { name: /Scan this range/ })
    expect(box).toBeChecked()
    await user.click(box)
    await user.click(within(dialog).getByRole('button', { name: 'Reserve' }))
    expect(ipam.createReservedRange).toHaveBeenCalledWith('sub-1', {
      start_ip: '10.20.0.20',
      end_ip: '10.20.0.29',
      description: null,
      scan_excluded: true,
    })
  })

  it('the pool setting is off by default and disabled when the subnet has no pool', async () => {
    const user = userEvent.setup()
    seed({ target: null, subnet: { dhcp_start: null, dhcp_end: null } })
    renderView()
    expect(await within(await findScanning()).findByText('Not scanned.')).toBeInTheDocument()
    await user.click(within(scanning()).getByRole('button', { name: 'Set up scanning' }))
    const pool = within(scanning()).getByRole('checkbox', { name: /Scan DHCP pool/ })
    expect(pool).not.toBeChecked()
    expect(pool).toBeDisabled()
    expect(within(scanning()).getByText('This subnet has no DHCP pool defined.')).toBeInTheDocument()
    // no target, so nothing to scan
    expect(within(scanning()).queryByRole('button', { name: 'Scan now' })).not.toBeInTheDocument()
  })

  it('sets up scanning with the chosen agent, methods and interval', async () => {
    const user = userEvent.setup()
    seed({ target: null })
    renderView()
    await user.click(await within(await findScanning()).findByRole('button', { name: 'Set up scanning' }))
    const region = scanning()
    expect(within(region).getByRole('checkbox', { name: /Scan DHCP pool/ })).not.toBeChecked()
    await user.selectOptions(within(region).getByLabelText('Agent'), 'agent-1')
    await user.click(within(region).getByRole('checkbox', { name: /Reverse DNS/ }))
    await user.type(within(region).getByLabelText('Interval (minutes)'), '30')
    await user.click(within(region).getByRole('button', { name: 'Set up scanning' }))

    await waitFor(() => expect(ipam.createScanTarget).toHaveBeenCalledTimes(1))
    expect(ipam.createScanTarget).toHaveBeenCalledWith({
      subnet_id: 'sub-1',
      agent_id: 'agent-1',
      enabled: true,
      methods: ['icmp', 'arp'],
      scan_dhcp_pool: false,
      interval_minutes: 30,
    })
    expect(await within(scanning()).findByText(/Scanned by lab-vlan20, every 30 min/)).toBeInTheDocument()
  })

  it('shows the server’s refusal of a settings change inline and keeps the form', async () => {
    const user = userEvent.setup()
    ipam.updateScanTarget.mockRejectedValue(
      new ApiError(
        'VALIDATION_ERROR',
        'A scan target needs a liveness method: methods must include icmp or arp',
        422
      )
    )
    renderView()
    await openSettings(user)
    await user.click(within(scanning()).getByRole('checkbox', { name: /ICMP/ }))
    await user.click(within(scanning()).getByRole('checkbox', { name: /ARP/ }))
    await user.click(within(scanning()).getByRole('button', { name: 'Save scanning settings' }))
    expect(await within(scanning()).findByRole('alert')).toHaveTextContent(
      'methods must include icmp or arp'
    )
    expect(within(scanning()).getByRole('checkbox', { name: /ICMP/ })).not.toBeChecked()
  })

  it('a blank interval saves as manual only', async () => {
    const user = userEvent.setup()
    renderView()
    await openSettings(user)
    await user.clear(within(scanning()).getByLabelText('Interval (minutes)'))
    await user.click(within(scanning()).getByRole('button', { name: 'Save scanning settings' }))
    await waitFor(() => expect(ipam.updateScanTarget).toHaveBeenCalledTimes(1))
    expect(ipam.updateScanTarget.mock.calls[0][1]).toMatchObject({ interval_minutes: null })
    expect(await within(scanning()).findByText(/on request only/)).toBeInTheDocument()
  })

  it('Scan now requests a scan; a cooldown shows the server’s message', async () => {
    const user = userEvent.setup()
    ipam.requestScan.mockResolvedValueOnce({ data: {} })
    renderView()
    await user.click(await within(await findScanning()).findByRole('button', { name: 'Scan now' }))
    expect(ipam.requestScan).toHaveBeenCalledWith('sub-1')
    expect(await screen.findByText(/picks it up at its next check-in/)).toBeInTheDocument()

    const msg =
      'A scan of subnet Lab was requested less than 15 minutes ago; request another after 2026-11-16T07:15:00+00:00'
    ipam.requestScan.mockRejectedValueOnce(new ApiError('COOLDOWN', msg, 429))
    await user.click(within(scanning()).getByRole('button', { name: 'Scan now' }))
    expect(await screen.findByText(msg)).toBeInTheDocument()
  })

  it('shows the last scan time and duration, read as UTC', async () => {
    const ago = new Date(Date.now() - 5 * 60 * 1000)
    seed({
      target: {
        last_scan_at: ago.toISOString().replace('Z', ''), // as SQLite returns it
        last_scan_duration_ms: 12_345,
        last_scan_complete: false,
      },
    })
    renderView()
    const region = await findScanning()
    expect(await within(region).findByText('5m ago')).toBeInTheDocument()
    expect(within(region).getByText(/took 12\.3 s/)).toBeInTheDocument()
    expect(within(region).getByText(/incomplete/)).toBeInTheDocument()
  })

  it('says MAC collection is unavailable from what the server reports, with no job out', async () => {
    seed({ target: { runnable_methods: ['icmp', 'rdns'] } })
    renderView()
    const region = await findScanning()
    expect(await within(region).findByText('idle')).toBeInTheDocument()
    expect(
      within(region).getByText('MAC collection unavailable — agent is not on this subnet')
    ).toBeInTheDocument()
    expect(within(region).queryByText(/Cannot be scanned/)).toBeNull()
  })

  it('says an arp-only target cannot be scanned by this agent', async () => {
    seed({ target: { methods: ['arp', 'rdns'], runnable_methods: [] } })
    renderView()
    const region = await findScanning()
    expect(await within(region).findByText(/^Cannot be scanned by this agent/)).toBeInTheDocument()
    expect(within(region).queryByText(/MAC collection unavailable/)).toBeNull()
  })

  it('says neither when the server does not know, and no longer reads it from a job out', async () => {
    // a job issued without arp, as before #68, but runnable_methods null: no agent
    // check-in to judge by, so nothing is claimed
    seed({
      target: {
        current_job_id: 'job-1',
        job_issued_at: new Date().toISOString().replace('Z', ''),
        job_methods: ['icmp', 'rdns'],
        runnable_methods: null,
      },
    })
    renderView()
    const region = await findScanning()
    expect(await within(region).findByText(/job issued/)).toBeInTheDocument()
    expect(within(region).queryByText(/MAC collection unavailable/)).toBeNull()
    expect(within(region).queryByText(/Cannot be scanned/)).toBeNull()
  })

  it('says neither for a target with no agent', async () => {
    seed({ target: { agent_id: null, methods: ['arp'], runnable_methods: null } })
    renderView()
    const region = await findScanning()
    expect(await within(region).findByText(/No agent assigned/)).toBeInTheDocument()
    expect(within(region).queryByRole('status')).toBeNull()
  })

  it('does not claim MAC collection is unavailable when the agent can run arp', async () => {
    seed({
      target: {
        current_job_id: 'job-1',
        job_issued_at: new Date().toISOString().replace('Z', ''),
        job_methods: ['icmp', 'arp', 'rdns'],
        runnable_methods: ['icmp', 'arp', 'rdns'],
      },
    })
    renderView()
    expect(await within(await findScanning()).findByText(/job issued/)).toBeInTheDocument()
    expect(screen.queryByText(/MAC collection unavailable/)).not.toBeInTheDocument()
  })

  it('shows the error the agent reported with its last scan, as text and never as markup', async () => {
    const error = '<img src=x onerror="alert(1)"><b>bold</b> guard: subnet outside ALLOWED_CIDRS'
    seed({
      target: {
        last_scan_at: new Date(Date.now() - 60 * 1000).toISOString().replace('Z', ''),
        last_scan_complete: false,
        last_scan_error: error,
      },
    })
    renderView()
    const region = await findScanning()
    const shown = await within(region).findByTestId('scan-error')
    expect(shown).toHaveTextContent(`Agent reported: ${error}`)
    expect(shown.textContent).toContain('<b>bold</b>')
    expect(region.querySelector('img')).toBeNull()
    expect(region.querySelector('b')).toBeNull()
  })

  it('shows no error line when the last scan carried none', async () => {
    seed({ target: { last_scan_at: '2026-11-16T06:00:00', last_scan_error: null } })
    renderView()
    const region = await findScanning()
    await within(region).findByText(/Last scan/)
    expect(within(region).queryByTestId('scan-error')).toBeNull()
  })
})

describe('review fixes', () => {
  it('an unsaved draft does not follow the view to another subnet with the same settings', async () => {
    const user = userEvent.setup()
    // a second subnet whose target has the same settings as the first
    ipam.getSubnet.mockImplementation(async (id) => ({
      data: id === 'sub-2' ? { ...subnetOut(), id: 'sub-2', name: 'Lab 2' } : subnetOut(),
    }))
    ipam.listScanTargets.mockImplementation(async () => ({
      data: [clone(db.target), { ...clone(db.target), id: 'tgt-2', subnet_id: 'sub-2' }],
    }))
    renderView()
    await openSettings(user)
    await user.clear(within(scanning()).getByLabelText('Interval (minutes)'))
    await user.type(within(scanning()).getByLabelText('Interval (minutes)'), '999')

    await user.click(screen.getByRole('button', { name: 'go to sub-2' }))
    await screen.findByRole('heading', { name: 'Lab 2' })
    // the form closed with the subnet; reopened, it shows the server's values
    expect(within(scanning()).queryByRole('button', { name: 'Save scanning settings' })).toBeNull()
    await openSettings(user)
    expect(within(scanning()).getByLabelText('Interval (minutes)')).toHaveValue(60)
    expect(ipam.updateScanTarget).not.toHaveBeenCalled()
  })

  it('a failed tree reload after a range change still reloads the view, and says so', async () => {
    const user = userEvent.setup()
    renderView()
    const box = await screen.findByRole('checkbox', { name: 'Scan 10.20.0.2–10.20.0.9' })
    await waitFor(() => expect(screen.getByTestId('tree')).toBeInTheDocument())
    ipam.listSubnets.mockRejectedValue(new ApiError('HTTP_502', 'Bad Gateway', 502))

    await user.click(box)

    expect(ipam.updateReservedRange).toHaveBeenCalledWith('sub-1', 'rr-1', { scan_excluded: true })
    expect(
      await screen.findByText('Saved, but the subnet list could not be reloaded: Bad Gateway')
    ).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByRole('checkbox', { name: 'Scan 10.20.0.2–10.20.0.9' })).not.toBeChecked()
    )
    expect(screen.queryByText('Bad Gateway')).toBeNull() // not reported as the write failing
  })

  it('a failed tree reload after saving settings closes the form and reloads the view', async () => {
    const user = userEvent.setup()
    renderView()
    await openSettings(user)
    await waitFor(() => expect(screen.getByTestId('tree')).toBeInTheDocument())
    ipam.listSubnets.mockRejectedValue(new ApiError('HTTP_502', 'Bad Gateway', 502))
    await user.click(within(scanning()).getByRole('checkbox', { name: /Scan DHCP pool/ }))
    await user.click(within(scanning()).getByRole('button', { name: 'Save scanning settings' }))

    expect(
      await screen.findByText('Saved, but the subnet list could not be reloaded: Bad Gateway')
    ).toBeInTheDocument()
    expect(within(scanning()).queryByRole('alert')).toBeNull()
    await waitFor(() =>
      expect(within(scanning()).queryByText(/DHCP pool included/)).not.toBeInTheDocument()
    )
  })

  it('reloads the target while open, so a job and what the agent can run show', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderView()
    expect(await within(await findScanning()).findByText('idle')).toBeInTheDocument()

    // the agent checks in from off the subnet's link and is issued a job without arp
    Object.assign(db.target, {
      current_job_id: 'job-1',
      job_issued_at: new Date().toISOString().replace('Z', ''),
      job_methods: ['icmp', 'rdns'],
      runnable_methods: ['icmp', 'rdns'],
    })
    await vi.advanceTimersByTimeAsync(REFRESH_MS)

    expect(await within(scanning()).findByText(/job issued/)).toBeInTheDocument()
    expect(
      within(scanning()).getByText('MAC collection unavailable — agent is not on this subnet')
    ).toBeInTheDocument()

    // the job finishes, and the agent's next check-in reports an interface on the link
    Object.assign(db.target, {
      current_job_id: null,
      job_issued_at: null,
      job_methods: null,
      runnable_methods: ['icmp', 'arp', 'rdns'],
    })
    await vi.advanceTimersByTimeAsync(REFRESH_MS)
    expect(await within(scanning()).findByText('idle')).toBeInTheDocument()
    expect(within(scanning()).queryByText(/MAC collection unavailable/)).toBeNull()
  })
})
