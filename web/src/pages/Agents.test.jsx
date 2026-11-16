import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import * as ipam from '../api/ipam'
import { ToastProvider } from '../context'
import Agents from './Agents'

vi.mock('../api/ipam', () => ({
  listAgents: vi.fn(),
  createAgent: vi.fn(),
  updateAgent: vi.fn(),
  revokeAgentTokens: vi.fn(),
  deleteAgent: vi.fn(),
  listScanTargets: vi.fn(),
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

  it('shows the token once, and nothing can show it again after the dialog closes', async () => {
    const user = userEvent.setup()
    ipam.listAgents.mockResolvedValueOnce({ data: [], total: 0 })
    ipam.listScanTargets.mockResolvedValue({ data: [], total: 0 })
    ipam.createAgent.mockResolvedValue({ data: { ...agentRow(), token: TOKEN } })
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Add agent/ }))
    await user.type(screen.getByLabelText('Name'), '  lab-vlan20  ')
    await user.click(screen.getByRole('button', { name: 'Create agent' }))

    expect(ipam.createAgent).toHaveBeenCalledWith({ name: 'lab-vlan20' })
    expect(await screen.findByTestId('agent-token')).toHaveTextContent(TOKEN)
    const command = screen.getByTestId('agent-command').textContent
    expect(command).toContain(`-e IPAM_AGENT_TOKEN=${TOKEN}`)
    expect(command).toContain(`-e IPAM_URL=${window.location.origin}`)
    expect(command).toContain('--network host')
    expect(command).toContain('--cap-add NET_RAW')
    expect(command).toMatch(/tlg-ipam-agent:0\.2\.0$/)

    // a stray click on the backdrop does not throw the token away
    await user.click(screen.getByRole('dialog').parentElement)
    expect(screen.getByTestId('agent-token')).toBeInTheDocument()

    // the list is reloaded from the owner route, which carries no token
    await waitFor(() => expect(ipam.listAgents).toHaveBeenCalledTimes(2))

    await user.click(screen.getByRole('button', { name: /Done, I have saved the token/ }))

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(await screen.findByText(`${PREFIX}…`)).toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain(TOKEN)
    expect(JSON.stringify({ ...localStorage })).not.toContain(TOKEN)
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(TOKEN)

    // opening the dialog again starts a new agent; it does not bring the token back
    await user.click(screen.getByRole('button', { name: /Add agent/ }))
    expect(screen.getByLabelText('Name')).toHaveValue('')
    expect(document.body.innerHTML).not.toContain(TOKEN)
    expect(ipam.createAgent).toHaveBeenCalledTimes(1)
  })

  it('drops the token when the dialog is closed with its close button too', async () => {
    const user = userEvent.setup()
    ipam.createAgent.mockResolvedValue({ data: { ...agentRow(), token: TOKEN } })
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Add agent/ }))
    await user.type(screen.getByLabelText('Name'), 'lab-vlan20')
    await user.click(screen.getByRole('button', { name: 'Create agent' }))
    await screen.findByTestId('agent-token')

    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.body.innerHTML).not.toContain(TOKEN)
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

    respond({ data: { ...agentRow(), token: TOKEN } })
    expect(await screen.findByTestId('agent-token')).toHaveTextContent(TOKEN)
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
})
