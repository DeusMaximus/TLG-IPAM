import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import * as ipam from '../api/ipam'
import { ToastProvider } from '../context'
import AddAgentModal from '../components/AddAgentModal'
import Agents from './Agents'

vi.mock('../api/ipam', () => ({
  listAgents: vi.fn(),
  createAgent: vi.fn(),
  updateAgent: vi.fn(),
  revokeAgentTokens: vi.fn(),
  deleteAgent: vi.fn(),
  listScanTargets: vi.fn(),
  listScanSuggestions: vi.fn(),
  createScanTarget: vi.fn(),
  requestScan: vi.fn(),
  reEnrolAgent: vi.fn(),
  getHealth: vi.fn(),
}))

vi.mock('../context', async (importOriginal) => ({
  ...(await importOriginal()),
  useTree: () => ({
    subnets: [{ id: 'sub-1', name: 'Lab', cidr: '10.20.0.0/24' }],
    refreshTree: vi.fn(),
  }),
}))

const TOKEN = 'tlga_SECRETsecretSECRETsecretSECRETsecret0123'
const PREFIX = TOKEN.slice(0, 13)
const ENROL_TOKEN = 'tlge_ENROLenrolENROLenrolENROLenrolENROL0123'
const EXPIRES = '2026-11-16T07:00:00'

// what the owner routes return: tokens by display prefix only, never the token
const agentRow = (over = {}) => ({
  id: 'agent-1',
  name: 'lab-vlan20',
  state: 'active',
  version: '0.2.0',
  platform: 'Linux-6.8-x86_64',
  hostname: 'scanner.home.arpa',
  interfaces: [{ name: 'eth0', cidr: '10.20.0.5/24', mac: '02:00:00:00:00:01' }],
  last_checkin_at: new Date(Date.now() - 20 * 1000).toISOString().replace('Z', ''),
  last_error: null,
  current_job_id: null,
  job_issued_at: null,
  created_at: '2026-11-16T06:00:00',
  update_available: false,
  protocol_mismatch: false,
  tokens: [
    {
      id: 'tok-1',
      kind: 'agent',
      token_prefix: PREFIX,
      created_at: '2026-11-16T06:00:00',
      last_used_at: null,
      expires_at: null,
      used_at: null,
      revoked_at: null,
    },
  ],
  ...over,
})

const target = { id: 'tgt-1', subnet_id: 'sub-1', agent_id: 'agent-1', enabled: true }

// what `POST /agents` returns: a pending agent, its one token the enrolment token
const enrolToken = (over = {}) => ({
  id: 'tok-e',
  kind: 'enrol',
  token_prefix: ENROL_TOKEN.slice(0, 13),
  created_at: '2026-11-16T06:00:00',
  last_used_at: null,
  expires_at: EXPIRES,
  // the server's count of what it has left (`agents.expires_in_s`), null once not live
  expires_in_s: 3600,
  used_at: null,
  revoked_at: null,
  ...over,
})
const created = () => ({
  data: {
    ...agentRow({ state: 'pending', last_checkin_at: null, tokens: [enrolToken()] }),
    enrol_token: ENROL_TOKEN,
    enrol_token_expires_at: EXPIRES,
  },
})

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <Agents />
      </ToastProvider>
    </MemoryRouter>
  )
}

beforeEach(() => {
  ipam.getHealth.mockResolvedValue({ status: 'ok', version: '0.2.0' })
  ipam.listScanTargets.mockResolvedValue({ data: [target], total: 1 })
  ipam.listAgents.mockResolvedValue({ data: [agentRow()], total: 1 })
  ipam.listScanSuggestions.mockResolvedValue({ data: [], total: 0 })
})

afterEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
})

describe('Agents page', () => {
  it('lists each agent with its status, version, interfaces and subnets covered', async () => {
    renderPage()
    const row = await screen.findByTestId('agent-agent-1')

    expect(within(row).getByText('Online')).toBeInTheDocument()
    expect(within(row).getByText('0.2.0')).toBeInTheDocument()
    expect(within(row).getByText('10.20.0.5/24')).toBeInTheDocument()
    expect(within(row).getByText('02:00:00:00:00:01')).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'Lab' })).toHaveAttribute('href', '/subnets/sub-1')
    expect(within(row).getByText(`${PREFIX}…`)).toBeInTheDocument()
  })

  it('says the clocks disagree for an agent whose last check-in is dated ahead, not online', async () => {
    // the same time the Scanning section is given for this agent's target
    const at = new Date(Date.now() + 24 * 3600 * 1000).toISOString().replace('Z', '+00:00')
    ipam.listAgents.mockResolvedValue({ data: [agentRow({ last_checkin_at: at })], total: 1 })
    renderPage()
    const row = await screen.findByTestId('agent-agent-1')
    expect(within(row).getByText('Clocks disagree')).toBeInTheDocument()
    expect(within(row).queryByText('Online')).toBeNull()
    const last = within(row).getByTestId('last-checkin')
    expect(last).toHaveTextContent(
      `at ${new Date(Date.parse(at)).toLocaleString()}, ahead of this browser’s clock`
    )
    expect(last).not.toHaveTextContent('0s ago')
  })

  // the exact three-minute boundary is held in utils/agents.test.js, on a fixed clock
  it('keeps the ordinary ages: seconds ago, minutes inside the online window, a day', async () => {
    const ago = (ms) => new Date(Date.now() - ms).toISOString().replace('Z', '+00:00')
    ipam.listAgents.mockResolvedValue({
      data: [
        agentRow({ id: 'a-20s', last_checkin_at: ago(20 * 1000) }),
        agentRow({ id: 'a-2m', last_checkin_at: ago(2 * 60 * 1000) }),
        agentRow({ id: 'a-1d', last_checkin_at: ago(24 * 3600 * 1000) }),
      ],
      total: 3,
    })
    renderPage()
    const seconds = await screen.findByTestId('agent-a-20s')
    expect(within(seconds).getByText('Online')).toBeInTheDocument()
    expect(within(seconds).getByTestId('last-checkin')).toHaveTextContent(/^\d+s ago$/)
    const minutes = screen.getByTestId('agent-a-2m')
    expect(within(minutes).getByText('Online')).toBeInTheDocument()
    expect(within(minutes).getByTestId('last-checkin')).toHaveTextContent('2m ago')
    const day = screen.getByTestId('agent-a-1d')
    expect(within(day).getByText('Offline')).toBeInTheDocument()
    expect(within(day).getByTestId('last-checkin')).toHaveTextContent('24h ago')
  })

  it('renders agent-reported text as text, never as markup', async () => {
    ipam.listAgents.mockResolvedValue({
      data: [agentRow({ hostname: '<img src=x onerror=alert(1)>' })],
      total: 1,
    })
    renderPage()
    const row = await screen.findByTestId('agent-agent-1')
    expect(within(row).getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(row.querySelector('img')).toBeNull()
  })

  it('shows disabled, never-checked-in, revoked and refused agents for what they are', async () => {
    ipam.listAgents.mockResolvedValue({
      data: [
        agentRow({ id: 'a-dis', name: 'off', state: 'disabled' }),
        agentRow({
          id: 'a-new',
          name: 'new',
          last_checkin_at: null,
          version: null,
          interfaces: null,
          tokens: [{ ...agentRow().tokens[0], revoked_at: '2026-11-16T07:00:00' }],
          last_error: 'protocol mismatch',
        }),
      ],
      total: 2,
    })
    renderPage()
    const off = await screen.findByTestId('agent-a-dis')
    expect(within(off).getByText('Disabled')).toBeInTheDocument()
    expect(within(off).getByRole('button', { name: /Enable/ })).toBeInTheDocument()

    const fresh = screen.getByTestId('agent-a-new')
    expect(within(fresh).getByText('Never checked in')).toBeInTheDocument()
    expect(within(fresh).getByText('revoked')).toBeInTheDocument()
    expect(within(fresh).getByText(/protocol mismatch/)).toBeInTheDocument()
    expect(within(fresh).getByRole('button', { name: /Revoke/ })).toBeDisabled()
  })

  it('shows an agent that has not enrolled as such, and lets its enrolment token be revoked', async () => {
    const pending = (id, token) =>
      agentRow({ id, name: id, state: 'pending', last_checkin_at: null, tokens: [token] })
    ipam.listAgents.mockResolvedValue({
      data: [
        pending('open', enrolToken({ expires_at: '2999-01-01T00:00:00' })),
        pending('expired', enrolToken({ expires_at: '2000-01-01T00:00:00', expires_in_s: null })),
      ],
      total: 2,
    })
    renderPage()
    const open = await screen.findByTestId('agent-open')
    expect(within(open).getByText('not enrolled')).toBeInTheDocument()
    expect(within(open).queryByText('revoked')).toBeNull()
    expect(within(open).getByRole('button', { name: /Revoke/ })).toBeEnabled()
    const expired = screen.getByTestId('agent-expired')
    expect(within(expired).getByText('not enrolled')).toBeInTheDocument()
    expect(within(expired).getByRole('button', { name: /Revoke/ })).toBeDisabled()
  })

  it('shows the enrolment token once, and nothing can show it again after the dialog closes', async () => {
    const user = userEvent.setup()
    ipam.listAgents.mockResolvedValueOnce({ data: [], total: 0 })
    ipam.listScanTargets.mockResolvedValue({ data: [], total: 0 })
    ipam.createAgent.mockResolvedValue(created())
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Add agent/ }))
    await user.type(screen.getByLabelText('Name'), '  lab-vlan20  ')
    await user.click(screen.getByRole('button', { name: 'Create agent' }))

    expect(ipam.createAgent).toHaveBeenCalledWith({ name: 'lab-vlan20' })
    expect(await screen.findByTestId('agent-token')).toHaveTextContent(ENROL_TOKEN)
    // served from loopback here (jsdom), so the page's own origin is used
    expect(screen.queryByTestId('agent-needs-https')).toBeNull()
    expect(screen.queryByTestId('agent-version-unknown')).toBeNull()

    // the Container tab first: the enrolment token as IPAM_ENROL_TOKEN, a volume for the
    // agent's own token, pinned to the server's version
    expect(screen.getByRole('tab', { name: 'Container' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('prerequisites')).toHaveTextContent('Docker or Podman running as root')
    const command = screen.getByTestId('agent-command').textContent
    expect(command).toContain(`-e IPAM_ENROL_TOKEN=${ENROL_TOKEN}`)
    expect(command).toContain('-v tlg-ipam-agent:/var/lib/tlg-ipam-agent')
    expect(command).toContain(`-e IPAM_URL=${window.location.origin}`)
    expect(command).toContain('--network host')
    expect(command).toContain('--cap-add NET_RAW')
    expect(command).toMatch(/ghcr\.io\/deusmaximus\/tlg-ipam-agent:0\.2\.0$/)
    expect(command).not.toContain('tlga_')
    expect(screen.queryByTestId('agent-forget-command')).toBeNull()

    // the systemd tab: three commands, the token read from standard input, never an argument
    await user.click(screen.getByRole('tab', { name: 'systemd' }))
    expect(screen.getByTestId('prerequisites')).toHaveTextContent('Python 3.11 or later')
    const systemd = screen.getByTestId('agent-systemd-commands').textContent
    expect(systemd.split('\n')).toEqual([
      'sudo python3 -m venv /opt/tlg-ipam-agent',
      'sudo /opt/tlg-ipam-agent/bin/pip install https://github.com/DeusMaximus/TLG-IPAM/releases/download/v0.2.0/tlg_ipam_agent-0.2.0-py3-none-any.whl',
      `sudo /opt/tlg-ipam-agent/bin/tlg-ipam-agent install --url ${window.location.origin} --enrol-token -`,
    ])
    expect(systemd).not.toContain('tlg' + 'e_')
    expect(systemd).not.toContain('tlga_')
    expect(screen.queryByTestId('agent-command')).toBeNull()
    // the token itself stays on show, to paste when install asks
    expect(screen.getByTestId('agent-token')).toHaveTextContent(ENROL_TOKEN)

    // a stray click on the backdrop does not throw the token away
    await user.click(screen.getByRole('dialog').parentElement)
    expect(screen.getByTestId('agent-token')).toBeInTheDocument()

    // the list is reloaded from the owner route, which carries no token
    await waitFor(() => expect(ipam.listAgents).toHaveBeenCalledTimes(2))

    await user.click(screen.getByRole('button', { name: /Done, I have saved the token/ }))

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(await screen.findByText(`${PREFIX}…`)).toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain(ENROL_TOKEN)
    expect(JSON.stringify({ ...localStorage })).not.toContain(ENROL_TOKEN)
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(ENROL_TOKEN)

    // opening the dialog again starts a new agent; it does not bring the token back
    await user.click(screen.getByRole('button', { name: /Add agent/ }))
    expect(screen.getByLabelText('Name')).toHaveValue('')
    expect(document.body.innerHTML).not.toContain(ENROL_TOKEN)
    expect(ipam.createAgent).toHaveBeenCalledTimes(1)
  })

  it('never builds a command that sends a token over plain http across a network', async () => {
    const user = userEvent.setup()
    ipam.createAgent.mockResolvedValue(created())
    const location = { protocol: 'http:', hostname: '10.0.0.2', origin: 'http://10.0.0.2:8080' }
    render(
      <AddAgentModal serverVersion="0.2.0" onClose={vi.fn()} onCreated={vi.fn()} location={location} />
    )
    await user.type(screen.getByLabelText('Name'), 'lab-vlan20')
    await user.click(screen.getByRole('button', { name: 'Create agent' }))
    expect(await screen.findByTestId('agent-needs-https')).toBeInTheDocument()
    const command = screen.getByTestId('agent-command').textContent
    expect(command).toContain("-e IPAM_URL='https://<server>'")
    await user.click(screen.getByRole('tab', { name: 'systemd' }))
    const systemd = screen.getByTestId('agent-systemd-commands').textContent
    expect(systemd).toContain("--url 'https://<server>' --enrol-token -")
    for (const text of [command, systemd]) expect(text).not.toContain('http://10.0.0.2')
  })

  it('pins nothing to a guess when the server version is unknown', async () => {
    const user = userEvent.setup()
    ipam.createAgent.mockResolvedValue(created())
    render(<AddAgentModal serverVersion={undefined} onClose={vi.fn()} onCreated={vi.fn()} />)
    await user.type(screen.getByLabelText('Name'), 'lab-vlan20')
    await user.click(screen.getByRole('button', { name: 'Create agent' }))
    expect(await screen.findByTestId('agent-version-unknown')).toBeInTheDocument()
    expect(screen.getByTestId('agent-command').textContent).toMatch(/tlg-ipam-agent:<version>$/)
  })

  it('drops the token when the dialog is closed with its close button too', async () => {
    const user = userEvent.setup()
    ipam.createAgent.mockResolvedValue(created())
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Add agent/ }))
    await user.type(screen.getByLabelText('Name'), 'lab-vlan20')
    await user.click(screen.getByRole('button', { name: 'Create agent' }))
    await screen.findByTestId('agent-token')

    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.body.innerHTML).not.toContain(ENROL_TOKEN)
  })

  it('cannot be closed while the create request is in flight, so its token is not lost', async () => {
    const user = userEvent.setup()
    let respond
    ipam.createAgent.mockReturnValue(new Promise((resolve) => (respond = resolve)))
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Add agent/ }))
    await user.type(screen.getByLabelText('Name'), 'lab-vlan20')
    await user.click(screen.getByRole('button', { name: 'Create agent' }))
    expect(screen.getByRole('button', { name: 'Creating…' })).toBeDisabled()

    // every close path does nothing while the request is pending
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await user.click(screen.getByRole('dialog').parentElement)
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    respond(created())
    expect(await screen.findByTestId('agent-token')).toHaveTextContent(ENROL_TOKEN)
  })

  it('keeps the form open with the server error when the name is taken', async () => {
    const user = userEvent.setup()
    ipam.createAgent.mockRejectedValue(
      new ApiError('CONFLICT', "An agent named 'lab-vlan20' already exists", 409)
    )
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Add agent/ }))
    await user.type(screen.getByLabelText('Name'), 'LAB-vlan20')
    await user.click(screen.getByRole('button', { name: 'Create agent' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('already exists')
    expect(screen.getByLabelText('Name')).toHaveValue('LAB-vlan20')
  })

  it('refuses an empty or over-long name before sending it', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Add agent/ }))
    await user.type(screen.getByLabelText('Name'), '   ')
    await user.click(screen.getByRole('button', { name: 'Create agent' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Name the agent')

    await user.clear(screen.getByLabelText('Name'))
    await user.type(screen.getByLabelText('Name'), 'x'.repeat(75))
    await user.click(screen.getByRole('button', { name: 'Create agent' }))
    expect(screen.getByRole('alert')).toHaveTextContent('At most 74')
    expect(ipam.createAgent).not.toHaveBeenCalled()
  })

  it('disables and enables an agent', async () => {
    const user = userEvent.setup()
    ipam.updateAgent.mockResolvedValue({ data: agentRow({ state: 'disabled' }) })
    renderPage()

    const row = await screen.findByTestId('agent-agent-1')
    await user.click(within(row).getByRole('button', { name: /Disable/ }))
    expect(ipam.updateAgent).toHaveBeenCalledWith('agent-1', { enabled: false })
    await waitFor(() => expect(ipam.listAgents).toHaveBeenCalledTimes(2))
  })

  it('revokes the token after a confirmation', async () => {
    const user = userEvent.setup()
    ipam.revokeAgentTokens.mockResolvedValue({ data: agentRow() })
    renderPage()

    const row = await screen.findByTestId('agent-agent-1')
    await user.click(within(row).getByRole('button', { name: /Revoke/ }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('cannot be reissued')
    expect(ipam.revokeAgentTokens).not.toHaveBeenCalled()

    await user.click(within(dialog).getByRole('button', { name: 'Revoke token' }))
    expect(ipam.revokeAgentTokens).toHaveBeenCalledWith('agent-1')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('deletes an agent after a confirmation that names what happens to its subnets', async () => {
    const user = userEvent.setup()
    ipam.deleteAgent.mockResolvedValue(null)
    renderPage()

    const row = await screen.findByTestId('agent-agent-1')
    await waitFor(() => expect(within(row).getByRole('link', { name: 'Lab' })).toBeInTheDocument())
    await user.click(within(row).getByRole('button', { name: /Delete/ }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('1 subnet(s) it covers')
    expect(dialog).toHaveTextContent('left unassigned')

    await user.click(within(dialog).getByRole('button', { name: 'Delete agent' }))
    expect(ipam.deleteAgent).toHaveBeenCalledWith('agent-1')
  })

  it('says coverage is unknown, never "none", when the scan targets fail to load', async () => {
    const user = userEvent.setup()
    ipam.listScanTargets.mockRejectedValue(new ApiError('HTTP_500', 'Internal Server Error', 500))
    ipam.deleteAgent.mockResolvedValue(null)
    renderPage()

    const row = await screen.findByTestId('agent-agent-1')
    expect(await screen.findByText(/Could not load scan targets/)).toBeInTheDocument()
    expect(within(row).getByText('unknown')).toBeInTheDocument()
    expect(within(row).queryByText('none')).toBeNull()

    await user.click(within(row).getByRole('button', { name: /Delete/ }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('could not be loaded')
    expect(dialog).not.toHaveTextContent('It covers no subnets')
  })

  it('says coverage is unknown while the scan targets are still loading', async () => {
    ipam.listScanTargets.mockReturnValue(new Promise(() => {}))
    renderPage()

    const row = await screen.findByTestId('agent-agent-1')
    expect(within(row).getByText('unknown')).toBeInTheDocument()
  })

  // --- Re-enrol ---

  it('re-enrols an agent after a confirmation, and shows its new token once', async () => {
    const user = userEvent.setup()
    ipam.reEnrolAgent.mockResolvedValue({
      data: {
        ...agentRow({ tokens: [...agentRow().tokens, enrolToken()] }),
        enrol_token: ENROL_TOKEN,
        enrol_token_expires_at: EXPIRES,
      },
    })
    renderPage()

    const row = await screen.findByTestId('agent-agent-1')
    await user.click(within(row).getByRole('button', { name: /Re-enrol/ }))
    expect(screen.getByRole('dialog')).toHaveTextContent('keeps its name and the subnets it covers')
    expect(ipam.reEnrolAgent).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Issue a new enrolment token' }))
    expect(ipam.reEnrolAgent).toHaveBeenCalledWith('agent-1')
    expect(await screen.findByTestId('agent-token')).toHaveTextContent(ENROL_TOKEN)
    // the host may still hold the old token, which would win: how to remove it first
    expect(screen.getByTestId('agent-forget-command').textContent).toBe(
      'docker rm -f tlg-ipam-agent\ndocker volume rm tlg-ipam-agent'
    )
    expect(screen.getByTestId('agent-command')).toHaveTextContent(`IPAM_ENROL_TOKEN=${ENROL_TOKEN}`)
    await user.click(screen.getByRole('tab', { name: 'systemd' }))
    expect(screen.getByTestId('agent-forget-command').textContent).toBe(
      'sudo /opt/tlg-ipam-agent/bin/tlg-ipam-agent uninstall'
    )
    await waitFor(() => expect(ipam.listAgents).toHaveBeenCalledTimes(2))

    // the dialog is taller than a short window: its overlay scrolls, down to Done
    expect(screen.getByRole('dialog').parentElement).toHaveClass('overflow-y-auto')
    // a stray backdrop click keeps it; Done drops it for good
    await user.click(screen.getByRole('dialog').parentElement)
    expect(screen.getByTestId('agent-token')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Done, I have saved the token/ }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.body.innerHTML).not.toContain(ENROL_TOKEN)
  })

  it('cannot close the re-enrol dialog while its request is in flight', async () => {
    const user = userEvent.setup()
    let respond
    ipam.reEnrolAgent.mockReturnValue(new Promise((resolve) => (respond = resolve)))
    renderPage()

    const row = await screen.findByTestId('agent-agent-1')
    await user.click(within(row).getByRole('button', { name: /Re-enrol/ }))
    await user.click(screen.getByRole('button', { name: 'Issue a new enrolment token' }))
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await user.click(screen.getByRole('dialog').parentElement)
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    respond({ data: { ...agentRow(), enrol_token: ENROL_TOKEN, enrol_token_expires_at: EXPIRES } })
    expect(await screen.findByTestId('agent-token')).toHaveTextContent(ENROL_TOKEN)
  })

  it('keeps the re-enrol dialog open with the server error, and offers no re-enrol to a disabled agent', async () => {
    const user = userEvent.setup()
    ipam.listAgents.mockResolvedValue({
      data: [agentRow(), agentRow({ id: 'a-off', name: 'off', state: 'disabled' })],
      total: 2,
    })
    ipam.reEnrolAgent.mockRejectedValue(new ApiError('CONFLICT', 'Agent lab-vlan20 is disabled', 409))
    renderPage()

    const off = await screen.findByTestId('agent-a-off')
    expect(within(off).getByRole('button', { name: /Re-enrol/ })).toBeDisabled()
    const row = screen.getByTestId('agent-agent-1')
    await user.click(within(row).getByRole('button', { name: /Re-enrol/ }))
    await user.click(screen.getByRole('button', { name: 'Issue a new enrolment token' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('is disabled')
    expect(screen.queryByTestId('agent-token')).toBeNull()
  })

  // --- Version flags ---

  it('flags an agent older than the server, with the update command', async () => {
    const user = userEvent.setup()
    ipam.listAgents.mockResolvedValue({
      data: [agentRow({ version: '0.1.0', update_available: true })],
      total: 1,
    })
    renderPage()

    const row = await screen.findByTestId('agent-agent-1')
    expect(within(row).getByTestId('update-available')).toHaveTextContent('update available')
    expect(within(row).queryByTestId('protocol-mismatch')).toBeNull()
    await user.click(within(row).getByRole('button', { name: /Update command/ }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('runs 0.1.0, older than this server (0.2.0)')
    const container = screen.getByTestId('agent-update-command').textContent.split('\n')
    expect(container.slice(0, 2)).toEqual([
      'docker pull ghcr.io/deusmaximus/tlg-ipam-agent:0.2.0',
      'docker rm -f tlg-ipam-agent',
    ])
    expect(container.join('\n')).toContain('-v tlg-ipam-agent:/var/lib/tlg-ipam-agent')
    expect(container.join('\n')).not.toMatch(/tlg[ae]_|IPAM_ENROL_TOKEN|IPAM_AGENT_TOKEN/)
    // a container of the first release's install has no stored token to keep: re-enrol it
    expect(screen.getByTestId('agent-update-legacy')).toHaveTextContent('Re-enrol that agent instead')

    await user.click(screen.getByRole('tab', { name: 'systemd' }))
    expect(screen.getByTestId('agent-update-command').textContent.split('\n')).toEqual([
      'sudo /opt/tlg-ipam-agent/bin/pip install https://github.com/DeusMaximus/TLG-IPAM/releases/download/v0.2.0/tlg_ipam_agent-0.2.0-py3-none-any.whl',
      `sudo /opt/tlg-ipam-agent/bin/tlg-ipam-agent install --url ${window.location.origin}`,
    ])
  })

  it('does not flag an agent the server does not flag', async () => {
    ipam.listAgents.mockResolvedValue({
      data: [agentRow({ version: '0.9.0', update_available: null })],
      total: 1,
    })
    renderPage()
    const row = await screen.findByTestId('agent-agent-1')
    expect(within(row).queryByTestId('update-available')).toBeNull()
    expect(within(row).queryByRole('button', { name: /Update command/ })).toBeNull()
  })

  it('says clearly that an agent on another protocol gets no scans, and how to update it', async () => {
    const user = userEvent.setup()
    const error =
      'Protocol mismatch: the agent speaks protocol 2, the server speaks protocol 1. Install the agent release that matches the server.'
    ipam.listAgents.mockResolvedValue({
      data: [agentRow({ protocol_mismatch: true, last_error: error })],
      total: 1,
    })
    renderPage()

    const row = await screen.findByTestId('agent-agent-1')
    expect(within(row).getByTestId('protocol-mismatch')).toHaveTextContent('given no scans')
    expect(within(row).getByText(`Last check-in refused: ${error}`)).toBeInTheDocument()
    await user.click(within(row).getByRole('button', { name: /Update command/ }))
    expect(screen.getByRole('dialog')).toHaveTextContent('speaks another protocol than this server')
  })

  // --- Pending enrolments ---

  it('lists pending enrolments until they are used or expire', async () => {
    const soon = new Date(Date.now() + 42.5 * 60 * 1000).toISOString().replace('Z', '+00:00')
    const pending = (id, token) =>
      agentRow({ id, name: id, state: 'pending', last_checkin_at: null, tokens: [token] })
    ipam.listAgents.mockResolvedValue({
      data: [
        pending('open', enrolToken({ id: 'e-open', expires_at: soon, expires_in_s: 42.5 * 60 })),
        pending('expired', enrolToken({ id: 'e-old', expires_at: '2000-01-01T00:00:00', expires_in_s: null })),
        pending('used', enrolToken({ id: 'e-used', expires_at: soon, expires_in_s: null, used_at: '2026-11-16T06:10:00' })),
        // an active agent being re-enrolled
        agentRow({ id: 'again', name: 'again', tokens: [...agentRow().tokens, enrolToken({ id: 'e-again', expires_at: soon, expires_in_s: 42.5 * 60 })] }),
      ],
      total: 4,
    })
    renderPage()

    const list = await screen.findByRole('region', { name: 'Pending enrolments' })
    const open = within(list).getByTestId('pending-open')
    expect(open).toHaveTextContent('waiting for its first enrolment')
    expect(open).toHaveTextContent('expires in 42m')
    expect(open).toHaveTextContent(`${ENROL_TOKEN.slice(0, 13)}…`)
    expect(within(list).getByTestId('pending-again')).toHaveTextContent('being re-enrolled')
    expect(within(list).queryByTestId('pending-expired')).toBeNull()
    expect(within(list).queryByTestId('pending-used')).toBeNull()
    // the row says so as well
    expect(within(screen.getByTestId('agent-open')).getByText(/enrolment open, expires in 42m/)).toBeInTheDocument()
  })

  it('does not list a disabled agent’s enrolment as open, and calls it blocked (#143)', async () => {
    // created, then disabled before it enrolled: the exchange refuses its token (403)
    ipam.listAgents.mockResolvedValue({
      data: [agentRow({ id: 'off', name: 'off', state: 'disabled', last_checkin_at: null, tokens: [enrolToken({ id: 'e-off' })] })],
      total: 1,
    })
    renderPage()
    const row = await screen.findByTestId('agent-off')
    expect(screen.queryByRole('region', { name: 'Pending enrolments' })).toBeNull()
    expect(screen.queryByText(/being re-enrolled/)).toBeNull()
    expect(screen.queryByText(/enrolment open/)).toBeNull()
    expect(within(row).getByText('not enrolled')).toBeInTheDocument()
    expect(within(row).getByText(/enrolment blocked until it is enabled, expires in 60m/)).toBeInTheDocument()
    // the token is still live, so it can still be revoked
    expect(within(row).getByRole('button', { name: /Revoke/ })).toBeEnabled()
    expect(within(row).getByRole('button', { name: /Re-enrol/ })).toBeDisabled()
  })

  describe('with the browser’s clock off the server’s (#143)', () => {
    afterEach(() => {
      vi.useRealTimers()
    })

    // The server answered at 12:00 by its own clock; the browser's says otherwise
    it('keeps a live token listed when the browser’s clock is ahead', async () => {
      vi.useFakeTimers({ toFake: ['Date'] })
      vi.setSystemTime(new Date('2026-11-16T12:31:00Z'))
      ipam.listAgents.mockResolvedValue({
        data: [
          agentRow({
            id: 'fresh',
            name: 'fresh',
            state: 'pending',
            last_checkin_at: null,
            tokens: [enrolToken({ expires_at: '2026-11-16T12:30:00+00:00', expires_in_s: 1800 })],
          }),
        ],
        total: 1,
      })
      renderPage()
      const list = await screen.findByRole('region', { name: 'Pending enrolments' })
      expect(within(list).getByTestId('pending-fresh')).toHaveTextContent('expires in 30m')
      expect(within(screen.getByTestId('agent-fresh')).getByText(/enrolment open, expires in 30m/)).toBeInTheDocument()
    })

    it('does not show an expired token as open when the browser’s clock is behind', async () => {
      vi.useFakeTimers({ toFake: ['Date'] })
      vi.setSystemTime(new Date('2026-11-16T11:58:00Z'))
      ipam.listAgents.mockResolvedValue({
        data: [
          agentRow({
            id: 'stale',
            name: 'stale',
            state: 'pending',
            last_checkin_at: null,
            tokens: [enrolToken({ expires_at: '2026-11-16T11:59:00+00:00', expires_in_s: null })],
          }),
        ],
        total: 1,
      })
      renderPage()
      const row = await screen.findByTestId('agent-stale')
      expect(screen.queryByRole('region', { name: 'Pending enrolments' })).toBeNull()
      expect(within(row).queryByText(/enrolment open/)).toBeNull()
      expect(within(row).getByRole('button', { name: /Revoke/ })).toBeDisabled()
    })
  })

  it('lists no pending enrolments when there are none', async () => {
    renderPage()
    await screen.findByTestId('agent-agent-1')
    expect(screen.queryByRole('region', { name: 'Pending enrolments' })).toBeNull()
  })

  // --- Suggested scans ---

  const suggestion = (over = {}) => ({
    agent_id: 'agent-1',
    agent_name: 'lab-vlan20',
    subnet_id: 'sub-2',
    subnet_name: 'IoT',
    subnet_cidr: '10.30.0.0/24',
    ...over,
  })

  it('offers to scan a subnet on the agent’s link, and one click sets it up and asks for a scan', async () => {
    const user = userEvent.setup()
    ipam.listScanSuggestions.mockResolvedValueOnce({ data: [suggestion()], total: 1 })
    ipam.createScanTarget.mockResolvedValue({ data: { id: 'tgt-2' } })
    ipam.requestScan.mockResolvedValue({ data: { id: 'tgt-2' } })
    renderPage()

    const offer = await screen.findByTestId('suggestion-agent-1:sub-2')
    expect(offer).toHaveTextContent('Scan IoT 10.30.0.0/24 with lab-vlan20?')
    expect(screen.getByRole('region', { name: 'Suggested scans' })).toHaveTextContent(
      'ICMP, ARP, Reverse DNS'
    )
    // nothing is set up or scanned until the owner accepts
    expect(ipam.createScanTarget).not.toHaveBeenCalled()
    expect(ipam.requestScan).not.toHaveBeenCalled()

    await user.click(within(offer).getByRole('button', { name: /Scan/ }))
    // the server's defaults: methods, scope and interval are left to it
    expect(ipam.createScanTarget).toHaveBeenCalledWith({ subnet_id: 'sub-2', agent_id: 'agent-1' })
    await waitFor(() => expect(ipam.requestScan).toHaveBeenCalledWith('sub-2'))
    expect(await screen.findByText('lab-vlan20 will scan IoT at its next check-in')).toBeInTheDocument()
    // reloaded: the server no longer offers it
    await waitFor(() => expect(ipam.listScanSuggestions).toHaveBeenCalledTimes(2))
    expect(screen.queryByTestId('suggestion-agent-1:sub-2')).toBeNull()
  })

  it('asks for no scan when the target could not be set up', async () => {
    const user = userEvent.setup()
    ipam.listScanSuggestions.mockResolvedValue({ data: [suggestion()], total: 1 })
    ipam.createScanTarget.mockRejectedValue(
      new ApiError('CONFLICT', 'Subnet IoT already has a scan target', 409)
    )
    renderPage()

    const offer = await screen.findByTestId('suggestion-agent-1:sub-2')
    await user.click(within(offer).getByRole('button', { name: /Scan/ }))
    expect(await screen.findByText('Subnet IoT already has a scan target')).toBeInTheDocument()
    expect(ipam.requestScan).not.toHaveBeenCalled()
  })

  it('says when the target is set up but the scan request failed', async () => {
    const user = userEvent.setup()
    ipam.listScanSuggestions.mockResolvedValueOnce({ data: [suggestion()], total: 1 })
    ipam.createScanTarget.mockResolvedValue({ data: { id: 'tgt-2' } })
    ipam.requestScan.mockRejectedValue(new ApiError('UNAVAILABLE', 'busy', 503))
    renderPage()

    const offer = await screen.findByTestId('suggestion-agent-1:sub-2')
    await user.click(within(offer).getByRole('button', { name: /Scan/ }))
    expect(
      await screen.findByText('Scanning of IoT is set up, but the scan was not requested: busy')
    ).toBeInTheDocument()
  })

  it('sets an offer aside with Not now', async () => {
    const user = userEvent.setup()
    ipam.listScanSuggestions.mockResolvedValue({
      data: [suggestion(), suggestion({ subnet_id: 'sub-3', subnet_name: 'Guest' })],
      total: 2,
    })
    renderPage()

    const offer = await screen.findByTestId('suggestion-agent-1:sub-2')
    await user.click(within(offer).getByRole('button', { name: 'Not now' }))
    expect(screen.queryByTestId('suggestion-agent-1:sub-2')).toBeNull()
    expect(screen.getByTestId('suggestion-agent-1:sub-3')).toBeInTheDocument()
    expect(ipam.createScanTarget).not.toHaveBeenCalled()
  })

  it('renders an offer’s names as text, never as markup', async () => {
    ipam.listScanSuggestions.mockResolvedValue({
      data: [suggestion({ agent_name: '<img src=x onerror=alert(1)>' })],
      total: 1,
    })
    renderPage()
    const offer = await screen.findByTestId('suggestion-agent-1:sub-2')
    expect(offer).toHaveTextContent('<img src=x onerror=alert(1)>')
    expect(offer.querySelector('img')).toBeNull()
  })
})
