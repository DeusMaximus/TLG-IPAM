import { useRef, useState } from 'react'
import { Check, Copy, TriangleAlert } from 'lucide-react'
import { createAgent } from '../api/ipam'
import {
  agentServerUrl,
  containerCommand,
  enrolCommand,
  SERVER_URL_PLACEHOLDER,
} from '../utils/agents'
import { parseServerTime } from '../utils/time'
import Modal, { inputCls, labelCls, primaryBtn, secondaryBtn } from './Modal'

const NAME_MAX = 74

// Copy with the Clipboard API where the page is a secure context; on plain http
// (common on a LAN) fall back to selecting the text and execCommand('copy').
async function copyText(text, element) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to the selection fallback */
  }
  if (!element) return false
  const range = document.createRange()
  range.selectNodeContents(element)
  const selection = window.getSelection()
  selection.removeAllRanges()
  selection.addRange(range)
  try {
    return document.execCommand('copy')
  } catch {
    return false
  }
}

function CopyBlock({ label, text, testId }) {
  const ref = useRef(null)
  const [state, setState] = useState(null)

  const copy = async () => {
    setState((await copyText(text, ref.current)) ? 'copied' : 'failed')
  }

  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs font-medium text-slate-400">{label}</span>
        <button
          type="button"
          onClick={copy}
          className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200"
          aria-label={`Copy ${label.toLowerCase()}`}
        >
          {state === 'copied' ? <Check size={12} /> : <Copy size={12} />}
          {state === 'copied' ? 'Copied' : state === 'failed' ? 'Select and copy' : 'Copy'}
        </button>
      </div>
      <pre
        ref={ref}
        data-testid={testId}
        className="select-all overflow-x-auto whitespace-pre rounded-md border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-200"
      >
        {text}
      </pre>
    </div>
  )
}

// Two steps: name the agent, then show its enrolment token once. The token lives
// only in this component's state: onCreated tells the page to reload its list
// (whose responses never carry a token), and closing unmounts the dialog, so
// nothing on the page can show the token again. While the create request is in
// flight no path closes the dialog either: the agent may already exist, and its
// response is the only place its token will ever appear.
//
// Until the agent exchanges the enrolment token itself (#22) and this dialog
// becomes the guided install (#23), the exchange is shown as a command to run by
// hand, and the container command takes the agent token that command prints.
// `location` is the page's own (`window.location`); a test passes another.
export default function AddAgentModal({
  serverVersion,
  onClose,
  onCreated,
  location = window.location,
}) {
  const [name, setName] = useState('')
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [created, setCreated] = useState(null)

  const submit = async (e) => {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return setError('Name the agent.')
    if (trimmed.length > NAME_MAX) return setError(`At most ${NAME_MAX} characters.`)
    setSaving(true)
    setError(null)
    try {
      const res = await createAgent({ name: trimmed })
      setCreated({
        name: res.data.name,
        token: res.data.enrol_token,
        expires: parseServerTime(res.data.enrol_token_expires_at),
      })
      onCreated()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  if (created) {
    // never a plain-http address the tokens would cross a network over in clear
    const secure = agentServerUrl(location)
    const url = secure ?? SERVER_URL_PLACEHOLDER
    const exchange = enrolCommand({ url, token: created.token })
    const command = containerCommand({ url, version: serverVersion })
    return (
      <Modal title={`Agent ${created.name} created`} onClose={onClose} maxWidth="max-w-3xl" closeOnBackdrop={false}>
        <div className="flex gap-2 rounded-md border border-amber-800 bg-amber-950/60 px-3 py-2 text-xs text-amber-200">
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          <span>
            This is the only time the enrolment token is shown. It can be exchanged once, until{' '}
            {created.expires ? created.expires.toLocaleString() : 'an hour from now'}. Copy it or
            the command now: once this dialog closes it cannot be shown again. If it is lost or
            runs out, delete the agent and add it again.
          </span>
        </div>
        <CopyBlock label="Enrolment token" text={created.token} testId="agent-token" />
        {!secure && (
          <p className="mt-3 text-xs text-amber-200" data-testid="agent-needs-https">
            This page is served over plain HTTP, which would carry both tokens across the
            network in clear. The commands below use <code>{SERVER_URL_PLACEHOLDER}</code>: put
            the server&apos;s HTTPS address in its place. The agent refuses a plain-HTTP server
            address anyway, unless it is on the agent&apos;s own host.
          </p>
        )}
        <p className="mt-3 text-xs text-slate-400">
          1. On the agent&apos;s host, exchange it for the agent token. The answer carries it as{' '}
          <code>agent_token</code> (<code>tlga_…</code>); it is shown nowhere else.
        </p>
        <CopyBlock label="Enrolment command" text={exchange} testId="agent-enrol-command" />
        <p className="mt-3 text-xs text-slate-400">
          2. Run the agent with that agent token in place of <code>&lt;agent token&gt;</code>.
        </p>
        <CopyBlock label="Container command" text={command} testId="agent-command" />
        <p className="mt-3 text-xs text-slate-400">
          Run it on a Linux host on the VLAN to be scanned, with Docker or Podman running as
          root: the agent needs host networking and <code>CAP_NET_RAW</code>, which rootless
          runtimes cannot grant. Add <code>-e ALLOWED_CIDRS=…</code> to narrow what it may scan
          (default: the private and CGNAT ranges) or <code>-e MAX_PPS=…</code> to change its
          rate limit (default 200 packets/s).
        </p>
        <div className="mt-5 flex justify-end">
          <button onClick={onClose} className={primaryBtn}>
            Done, I have saved the token
          </button>
        </div>
      </Modal>
    )
  }

  const closeUnlessSaving = () => {
    if (!saving) onClose()
  }

  return (
    <Modal title="Add agent" onClose={closeUnlessSaving} closeOnBackdrop={!saving}>
      <form onSubmit={submit}>
        <label className={labelCls} htmlFor="agent-name">
          Name
        </label>
        <input
          id="agent-name"
          autoFocus
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. lab-vlan20"
          className={inputCls}
        />
        <p className="mt-1 text-xs text-slate-500">
          Up to {NAME_MAX} characters, unique. Its enrolment token is shown once, on the next
          step.
        </p>
        {error && (
          <p role="alert" className="mt-2 text-xs text-red-400">
            {error}
          </p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={closeUnlessSaving}
            disabled={saving}
            className={`${secondaryBtn} disabled:opacity-50`}
          >
            Cancel
          </button>
          <button type="submit" disabled={saving} className={primaryBtn}>
            {saving ? 'Creating…' : 'Create agent'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
