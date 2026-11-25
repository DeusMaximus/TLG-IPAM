import { useState } from 'react'
import { createAgent } from '../api/ipam'
import { parseServerTime } from '../utils/time'
import EnrolmentSteps, { PlainHttpBeforeIssue, serverUrlFor } from './EnrolmentSteps'
import Modal, { inputCls, labelCls, primaryBtn, secondaryBtn } from './Modal'

const NAME_MAX = 74

// Two steps: name the agent, then the guided install (spec §5.6): its enrolment token,
// shown once, and the Container and systemd ways to run the agent with it. The token
// lives only in this component's state: onCreated tells the page to reload its list
// (whose responses never carry a token), and closing unmounts the dialog, so nothing
// on the page can show the token again. While the create request is in flight no path
// closes the dialog either: the agent may already exist, and its response is the only
// place its token will ever appear. On a page served over plain http across a network,
// the form says before the agent is created that its token will cross in clear, and
// still creates it (spec §5.6).
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
    return (
      <Modal title={`Agent ${created.name} created`} onClose={onClose} maxWidth="max-w-3xl" closeOnBackdrop={false}>
        <EnrolmentSteps
          token={created.token}
          expires={created.expires}
          serverVersion={serverVersion}
          location={location}
        />
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
        {!serverUrlFor(location).secure && <PlainHttpBeforeIssue />}
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
