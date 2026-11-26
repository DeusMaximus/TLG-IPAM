import { useEffect, useState } from 'react'
import { Ban, Link2 } from 'lucide-react'
import { cancelLinkWindow, getLinkWindow, listLinkedClients, revokeLinkedClient } from '../api/ipam'
import EmptyState from '../components/EmptyState'
import LinkClientModal from '../components/LinkClientModal'
import Modal, { dangerBtn, primaryBtn, secondaryBtn } from '../components/Modal'
import { useToast } from '../context'
import { useFetch } from '../hooks/useFetch'
import { relativeTime } from '../utils/agents'
import { access, liveLinks, nameText, REFRESH_MS, revokedLinks, shortId } from '../utils/links'
import { formatServerTime } from '../utils/time'

// The name the client gave itself: its own claim, which this server does not check. It is
// quoted and set in small, plain, muted text under the host, never bold and never where
// the page's own words go, so it does not read as the server vouching for the client.
function ClientName({ link }) {
  const missing = nameText(link)
  return (
    <span
      className="block text-xs text-slate-500"
      title={missing ? undefined : "The client's own name for itself. This server does not check it."}
    >
      {missing ?? (
        <>
          calls itself <span className="italic text-slate-400">“{link.client_name}”</span>
        </>
      )}
      <span className="ml-2 font-mono text-slate-600">link {shortId(link.id)}</span>
    </span>
  )
}

function Host({ link }) {
  return <span className="block font-mono text-sm text-slate-100">{link.redirect_host}</span>
}

function When({ value, now }) {
  return <span title={formatServerTime(value)}>{relativeTime(value, now)}</span>
}

function LinkRow({ link, now, onRevoke }) {
  const { label, note } = access(link)
  return (
    <tr className="border-t border-slate-700/60 align-top">
      <td className="px-3 py-2">
        <Host link={link} />
        <ClientName link={link} />
      </td>
      <td className="px-3 py-2">
        {label}
        {note && <span className="block max-w-xs text-xs text-amber-300/80">{note}</span>}
      </td>
      <td className="px-3 py-2">
        <When value={link.created_at} now={now} />
      </td>
      <td className="px-3 py-2">
        <When value={link.last_used_at} now={now} />
      </td>
      <td className="px-3 py-2 text-right">
        <button
          onClick={() => onRevoke(link)}
          className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-red-300 hover:bg-red-900/40"
          aria-label={`Revoke the link to ${link.redirect_host}`}
        >
          <Ban size={12} /> Revoke
        </button>
      </td>
    </tr>
  )
}

export default function LinkedClients() {
  const toast = useToast()
  const links = useFetch(listLinkedClients, [])
  const windowState = useFetch(getLinkWindow, [])
  const [now, setNow] = useState(() => Date.now())
  const [linking, setLinking] = useState(false)
  const [revoking, setRevoking] = useState(null)
  const [busy, setBusy] = useState(false)

  const { refresh: refreshLinks } = links
  const { refresh: refreshWindow } = windowState
  const reload = () => {
    setNow(Date.now())
    refreshLinks()
    refreshWindow()
  }

  useEffect(() => {
    const id = setInterval(() => {
      setNow(Date.now())
      refreshLinks()
      refreshWindow()
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [refreshLinks, refreshWindow])

  const state = windowState.data?.data
  const list = links.data?.data
  const live = liveLinks(list)
  const revoked = revokedLinks(list)
  const configured = state?.configured

  const revoke = async () => {
    setBusy(true)
    try {
      await revokeLinkedClient(revoking.id)
      toast.success(`Link to ${revoking.redirect_host} revoked`)
      setRevoking(null)
    } catch (err) {
      toast.error(err.message)
      // a link the server had already revoked (the reuse rule) is shown as such
      if (err.code === 'ALREADY_REVOKED') setRevoking(null)
    } finally {
      setBusy(false)
      reload()
    }
  }

  const cancelOpen = async () => {
    try {
      await cancelLinkWindow()
      toast.success('Link window cancelled')
    } catch (err) {
      toast.error(err.message)
    } finally {
      reload()
    }
  }

  return (
    <div className="max-w-4xl">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-100">Linked clients</h1>
        <button
          onClick={() => setLinking(true)}
          disabled={!configured || linking}
          className={primaryBtn}
          title={configured === false ? 'Remote MCP is not configured' : undefined}
        >
          <span className="flex items-center gap-1.5">
            <Link2 size={14} /> Link a client
          </span>
        </button>
      </div>
      <p className="mt-1 text-sm text-slate-400">
        Web assistants (Claude.ai, ChatGPT, Gemini, Mistral) linked to this server’s MCP
        endpoint. Each link is one approval you gave with a link code; revoking it stops the
        client at its next request.
      </p>

      {windowState.error && !state && (
        <p className="mt-4 text-sm text-red-400">
          Could not load the link window: {windowState.error.message}
        </p>
      )}

      {configured === false && (
        <div className="mt-4 rounded-lg border border-slate-700 bg-slate-800 px-4 py-3 text-sm text-slate-300">
          <p className="font-medium text-slate-200">Remote MCP is not configured.</p>
          <p className="mt-1 text-slate-400">
            Linking a web assistant needs <code>PUBLIC_BASE_URL</code> set to the public origin
            this server is reached at (for example <code>https://ipam.example.com</code>), then a
            restart. Until then <code>/mcp</code> takes the API key alone, as a local client such
            as Claude Desktop sends it, and no client can link.
          </p>
        </div>
      )}

      {configured && !state.remote_writes && (
        <p className="mt-3 text-xs text-slate-400">
          This server has remote writes off (<code>MCP_REMOTE_WRITES</code>): every linked client
          can only read.
        </p>
      )}

      {configured && state.open && !linking && (
        <div className="mt-4 flex items-center justify-between gap-3 rounded-lg border border-amber-700/60 bg-amber-950/30 px-4 py-3 text-sm text-amber-200">
          <span>
            A link window is open, from another tab or from before this page was reloaded. Its
            code is shown only in the dialog that opened it. Cancel it, or open a new one, which
            replaces it.
          </span>
          <button onClick={cancelOpen} className={secondaryBtn}>
            Cancel window
          </button>
        </div>
      )}

      <div className="mt-5">
        {links.error && !list && (
          <p className="text-sm text-red-400">Could not load linked clients: {links.error.message}</p>
        )}
        {list && live.length === 0 && (
          <EmptyState>
            No linked clients.{' '}
            {configured ? 'Link one with “Link a client”.' : ''}
          </EmptyState>
        )}
        {live.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-slate-700 bg-slate-800">
            <table className="w-full text-left text-xs" aria-label="Linked clients">
              <thead className="text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">Redirect host</th>
                  <th className="px-3 py-2 font-medium">Access</th>
                  <th className="px-3 py-2 font-medium">Linked</th>
                  <th className="px-3 py-2 font-medium">Last contact</th>
                  <th className="px-3 py-2">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="text-slate-300">
                {live.map((link) => (
                  <LinkRow key={link.id} link={link} now={now} onRevoke={setRevoking} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {list && (live.length > 0 || revoked.length > 0) && (
          <p className="mt-2 text-xs text-slate-500">
            The redirect host is where the link’s codes were sent when you approved it.{' '}
            <strong className="font-medium text-slate-400">Last contact</strong> is the client’s
            last request to this server (a call to <code>/mcp</code> or a token refresh). Clients
            make these on their own, so it is not when you last used one.
          </p>
        )}

        {revoked.length > 0 && (
          <div className="mt-6">
            <h2 className="text-sm font-semibold text-slate-300">Recently revoked</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              The newest revoked links, by you or by the server (a refresh token used again after
              it was replaced). The change log has every revoke.
            </p>
            <ul className="mt-2 divide-y divide-slate-800 rounded-lg border border-slate-800">
              {revoked.map((link) => (
                <li key={link.id} className="flex items-start justify-between gap-3 px-3 py-2 text-xs">
                  <span>
                    <span className="block font-mono text-sm text-slate-400">{link.redirect_host}</span>
                    <ClientName link={link} />
                  </span>
                  <span className="text-slate-500">
                    revoked <When value={link.revoked_at} now={now} />
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {linking && (
        <LinkClientModal
          state={state}
          onClose={() => {
            setLinking(false)
            reload()
          }}
          onChanged={reload}
        />
      )}

      {revoking && (
        <Modal title={`Revoke the link to ${revoking.redirect_host}?`} onClose={() => setRevoking(null)}>
          <div className="text-sm text-slate-300">
            <p>
              Its tokens stop working now: the client’s next request is refused. To use it again,
              link it again with a new link code. This cannot be undone.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setRevoking(null)} className={secondaryBtn}>
                Cancel
              </button>
              <button onClick={revoke} disabled={busy} className={dangerBtn}>
                Revoke link
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}
