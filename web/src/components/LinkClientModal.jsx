import { useEffect, useRef, useState } from 'react'
import { cancelLinkWindow, getLinkWindow, openLinkWindow } from '../api/ipam'
import { useToast } from '../context'
import { monotonicNow, received } from '../utils/agents'
import { countdown, ENDED_TEXT, groupedCode, POLL_MS, sameWindow, windowLeft } from '../utils/links'
import CopyBlock from './CopyBlock'
import Modal, { primaryBtn, secondaryBtn } from './Modal'

// Cancel the window opened at `openedAt`, and no other: `DELETE /link-window` ends
// whatever window is open, so the state is asked first, and a window that has ended or
// been replaced since is left alone. True when it was cancelled.
export async function cancelOwn(openedAt) {
  const now = (await getLinkWindow()).data
  if (!now.open || now.opened_at !== openedAt) return false
  await cancelLinkWindow()
  return true
}

// "Link a client" (v0.2 spec §6.3). Opening the window gives the link code, in that one
// answer: the code is kept in this dialog's state alone, never in storage, the URL or
// anything that outlives the dialog, so a reload loses it. So no window this dialog
// opened is left open once the dialog no longer shows its code: closing the dialog while
// its window is open cancels the window, and so does unmounting it (navigating away);
// a dialog dismissed while its open request is in flight cancels that window as soon as
// the answer arrives. Each cancel is of this dialog's own window only (`cancelOwn`).
// While open, the dialog asks the server how the window stands every POLL_MS and stops
// once it has ended (a link, its expiry, a cancel or a replacement from elsewhere), or
// when the dialog closes.
export default function LinkClientModal({ state, onClose, onChanged }) {
  const toast = useToast()
  const [canWrite, setCanWrite] = useState(false)
  const [busy, setBusy] = useState(false)
  // the window this dialog opened, as last answered, with when it was asked for
  const [opened, setOpened] = useState(null)
  const [code, setCode] = useState(null)
  // how it ended: a key of ENDED_TEXT
  const [ended, setEnded] = useState(null)
  const [pollError, setPollError] = useState(null)
  const [mono, setMono] = useState(() => monotonicNow())
  // what the poll compares a later answer with, without restarting it on each answer
  const openedRef = useRef(null)
  useEffect(() => {
    openedRef.current = opened
  }, [opened])
  // the `opened_at` of the window this dialog holds open and shows the code of, if any
  const held = useRef(null)
  // set once the dialog is dismissed: an open request still in flight then cancels its
  // window when it answers, since no dialog is left to show the code
  const dismissed = useRef(false)
  // the page's callbacks as they are now, for the unmount below
  const latest = useRef({ toast, onChanged })
  useEffect(() => {
    latest.current = { toast, onChanged }
  })

  // Unmounting (navigating away, or the page closing the dialog) cancels a window the
  // dialog still holds. Set again on mount, since StrictMode mounts twice in development.
  useEffect(() => {
    const flags = { dismissed, held, latest }
    flags.dismissed.current = false
    return () => {
      flags.dismissed.current = true
      const openedAt = flags.held.current
      if (!openedAt) return
      flags.held.current = null
      cancelOwn(openedAt)
        .catch((err) =>
          flags.latest.current.toast.error(`The link window could not be cancelled: ${err.message}`)
        )
        .finally(() => flags.latest.current.onChanged?.())
    }
  }, [])

  const offerWrites = Boolean(state?.remote_writes)
  const isOpen = Boolean(opened) && !ended

  const open = async () => {
    setBusy(true)
    const askedAt = monotonicNow()
    let res
    try {
      res = await openLinkWindow({ can_write: offerWrites && canWrite })
    } catch (err) {
      // nothing was opened; after a dismissal there is no one to tell
      if (!dismissed.current) {
        toast.error(err.message)
        setBusy(false)
      }
      return
    }
    const { code: given, ...window } = res.data
    if (dismissed.current) {
      // dismissed while the request was in flight: nothing can show this code
      try {
        await cancelOwn(window.opened_at)
      } catch (err) {
        toast.error(`A link window opened after the dialog closed, and could not be cancelled: ${err.message}`)
      }
      onChanged?.()
      return
    }
    held.current = window.opened_at
    setOpened(received(window, askedAt))
    setCode(given)
    setEnded(null)
    setPollError(null)
    setMono(monotonicNow())
    setBusy(false)
    onChanged?.()
  }

  // the end of the window: the code goes with it
  const finish = (how) => {
    held.current = null
    setEnded(how)
    setCode(null)
    onChanged?.()
  }

  useEffect(() => {
    if (!isOpen) return undefined
    let alive = true
    const poll = async () => {
      const askedAt = monotonicNow()
      try {
        const res = await getLinkWindow()
        if (!alive) return
        setPollError(null)
        const now = res.data
        if (sameWindow(openedRef.current, now)) {
          setOpened(received(now, askedAt))
        } else if (now.open) {
          finish('replaced')
        } else {
          finish(now.ended || 'gone')
        }
      } catch (err) {
        if (alive) setPollError(err.message)
      }
    }
    const pollId = setInterval(poll, POLL_MS)
    const tickId = setInterval(() => setMono(monotonicNow()), 1000)
    return () => {
      alive = false
      clearInterval(pollId)
      clearInterval(tickId)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen])

  // Closing cancels a window this dialog still holds open: its code exists only here.
  // Closed while the open request is in flight, the request cancels its window when it
  // answers (`open`).
  const close = async () => {
    if (isOpen) {
      setBusy(true)
      try {
        await cancelOwn(opened.opened_at)
      } catch (err) {
        toast.error(`The window could not be cancelled: ${err.message}`)
        setBusy(false)
        return
      }
      held.current = null
      setCode(null)
      onChanged?.()
    }
    dismissed.current = true
    onClose()
  }

  const left = isOpen ? windowLeft(opened, Math.max(mono, opened.arrivedAt ?? -Infinity)) : null

  return (
    <Modal title="Link a client" onClose={close} wide closeOnBackdrop={false}>
      {!opened && (
        <div className="text-sm text-slate-300">
          <p>
            Opening a link window gives you a one-time <strong>link code</strong> and lets one
            client link to this server in the next 10 minutes. Open it before you enter the
            server’s URL in the client: some clients (ChatGPT, Gemini) register the moment the
            URL is entered, and are refused when no window is open.
          </p>
          {state?.open && (
            <p className="mt-2 text-amber-300">
              A link window is already open, from another tab or from before this page was
              reloaded. Its code is not shown again: opening a new window replaces it, and its
              code stops working.
            </p>
          )}
          {offerWrites ? (
            <label className="mt-3 flex items-start gap-2">
              <input
                type="checkbox"
                className="tlg-check mt-0.5"
                checked={canWrite}
                onChange={(e) => setCanWrite(e.target.checked)}
              />
              <span>
                Allow this client to make changes
                <span className="block text-xs text-slate-400">
                  Off: the client can only read. On: it can also add, change and delete hosts and
                  reserved ranges, and ask for scans.
                </span>
              </span>
            </label>
          ) : (
            <p className="mt-2 text-xs text-slate-400">
              The link will be read-only: this server has remote writes off
              (<code>MCP_REMOTE_WRITES</code>).
            </p>
          )}
          <div className="mt-4 flex justify-end gap-2">
            <button onClick={close} className={secondaryBtn}>
              Cancel
            </button>
            <button onClick={open} disabled={busy} className={primaryBtn}>
              {state?.open ? 'Open a new window' : 'Open link window'}
            </button>
          </div>
        </div>
      )}

      {isOpen && (
        <div className="text-sm text-slate-300">
          <p className="text-xs text-slate-400">Link code</p>
          <p
            className="mt-1 select-all font-mono text-2xl tracking-widest text-slate-100"
            data-testid="link-code"
          >
            {groupedCode(code)}
          </p>
          <p className="mt-1 text-xs text-slate-400">
            Shown only here. Closing this dialog cancels the window. This link will be{' '}
            {opened.can_write ? 'able to make changes' : 'read-only'}.
          </p>

          <ol className="mt-4 list-decimal space-y-2 pl-5">
            <li>
              In the client, add a custom connector (an MCP server) with this URL:
              {opened.mcp_url && <CopyBlock label="Server URL" text={opened.mcp_url} />}
            </li>
            <li>
              Connect. The client sends your browser to this server’s consent page: check that the
              host it shows is the client’s, then type the code above.
            </li>
            <li>The window closes by itself once the client is linked.</li>
          </ol>

          <p className="mt-4" role="timer" aria-live="off">
            {left > 0 ? (
              <>
                Closes in <span className="font-mono">{countdown(left)}</span>
              </>
            ) : (
              'Closing…'
            )}
          </p>
          {opened.wrong_codes > 0 && (
            <p className="mt-2 text-amber-300">
              {opened.wrong_codes === 1
                ? '1 wrong code has been typed'
                : `${opened.wrong_codes} wrong codes have been typed`}{' '}
              on this window’s consent pages. A wrong code ends only that request, never the
              window. If you did not type it, someone else is trying: you can cancel the window
              and open a new one when you are ready.
            </p>
          )}
          {pollError && (
            <p className="mt-2 text-red-400">Could not check the window: {pollError}</p>
          )}
          <div className="mt-4 flex justify-end">
            <button onClick={close} disabled={busy} className={secondaryBtn}>
              Cancel link
            </button>
          </div>
        </div>
      )}

      {ended && (
        <div className="text-sm text-slate-300">
          <p className={ended === 'linked' ? 'text-emerald-300' : ''}>{ENDED_TEXT[ended]}</p>
          <div className="mt-4 flex justify-end">
            <button onClick={onClose} className={primaryBtn}>
              Done
            </button>
          </div>
        </div>
      )}
    </Modal>
  )
}
