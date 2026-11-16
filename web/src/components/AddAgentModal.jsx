import { useRef, useState } from 'react'
import { Check, Copy, TriangleAlert } from 'lucide-react'
import { createAgent } from '../api/ipam'
import { containerCommand } from '../utils/agents'
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

// Two steps: name the agent, then show its token once. The token lives only in
// this component's state: onCreated tells the page to reload its list (whose
// responses never carry a token), and closing unmounts the dialog, so nothing on
// the page can show the token again. While the create request is in flight no
// path closes the dialog either: the agent may already exist, and its response
// is the only place its token will ever appear.
export default function AddAgentModal({ serverVersion, onClose, onCreated }) {
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
      setCreated({ name: res.data.name, token: res.data.token })
      onCreated()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  if (created) {
    const command = containerCommand({
      url: window.location.origin,
      token: created.token,
      version: serverVersion,
    })
    return (
      <Modal title={`Agent ${created.name} created`} onClose={onClose} maxWidth="max-w-3xl" closeOnBackdrop={false}>
        <div className="flex gap-2 rounded-md border border-amber-800 bg-amber-950/60 px-3 py-2 text-xs text-amber-200">
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          <span>
            This is the only time the token is shown. Copy it or the command now: once this
            dialog closes it cannot be shown again, and a lost token means deleting the agent
            and adding a new one.
          </span>
        </div>
        <CopyBlock label="Agent token" text={created.token} testId="agent-token" />
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
          Up to {NAME_MAX} characters, unique. The agent&apos;s token is shown once, on the next
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
