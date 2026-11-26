import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import * as ipam from '../api/ipam'
import { ToastProvider } from '../context'
import { POLL_MS } from '../utils/links'
import LinkedClients from './LinkedClients'

vi.mock('../api/ipam', () => ({
  getLinkWindow: vi.fn(),
  openLinkWindow: vi.fn(),
  cancelLinkWindow: vi.fn(),
  listLinkedClients: vi.fn(),
  revokeLinkedClient: vi.fn(),
}))

const CODE = 'ABCDEFGHJK'
const MCP_URL = 'https://ipam.example.com/mcp'
const OPENED_AT = '2026-11-26T12:00:00+00:00'

const closed = (over = {}) => ({
  configured: true,
  remote_writes: false,
  open: false,
  can_write: null,
  opened_at: null,
  expires_at: null,
  expires_in_s: null,
  wrong_codes: null,
  ended: null,
  ended_at: null,
  mcp_url: MCP_URL,
  ...over,
})

const open = (over = {}) => ({
  ...closed(),
  open: true,
  can_write: false,
  opened_at: OPENED_AT,
  // deliberately far from the browser's clock: the countdown never reads it
  expires_at: '2030-01-01T00:00:00+00:00',
  expires_in_s: 600,
  wrong_codes: 0,
  ...over,
})

const link = (over = {}) => ({
  id: '0f1e2d3c-0000-4000-8000-000000000001',
  client_id: 'c1',
  client_name: 'Example Assistant',
  client_registered: true,
  redirect_host: 'client.example.com',
  can_write: false,
  writes: false,
  created_at: '2026-11-26T10:00:00+00:00',
  last_used_at: '2026-11-26T11:00:00+00:00',
  revoked_at: null,
  ...over,
})

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <LinkedClients />
      </ToastProvider>
    </MemoryRouter>
  )
}

beforeEach(() => {
  ipam.getLinkWindow.mockResolvedValue({ data: closed() })
  ipam.listLinkedClients.mockResolvedValue({ data: [] })
  ipam.openLinkWindow.mockResolvedValue({ data: { ...open(), code: CODE } })
  ipam.cancelLinkWindow.mockResolvedValue({ data: closed({ ended: 'cancelled' }) })
})

afterEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
})

const table = async () => within(await screen.findByRole('table', { name: 'Linked clients' }))

describe('Linked clients list', () => {
  it('shows the redirect host, the name as the client’s quoted claim, access and Last contact', async () => {
    ipam.listLinkedClients.mockResolvedValue({ data: [link()] })
    renderPage()
    const t = await table()
    expect(t.getByRole('columnheader', { name: 'Last contact' })).toBeInTheDocument()
    const host = t.getByText('client.example.com')
    // the name is a quotation, attributed to the client, and is not bold
    const name = t.getByText('“Example Assistant”')
    expect(name.closest('strong, b, h1, h2, h3, th')).toBeNull()
    expect(getComputedStyle(name).fontWeight).not.toBe('bold')
    expect(name.parentElement).toHaveTextContent('calls itself “Example Assistant”')
    // the host comes first and is the larger text
    expect(host.compareDocumentPosition(name) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(host.className).toMatch(/text-sm/)
    expect(name.parentElement.className).toMatch(/text-xs/)
    expect(t.getByText('Read-only')).toBeInTheDocument()
  })

  it('renders a name that holds markup as text', async () => {
    ipam.listLinkedClients.mockResolvedValue({
      data: [link({ client_name: '<img src=x onerror=alert(1)>' })],
    })
    renderPage()
    const t = await table()
    expect(t.getByText('“<img src=x onerror=alert(1)>”')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })

  it('calls a link with no kept name by what is known', async () => {
    ipam.listLinkedClients.mockResolvedValue({
      data: [
        link({ id: 'a0000000-0000-4000-8000-000000000001', client_name: null }),
        link({
          id: 'b0000000-0000-4000-8000-000000000002',
          client_name: null,
          client_registered: false,
          redirect_host: 'other.example.com',
        }),
      ],
    })
    renderPage()
    const t = await table()
    expect(t.getByText(/gave no name this server keeps/)).toBeInTheDocument()
    expect(t.getByText(/registration deleted/)).toBeInTheDocument()
    expect(t.getByText('link a0000000')).toBeInTheDocument()
  })

  it('says a link made read-write is read-only while remote writes are off', async () => {
    ipam.listLinkedClients.mockResolvedValue({
      data: [
        link({ id: 'a', can_write: true, writes: false }),
        link({ id: 'b', can_write: true, writes: true, redirect_host: 'rw.example.com' }),
      ],
    })
    renderPage()
    const t = await table()
    expect(t.getByText('Read-only now')).toBeInTheDocument()
    expect(t.getByText(/Linked read and write; this server has remote writes off/)).toBeInTheDocument()
    expect(t.getByText('Read and write')).toBeInTheDocument()
  })

  it('revokes a link after asking, then lists it as revoked', async () => {
    const user = userEvent.setup()
    ipam.listLinkedClients.mockResolvedValueOnce({ data: [link()] })
    ipam.revokeLinkedClient.mockResolvedValue({ data: link({ revoked_at: OPENED_AT }) })
    renderPage()
    const t = await table()
    ipam.listLinkedClients.mockResolvedValue({ data: [link({ revoked_at: OPENED_AT })] })
    await user.click(t.getByRole('button', { name: 'Revoke the link to client.example.com' }))
    const dialog = screen.getByRole('dialog', { name: 'Revoke the link to client.example.com?' })
    expect(dialog).toHaveTextContent('the client’s next request is refused')
    expect(ipam.revokeLinkedClient).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button', { name: 'Revoke link' }))
    expect(ipam.revokeLinkedClient).toHaveBeenCalledWith(link().id)
    expect(await screen.findByText('Recently revoked')).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: 'Linked clients' })).toBeNull()
  })

  it('closes the question when the server had already revoked the link', async () => {
    const user = userEvent.setup()
    ipam.listLinkedClients.mockResolvedValue({ data: [link()] })
    ipam.revokeLinkedClient.mockRejectedValue(
      new ApiError('ALREADY_REVOKED', 'This link is already revoked', 409)
    )
    renderPage()
    const t = await table()
    await user.click(t.getByRole('button', { name: /Revoke the link/ }))
    await user.click(screen.getByRole('button', { name: 'Revoke link' }))
    expect(await screen.findByText('This link is already revoked')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('says what is needed when remote MCP is not configured, and offers no link', async () => {
    ipam.getLinkWindow.mockResolvedValue({
      data: closed({ configured: false, mcp_url: null }),
    })
    renderPage()
    expect(await screen.findByText('Remote MCP is not configured.')).toBeInTheDocument()
    expect(screen.getByText(/PUBLIC_BASE_URL/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Link a client/ })).toBeDisabled()
  })

  it('warns of a window opened elsewhere, whose code it cannot show, and cancels it', async () => {
    const user = userEvent.setup()
    ipam.getLinkWindow.mockResolvedValue({ data: open() })
    renderPage()
    expect(await screen.findByText(/A link window is open/)).toHaveTextContent(
      'Its code is shown only in the dialog that opened it'
    )
    await user.click(screen.getByRole('button', { name: 'Cancel window' }))
    expect(ipam.cancelLinkWindow).toHaveBeenCalled()
  })
})

describe('Link a client dialog', () => {
  it('offers the write checkbox, off by default, only while remote writes are on', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Link a client/ }))
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(screen.getByText(/The link will be read-only/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Open link window' }))
    expect(ipam.openLinkWindow).toHaveBeenCalledWith({ can_write: false })
  })

  it('sends can_write only when the owner ticks it', async () => {
    const user = userEvent.setup()
    ipam.getLinkWindow.mockResolvedValue({ data: closed({ remote_writes: true }) })
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Link a client/ }))
    const box = screen.getByRole('checkbox', { name: /Allow this client to make changes/ })
    expect(box).not.toBeChecked()
    expect(box).toHaveClass('tlg-check')
    await user.click(box)
    await user.click(screen.getByRole('button', { name: 'Open link window' }))
    expect(ipam.openLinkWindow).toHaveBeenCalledWith({ can_write: true })
  })

  it('shows the code and the URL to give the client, and keeps the code out of storage', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Link a client/ }))
    await user.click(screen.getByRole('button', { name: 'Open link window' }))
    expect(await screen.findByTestId('link-code')).toHaveTextContent('ABCDE FGHJK')
    expect(screen.getByText(MCP_URL)).toBeInTheDocument()
    expect(screen.getByRole('timer')).toHaveTextContent(/Closes in (10:00|9:59)/)
    expect(JSON.stringify({ ...localStorage })).not.toContain(CODE)
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(CODE)
    expect(window.location.href).not.toContain(CODE)
  })

  it('cancels the window when the dialog is closed while it is open, and the code goes', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Link a client/ }))
    await user.click(screen.getByRole('button', { name: 'Open link window' }))
    await screen.findByTestId('link-code')
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(ipam.cancelLinkWindow).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(document.body.textContent).not.toContain('ABCDE')
  })

  it('keeps the dialog and the code when the cancel fails', async () => {
    const user = userEvent.setup()
    ipam.cancelLinkWindow.mockRejectedValue(new Error('server unreachable'))
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Link a client/ }))
    await user.click(screen.getByRole('button', { name: 'Open link window' }))
    await screen.findByTestId('link-code')
    await user.click(screen.getByRole('button', { name: 'Cancel link' }))
    expect(await screen.findByText(/could not be cancelled/)).toBeInTheDocument()
    expect(screen.getByTestId('link-code')).toHaveTextContent('ABCDE FGHJK')
  })

  it('says a window is already open and that opening replaces it', async () => {
    const user = userEvent.setup()
    ipam.getLinkWindow.mockResolvedValue({ data: open() })
    renderPage()
    await screen.findByText(/A link window is open/)
    await user.click(screen.getByRole('button', { name: /Link a client/ }))
    expect(screen.getByRole('dialog')).toHaveTextContent('opening a new window replaces it')
    expect(screen.getByRole('button', { name: 'Open a new window' })).toBeInTheDocument()
  })

  describe('while the window is open', () => {
    // in steps of at most a second, each in its own act(), as the Agents page's tests do
    async function advance(ms) {
      let left = ms
      do {
        const step = Math.min(left, 1000)
        await act(() => vi.advanceTimersByTimeAsync(step))
        left -= step
      } while (left > 0)
    }

    async function openDialog() {
      renderPage()
      await advance(0)
      fireEvent.click(screen.getByRole('button', { name: /Link a client/ }))
      fireEvent.click(screen.getByRole('button', { name: 'Open link window' }))
      await advance(0)
      expect(screen.getByTestId('link-code')).toHaveTextContent('ABCDE FGHJK')
    }

    beforeEach(() => {
      vi.useFakeTimers({
        toFake: ['Date', 'performance', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'],
      })
      vi.setSystemTime(new Date('2026-11-26T12:00:00Z'))
    })
    afterEach(() => {
      vi.useRealTimers()
    })

    it('counts down from expires_in_s by the monotonic clock, whatever the wall clock does', async () => {
      ipam.getLinkWindow.mockResolvedValueOnce({ data: closed() })
      // every later answer is what the server would give: the same window, with its
      // seconds left by a monotonic clock of its own, rounded down as the server does
      const start = performance.now()
      ipam.getLinkWindow.mockImplementation(async () => ({
        data: open({ expires_in_s: 600 - Math.ceil((performance.now() - start) / 1000) }),
      }))
      await openDialog()
      expect(screen.getByRole('timer')).toHaveTextContent('Closes in 10:00')
      vi.setSystemTime(new Date('2026-11-26T15:00:00Z'))
      await advance(61 * 1000)
      expect(screen.getByRole('timer')).toHaveTextContent('Closes in 8:59')
      vi.setSystemTime(new Date('2026-11-26T09:00:00Z'))
      await advance(60 * 1000)
      expect(screen.getByRole('timer')).toHaveTextContent('Closes in 7:59')
    })

    it('says "Closing…" at zero until the server says the window is closed', async () => {
      ipam.getLinkWindow.mockResolvedValueOnce({ data: closed() })
      ipam.openLinkWindow.mockResolvedValue({ data: { ...open({ expires_in_s: 3 }), code: CODE } })
      // the server still calls it open, with 0 s left, at the last second
      ipam.getLinkWindow.mockResolvedValue({ data: open({ expires_in_s: 0 }) })
      await openDialog()
      await advance(4000)
      expect(screen.getByRole('timer')).toHaveTextContent('Closing…')
      expect(screen.getByTestId('link-code')).toBeInTheDocument()
      ipam.getLinkWindow.mockResolvedValue({ data: closed({ ended: 'expired' }) })
      await advance(POLL_MS)
      expect(screen.getByRole('dialog')).toHaveTextContent('The window ran out')
      expect(screen.queryByTestId('link-code')).toBeNull()
    })

    it('learns of the link by polling, drops the code, refreshes the list, and stops polling', async () => {
      ipam.getLinkWindow.mockResolvedValueOnce({ data: closed() })
      ipam.getLinkWindow.mockResolvedValue({ data: open() })
      await openDialog()
      await advance(POLL_MS)
      expect(screen.getByTestId('link-code')).toBeInTheDocument()
      const listed = ipam.listLinkedClients.mock.calls.length
      ipam.getLinkWindow.mockResolvedValue({ data: closed({ ended: 'linked' }) })
      ipam.listLinkedClients.mockResolvedValue({ data: [link()] })
      await advance(POLL_MS)
      expect(screen.getByRole('dialog')).toHaveTextContent('Linked. The client is in the list below.')
      expect(screen.queryByTestId('link-code')).toBeNull()
      expect(document.body.textContent).not.toContain('ABCDE')
      expect(ipam.listLinkedClients.mock.calls.length).toBeGreaterThan(listed)
      const polls = ipam.getLinkWindow.mock.calls.length
      await advance(5 * POLL_MS)
      // only the page's own refresh, at most, after the end: not the dialog's poll
      expect(ipam.getLinkWindow.mock.calls.length).toBeLessThanOrEqual(polls + 1)
      // a closed dialog no longer polls either
      fireEvent.click(screen.getByRole('button', { name: 'Done' }))
      expect(ipam.cancelLinkWindow).not.toHaveBeenCalled()
    })

    it('says the window was replaced when another one is open in its place', async () => {
      ipam.getLinkWindow.mockResolvedValueOnce({ data: closed() })
      ipam.getLinkWindow.mockResolvedValue({ data: open({ opened_at: '2026-11-26T12:05:00+00:00' }) })
      await openDialog()
      await advance(POLL_MS)
      expect(screen.getByRole('dialog')).toHaveTextContent('This window was replaced')
      expect(screen.queryByTestId('link-code')).toBeNull()
    })

    it('says the window is gone when the server forgot it in a restart', async () => {
      ipam.getLinkWindow.mockResolvedValueOnce({ data: closed() })
      ipam.getLinkWindow.mockResolvedValue({ data: closed() })
      await openDialog()
      await advance(POLL_MS)
      expect(screen.getByRole('dialog')).toHaveTextContent('the server has restarted')
    })

    it('shows the wrong codes typed on the window, which do not close it', async () => {
      ipam.getLinkWindow.mockResolvedValueOnce({ data: closed() })
      ipam.getLinkWindow.mockResolvedValue({ data: open({ wrong_codes: 3 }) })
      await openDialog()
      expect(screen.queryByText(/wrong code/)).toBeNull()
      await advance(POLL_MS)
      expect(screen.getByText(/3 wrong codes have been typed/)).toHaveTextContent(
        'A wrong code ends only that request, never the window'
      )
      expect(screen.getByTestId('link-code')).toBeInTheDocument()
    })

    it('keeps polling through a failed check and says so', async () => {
      ipam.getLinkWindow.mockResolvedValueOnce({ data: closed() })
      ipam.getLinkWindow.mockResolvedValue({ data: open() })
      await openDialog()
      // the next ask is the dialog's poll (the page's next refresh is 15 s away)
      ipam.getLinkWindow.mockRejectedValueOnce(new Error('server unreachable'))
      await advance(POLL_MS)
      expect(screen.getByText(/Could not check the window: server unreachable/)).toBeInTheDocument()
      await advance(POLL_MS)
      expect(screen.queryByText(/Could not check the window/)).toBeNull()
      expect(screen.getByTestId('link-code')).toBeInTheDocument()
    })
  })
})
