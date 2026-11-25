import { useState } from 'react'
import { reEnrolAgent } from '../api/ipam'
import { parseServerTime } from '../utils/time'
import EnrolmentSteps, { PlainHttpBeforeIssue, serverUrlFor } from './EnrolmentSteps'
import Modal, { primaryBtn, secondaryBtn } from './Modal'

// Re-enrol (spec §5.6): a new enrolment token for an existing agent, which keeps its
// identity and scan targets. A confirmation first, since it revokes an enrolment token
// the agent still holds unexchanged; then the guided install with the new token, and
// the commands that remove the old agent token from a host that still holds it. As in
// AddAgentModal, the token lives only in this component's state, and no path closes the
// dialog while the request is in flight or (by a stray backdrop click) once it answers,
// and on a page served over plain http across a network the confirmation says the new
// token will cross in clear, as Add agent's form does.
export default function ReEnrolModal({
  agent,
  serverVersion,
  onClose,
  onIssued,
  location = window.location,
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [issued, setIssued] = useState(null)

  const issue = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await reEnrolAgent(agent.id)
      setIssued({
        token: res.data.enrol_token,
        expires: parseServerTime(res.data.enrol_token_expires_at),
      })
      onIssued()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (issued) {
    return (
      <Modal title={`Re-enrol ${agent.name}`} onClose={onClose} maxWidth="max-w-3xl" closeOnBackdrop={false}>
        <EnrolmentSteps
          token={issued.token}
          expires={issued.expires}
          serverVersion={serverVersion}
          location={location}
          reEnrol
        />
        <div className="mt-5 flex justify-end">
          <button onClick={onClose} className={primaryBtn}>
            Done, I have saved the token
          </button>
        </div>
      </Modal>
    )
  }

  const closeUnlessBusy = () => {
    if (!busy) onClose()
  }

  return (
    <Modal title={`Re-enrol ${agent.name}?`} onClose={closeUnlessBusy} closeOnBackdrop={!busy}>
      <div className="space-y-2 text-sm text-slate-300">
        <p>
          This issues a new enrolment token for {agent.name}, valid for an hour, and shows how to
          run the agent with it. It keeps its name and the subnets it covers.
        </p>
        <p>
          Exchanging the new token revokes the agent token it holds now; until then the agent keeps
          running. An enrolment token it has not exchanged yet is revoked now.
        </p>
      </div>
      {!serverUrlFor(location).secure && <PlainHttpBeforeIssue />}
      {error && (
        <p role="alert" className="mt-2 text-xs text-red-400">
          {error}
        </p>
      )}
      <div className="mt-5 flex justify-end gap-2">
        <button
          type="button"
          onClick={closeUnlessBusy}
          disabled={busy}
          className={`${secondaryBtn} disabled:opacity-50`}
        >
          Cancel
        </button>
        <button type="button" onClick={issue} disabled={busy} className={primaryBtn}>
          {busy ? 'Issuing…' : 'Issue a new enrolment token'}
        </button>
      </div>
    </Modal>
  )
}
