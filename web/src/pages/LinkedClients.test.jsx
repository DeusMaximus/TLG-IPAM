import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
const WINDOW_ID = 'a1b2c3d4-0000-4000-8000-000000000001'
// a window another tab or the API opened in place of the dialog's
const OTHER = { window_id: 'a1b2c3d4-0000-4000-8000-000000000002', opened_at: '2026-11-26T12:05:00+00:00' }

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
  window_id: WINDOW_ID,
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
  never_completed: false,
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
  ipam.cancelLinkWindow.mockResolvedValue({ data: { ...closed({ ended: 'cancelled' }), cancelled: true } })
})

afterEach(async () => {
  // unmount here, before the mocks are cleared: a dialog still holding its window
  // cancels it as it goes, and that call belongs to this test, not the next
  cleanup()
  await act(async () => {})
  // the page never asks to cancel a window it did not name: every cancel names one
  for (const call of ipam.cancelLinkWindow.mock.calls) expect(call[0]).toMatch(/^a1b2c3d4-/)
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
})

// Not bold, as production would make it so: by an element (above), or by a weight
// utility class or an inline weight on the name or anything around it in its cell. jsdom
// has no Tailwind sheet, so the classes are read, not the computed style (round 1 of
// #195's briefed review, finding 3).
const BOLD_CLASS = /(^|\s)(font-(semibold|bold|extrabold|black)|font-\[(\d+|bold|bolder)\])(\s|$)/
function expectNotBold(element) {
  for (let el = element; el && el.tagName !== 'TD'; el = el.parentElement) {
    expect(el.className).not.toMatch(BOLD_CLASS)
    const weight = el.style.fontWeight
    expect(['bold', 'bolder'].includes(weight) || Number(weight) >= 600).toBe(false)
  }
}

// Whether any mounted component holds the link code, in its state, its props or its
// refs: a walk of React's tree from the page's root (round 1 of #195's briefed review,
// finding 4). It reads what React keeps for the mounted components, not the JavaScript
// heap: a closure or a value React has let go of but not yet freed is not seen.
function reactHolds(text) {
  const roots = [...document.body.children]
    .map((el) => el[Object.keys(el).find((k) => k.startsWith('__reactContainer$'))])
    .filter(Boolean)
  const skip = new Set(['return', 'alternate', 'stateNode', '_owner', '_debugOwner', '_store', 'deletions', 'sibling', 'child'])
  const seen = new Set()
  const found = (value, depth) => {
    if (typeof value === 'string') return value.includes(text)
    if (!value || typeof value !== 'object' || depth > 25 || seen.has(value)) return false
    if (value instanceof Node) return false
    seen.add(value)
    return Object.keys(value).some((k) => !skip.has(k) && found(value[k], depth + 1))
  }
  const visit = (fiber) => {
    for (let f = fiber; f; f = f.sibling) {
      if (found(f.memoizedState, 0) || found(f.memoizedProps, 0)) return true
      if (visit(f.child)) return true
    }
    return false
  }
  return roots.some((root) => visit(root.child ?? root.current?.child))
}

const table = async () => within(await screen.findByRole('table', { name: 'Linked clients' }))

describe('Linked clients list', () => {
  // #198: Gemini can be linked but is not a supported client
  it('names the three supported web clients in its intro, and not Gemini', async () => {
    renderPage()
    const intro = await screen.findByText(/^Web assistants/)
    expect(intro).toHaveTextContent(
      'Web assistants (Claude.ai, ChatGPT, Mistral) linked to this server’s MCP endpoint.'
    )
    expect(document.body.textContent).not.toContain('Gemini')
  })

  it('shows the redirect host, the name as the client’s quoted claim, access and Last contact', async () => {
    ipam.listLinkedClients.mockResolvedValue({ data: [link()] })
    renderPage()
    const t = await table()
    expect(t.getByRole('columnheader', { name: 'Last contact' })).toBeInTheDocument()
    const host = t.getByText('client.example.com')
    // the name is a quotation, attributed to the client, and is not bold
    const name = t.getByText('“Example Assistant”')
    expect(name.closest('strong, b, h1, h2, h3, th')).toBeNull()
    expectNotBold(name)
    expect(name.parentElement).toHaveTextContent('unverified calls itself “Example Assistant”')
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
    expect(t.getByText(/no name this server keeps/)).toBeInTheDocument()
    expect(t.getByText(/registration deleted/)).toBeInTheDocument()
    expect(t.getByText('link a0000000')).toBeInTheDocument()
  })

  it('says which link was never completed, from the server’s flag alone, and offers revoke', async () => {
    ipam.listLinkedClients.mockResolvedValue({
      data: [
        link({ id: 'a', last_used_at: null, never_completed: true }),
        // approved a moment ago, its code not yet expired: never contacted, not flagged
        link({ id: 'b', last_used_at: null, redirect_host: 'young.example.com' }),
      ],
    })
    renderPage()
    const t = await table()
    const notes = t.getAllByText(/^Never completed: as of this list/)
    expect(notes).toHaveLength(1)
    const row = notes[0].closest('tr')
    expect(within(row).getByText('client.example.com')).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: /Revoke the link to client.example.com/ })).toBeInTheDocument()
    const young = t.getByText('young.example.com').closest('tr')
    expect(within(young).queryByText(/Never completed/)).toBeNull()
  })

  it('says a revoke frees no place when the registration holds another live link', async () => {
    ipam.listLinkedClients.mockResolvedValue({
      data: [
        link({ id: 'a', client_id: 'shared', last_used_at: null, never_completed: true }),
        link({ id: 'b', client_id: 'shared', redirect_host: 'other.example.com' }),
      ],
    })
    renderPage()
    const t = await table()
    const [note] = t.getAllByText(/^Never completed/)
    expect(note).toHaveTextContent(/frees no place: its registration holds another link listed here/)
    expect(note).not.toHaveTextContent(/link it again/)
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
    expect(screen.getAllByText(/PUBLIC_BASE_URL/).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /Link a client/ })).toBeDisabled()
    // with no link there is nothing more to say
    expect(screen.queryByText(/do not work while it is unset/)).toBeNull()
  })

  it('says existing links do not work while remote MCP is off, and still offers revoke', async () => {
    ipam.getLinkWindow.mockResolvedValue({ data: closed({ configured: false, mcp_url: null }) })
    ipam.listLinkedClients.mockResolvedValue({ data: [link()] })
    renderPage()
    expect(await screen.findByText(/do not work while it is unset/)).toHaveTextContent(
      'their tokens are refused'
    )
    const t = await table()
    expect(t.getByRole('button', { name: 'Revoke the link to client.example.com' })).toBeInTheDocument()
  })

  it('warns of a window opened elsewhere, whose code it cannot show, and cancels it', async () => {
    const user = userEvent.setup()
    ipam.getLinkWindow.mockResolvedValue({ data: open() })
    renderPage()
    expect(await screen.findByText(/A link window is open/)).toHaveTextContent(
      'Its code is shown only in the dialog that opened it'
    )
    await user.click(screen.getByRole('button', { name: 'Cancel window' }))
    expect(ipam.cancelLinkWindow).toHaveBeenCalledWith(WINDOW_ID)
  })

  it('says so when the window the banner showed had already been replaced', async () => {
    const user = userEvent.setup()
    ipam.getLinkWindow.mockResolvedValue({ data: open() })
    ipam.cancelLinkWindow.mockResolvedValue({ data: { ...open(OTHER), cancelled: false } })
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Cancel window' }))
    expect(ipam.cancelLinkWindow).toHaveBeenCalledWith(WINDOW_ID)
    expect(await screen.findByText(/nothing was cancelled/)).toBeInTheDocument()
  })
})

describe('Link a client dialog', () => {
  it('says which client registers the moment the URL is entered, without naming Gemini', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Link a client/ }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent(
      'some clients (ChatGPT, for one) register the moment the URL is entered, and are refused when no window is open.'
    )
    expect(document.body.textContent).not.toContain('Gemini')
  })

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
    // the server reports the dialog's own window, so the cancel is of that one
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(ipam.cancelLinkWindow).toHaveBeenCalledTimes(1)
    expect(ipam.cancelLinkWindow).toHaveBeenCalledWith(WINDOW_ID)
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
    ipam.getLinkWindow.mockResolvedValue({ data: open() })
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

  // Codex's P2 on e911edf: no window the dialog opened is left open once the dialog no
  // longer shows its code, however it went away, and only the dialog's own is cancelled
  describe('no window is left open that the dialog no longer shows', () => {
    // an open request the test answers when it chooses
    function pendingOpen() {
      let answer, refuse
      ipam.openLinkWindow.mockImplementation(
        () =>
          new Promise((resolve, reject) => {
            answer = () => resolve({ data: { ...open(), code: CODE } })
            refuse = () => reject(new Error('server unreachable'))
          })
      )
      return { answer: () => answer(), refuse: () => refuse() }
    }

    async function startOpening(user) {
      const view = renderPage()
      await user.click(await screen.findByRole('button', { name: /Link a client/ }))
      await user.click(screen.getByRole('button', { name: 'Open link window' }))
      // from here on the server reports the window the request opens
      ipam.getLinkWindow.mockResolvedValue({ data: open() })
      return view
    }

    for (const [how, dismiss] of [
      ['its close button', () => userEvent.click(screen.getByRole('button', { name: 'Close' }))],
      ['its Cancel button', () => userEvent.click(screen.getByRole('button', { name: 'Cancel' }))],
    ]) {
      it(`cancels the window a request in flight opens, after a dismissal by ${how}`, async () => {
        const user = userEvent.setup()
        const request = pendingOpen()
        await startOpening(user)
        await dismiss()
        expect(screen.queryByRole('dialog')).toBeNull()
        await act(async () => request.answer())
        await waitFor(() => expect(ipam.cancelLinkWindow).toHaveBeenCalledTimes(1))
        expect(ipam.cancelLinkWindow).toHaveBeenCalledWith(WINDOW_ID)
        expect(document.body.textContent).not.toContain('ABCDE')
      })
    }

    it('cancels the window a request in flight opens when the page goes away', async () => {
      const user = userEvent.setup()
      const request = pendingOpen()
      const view = await startOpening(user)
      view.unmount()
      await act(async () => request.answer())
      await waitFor(() => expect(ipam.cancelLinkWindow).toHaveBeenCalledTimes(1))
      expect(ipam.cancelLinkWindow).toHaveBeenCalledWith(WINDOW_ID)
    })

    it('cancels its open window when the page goes away while the code is shown', async () => {
      const user = userEvent.setup()
      const view = await startOpening(user)
      await screen.findByTestId('link-code')
      view.unmount()
      await waitFor(() => expect(ipam.cancelLinkWindow).toHaveBeenCalledTimes(1))
      expect(ipam.cancelLinkWindow).toHaveBeenCalledWith(WINDOW_ID)
    })

    it('cancels nothing, and says nothing, when the request fails after a dismissal', async () => {
      const user = userEvent.setup()
      const request = pendingOpen()
      await startOpening(user)
      await user.click(screen.getByRole('button', { name: 'Close' }))
      await act(async () => request.refuse())
      expect(ipam.cancelLinkWindow).not.toHaveBeenCalled()
      expect(screen.queryByText(/server unreachable/)).toBeNull()
    })

    // The server compares the id under its lock and leaves a replacement open
    // (`cancelled: false`, test_link_window.py); the dialog's part is to name its own
    it('names its own window when another replaced it before the request answered', async () => {
      const user = userEvent.setup()
      const request = pendingOpen()
      await startOpening(user)
      await user.click(screen.getByRole('button', { name: 'Close' }))
      ipam.cancelLinkWindow.mockResolvedValue({ data: { ...open(OTHER), cancelled: false } })
      await act(async () => request.answer())
      await waitFor(() => expect(ipam.cancelLinkWindow).toHaveBeenCalledTimes(1))
      expect(ipam.cancelLinkWindow).toHaveBeenCalledWith(WINDOW_ID)
      expect(screen.queryByText(/could not be cancelled/)).toBeNull()
    })

    it('closes, naming its own window, when another tab opened one in its place', async () => {
      const user = userEvent.setup()
      await startOpening(user)
      await screen.findByTestId('link-code')
      ipam.cancelLinkWindow.mockResolvedValue({ data: { ...open(OTHER), cancelled: false } })
      await user.click(screen.getByRole('button', { name: 'Close' }))
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
      expect(ipam.cancelLinkWindow).toHaveBeenCalledTimes(1)
      expect(ipam.cancelLinkWindow).toHaveBeenCalledWith(WINDOW_ID)
    })
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
      ipam.getLinkWindow.mockResolvedValue({ data: closed({ ended: 'expired', ended_window_id: WINDOW_ID }) })
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
      // the walk finds the code while the window is open: it can see where it is held
      expect(reactHolds('ABCDE')).toBe(true)
      const listed = ipam.listLinkedClients.mock.calls.length
      ipam.getLinkWindow.mockResolvedValue({ data: closed({ ended: 'linked', ended_window_id: WINDOW_ID }) })
      ipam.listLinkedClients.mockResolvedValue({ data: [link()] })
      await advance(POLL_MS)
      expect(screen.getByRole('dialog')).toHaveTextContent('Linked. The client is in the list below.')
      expect(screen.queryByTestId('link-code')).toBeNull()
      expect(document.body.textContent).not.toContain('ABCDE')
      // and no mounted component holds it once the window has ended, though the dialog is
      // still open: not in the DOM alone, in no state, prop or ref
      expect(reactHolds('ABCDE')).toBe(false)
      expect(ipam.listLinkedClients.mock.calls.length).toBeGreaterThan(listed)
      const polls = ipam.getLinkWindow.mock.calls.length
      await advance(5 * POLL_MS)
      // only the page's own refresh, at most, after the end: not the dialog's poll
      expect(ipam.getLinkWindow.mock.calls.length).toBeLessThanOrEqual(polls + 1)
      // a closed dialog no longer polls either
      fireEvent.click(screen.getByRole('button', { name: 'Done' }))
      expect(ipam.cancelLinkWindow).not.toHaveBeenCalled()
    })

    // Round 1 of #195's briefed review, finding 1: the state's end is the latest of any
    // window's, so the dialog claims an outcome only for an end that names its window
    async function endedAs(state) {
      ipam.getLinkWindow.mockResolvedValueOnce({ data: closed() })
      ipam.getLinkWindow.mockResolvedValue({ data: state })
      await openDialog()
      await advance(POLL_MS)
      expect(screen.queryByTestId('link-code')).toBeNull()
      expect(reactHolds('ABCDE')).toBe(false)
      return screen.getByRole('dialog')
    }
    const NOT_KNOWN = 'Whether a client was linked with it is not known here'

    it('does not say it was replaced, or that nothing linked, when another window is open', async () => {
      const dialog = await endedAs(open(OTHER))
      expect(dialog).toHaveTextContent('another one is open now')
      expect(dialog).toHaveTextContent(NOT_KNOWN)
      expect(dialog).not.toHaveTextContent('Nothing was linked')
    })

    it('does not take a later window’s cancel for its own: it may have linked first', async () => {
      // A links; B opens and is cancelled before A's dialog asks
      const dialog = await endedAs(closed({ ended: 'cancelled', ended_window_id: OTHER.window_id }))
      expect(dialog).toHaveTextContent(NOT_KNOWN)
      expect(dialog).not.toHaveTextContent('Nothing was linked')
      expect(dialog).not.toHaveTextContent('cancelled')
    })

    it('does not take a later window’s link for its own', async () => {
      // A is replaced by B, and B links before A's dialog asks
      const dialog = await endedAs(closed({ ended: 'linked', ended_window_id: OTHER.window_id }))
      expect(dialog).toHaveTextContent(NOT_KNOWN)
      expect(dialog).not.toHaveTextContent('Linked.')
    })

    it('does not say nothing linked after a restart forgot every end', async () => {
      // A may have linked, then the server restarted before the dialog asked
      const dialog = await endedAs(closed())
      expect(dialog).toHaveTextContent('the server has restarted')
      expect(dialog).toHaveTextContent(NOT_KNOWN)
      expect(dialog).not.toHaveTextContent('Nothing was linked')
    })

    it('does not say nothing linked when its own approval failed', async () => {
      const dialog = await endedAs(closed({ ended: 'failed', ended_window_id: WINDOW_ID }))
      expect(dialog).toHaveTextContent('The approval failed on the server')
      expect(dialog).toHaveTextContent(NOT_KNOWN)
      expect(dialog).not.toHaveTextContent('Nothing was linked')
    })

    it('says nothing linked only for its own cancel, expiry or replacement', async () => {
      const dialog = await endedAs(closed({ ended: 'cancelled', ended_window_id: WINDOW_ID }))
      expect(dialog).toHaveTextContent('The window was cancelled elsewhere')
      expect(dialog).toHaveTextContent('Nothing was linked by it.')
    })

    it('shows the wrong codes typed on the window, which do not close it', async () => {
      ipam.getLinkWindow.mockResolvedValueOnce({ data: closed() })
      ipam.getLinkWindow.mockResolvedValue({ data: open({ wrong_codes: 3 }) })
      await openDialog()
      expect(screen.queryByText(/wrong code/)).toBeNull()
      await advance(POLL_MS)
      expect(screen.getByText(/3 wrong codes have been typed/)).toHaveTextContent(
        'the fifth on one request ends that request, and its client has to start again. None of them closes this window.'
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
