import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpCircle, Ban, KeyRound, Plus, Power, PowerOff, Radar, Trash2 } from 'lucide-react'
import {
  createScanTarget,
  deleteAgent,
  getHealth,
  listAgents,
  listScanSuggestions,
  listScanTargets,
  requestScan,
  revokeAgentTokens,
  updateAgent,
} from '../api/ipam'
import AddAgentModal from '../components/AddAgentModal'
import EmptyState from '../components/EmptyState'
import Modal, { dangerBtn, primaryBtn, secondaryBtn } from '../components/Modal'
import ReEnrolModal from '../components/ReEnrolModal'
import UpdateModal from '../components/UpdateModal'
import { useToast, useTree } from '../context'
import { useFetch } from '../hooks/useFetch'
import {
  activeToken,
  agentStatus,
  blockedEnrolment,
  liveEnrolment,
  openEnrolment,
  pendingEnrolments,
  received,
  relativeTime,
  sinceReceived,
  STATUS_LABELS,
  timeLeft,
} from '../utils/agents'
import { NEW_TARGET, SCAN_METHODS } from '../utils/scans'
import { parseServerTime } from '../utils/time'

// How often the list and the status dots refresh while the page is open
const REFRESH_MS = 30 * 1000

// The agents list with the browser's time it arrived at, which an enrolment token's
// time left counts from (`agents.enrolmentLeft`)
const loadAgents = () => listAgents().then((answer) => received(answer))

const DOT = {
  online: 'bg-emerald-500',
  late: 'bg-amber-500',
  offline: 'bg-red-500',
  never: 'border border-slate-500 bg-transparent',
  // the last check-in is dated ahead of this browser's clock: how fresh it is is not known
  ahead: 'border border-amber-500 bg-transparent',
  disabled: 'bg-slate-600',
}

function StatusDot({ agent, now }) {
  const status = agentStatus(agent, now)
  const last = parseServerTime(agent.last_checkin_at)
  const title = last
    ? `${STATUS_LABELS[status]} · last check-in ${last.toLocaleString()}`
    : STATUS_LABELS[status]
  return (
    <span className="flex items-center gap-2" title={title}>
      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${DOT[status]}`} aria-hidden="true" />
      <span className="whitespace-nowrap text-slate-300">{STATUS_LABELS[status]}</span>
    </span>
  )
}

function Interfaces({ interfaces }) {
  if (!interfaces?.length) return <span className="text-slate-600">—</span>
  return (
    <ul className="space-y-0.5">
      {interfaces.map((iface, i) => (
        <li key={`${iface.name}-${i}`} className="whitespace-nowrap font-mono">
          <span className="text-slate-400">{iface.name}</span>{' '}
          <span className="text-slate-200">{iface.cidr}</span>
          {iface.mac && <span className="ml-1 text-slate-500">{iface.mac}</span>}
        </li>
      ))}
    </ul>
  )
}

function Covered({ targets, subnets }) {
  // null: the scan targets did not load, so which subnets it covers is unknown
  if (!targets) return <span className="text-slate-500">unknown</span>
  if (!targets.length) return <span className="text-slate-600">none</span>
  return (
    <ul className="space-y-0.5">
      {targets.map((t) => {
        const subnet = subnets.find((s) => s.id === t.subnet_id)
        return (
          <li key={t.id} className="whitespace-nowrap">
            <Link to={`/subnets/${t.subnet_id}`} className="text-blue-400 hover:text-blue-300">
              {subnet ? subnet.name : t.subnet_id}
            </Link>
            {subnet && <span className="ml-1 font-mono text-slate-500">{subnet.cidr}</span>}
            {!t.enabled && <span className="ml-1 text-slate-500">(paused)</span>}
          </li>
        )
      })}
    </ul>
  )
}

// The version the agent last reported, and the server's two flags (`update_available`,
// `protocol_mismatch`, computed on read by `agents.agent_out`): either offers the
// update command
function Version({ agent, onUpdate }) {
  const flagged = agent.update_available || agent.protocol_mismatch
  return (
    <div>
      <span className="font-mono text-slate-300">{agent.version || '—'}</span>
      {agent.update_available && (
        <span
          className="ml-1.5 rounded bg-amber-900/60 px-1.5 py-0.5 text-[11px] text-amber-200"
          data-testid="update-available"
        >
          update available
        </span>
      )}
      {flagged && (
        <button
          onClick={() => onUpdate(agent)}
          className="mt-1 flex items-center gap-1 text-blue-400 hover:text-blue-300"
        >
          <ArrowUpCircle size={12} /> Update command
        </button>
      )}
    </div>
  )
}

function TokenCell({ agent, elapsed }) {
  const token = activeToken(agent)
  const enrolment = openEnrolment(agent, elapsed)
  const blocked = blockedEnrolment(agent, elapsed)
  const enrolled = (agent.tokens || []).some((t) => t.kind === 'agent')
  return (
    <div>
      {token ? (
        <code className="text-slate-400">{token.token_prefix}…</code>
      ) : !enrolled ? (
        <span className="text-slate-400" title="It has never exchanged an enrolment token">
          not enrolled
        </span>
      ) : (
        <span className="text-amber-400">revoked</span>
      )}
      {enrolment && (
        <div className="mt-0.5 whitespace-nowrap text-slate-500">
          enrolment open, expires {timeLeft(enrolment, elapsed) ?? 'soon'}
        </div>
      )}
      {blocked && (
        <div
          className="mt-0.5 max-w-56 text-slate-500"
          title="The server refuses a disabled agent's enrolment token until the agent is enabled"
        >
          enrolment blocked until it is enabled, expires {timeLeft(blocked, elapsed) ?? 'soon'}
        </div>
      )}
    </div>
  )
}

function AgentRow({
  agent,
  targets,
  subnets,
  now,
  elapsed,
  onToggle,
  onRevoke,
  onReEnrol,
  onUpdate,
  onDelete,
}) {
  const token = activeToken(agent)
  // a disabled agent's live enrolment token can still be revoked
  const enrolment = token ? null : liveEnrolment(agent, elapsed)
  const revocable = token || enrolment
  const disabled = agent.state === 'disabled'
  return (
    <tr className="border-t border-slate-700/60 align-top" data-testid={`agent-${agent.id}`}>
      <td className="px-3 py-2.5">
        <StatusDot agent={agent} now={now} />
        <div className="mt-0.5 max-w-56 pl-[18px] text-slate-500" data-testid="last-checkin">
          {relativeTime(agent.last_checkin_at, now)}
        </div>
      </td>
      <td className="px-3 py-2.5">
        <div className="font-medium text-slate-100">{agent.name}</div>
        {agent.hostname && (
          <div className="max-w-48 truncate text-slate-400" title={agent.hostname}>
            {agent.hostname}
          </div>
        )}
        {agent.platform && (
          <div className="max-w-48 truncate text-slate-500" title={agent.platform}>
            {agent.platform}
          </div>
        )}
        {agent.protocol_mismatch && (
          <div className="mt-1 max-w-64 font-medium text-red-400" data-testid="protocol-mismatch">
            Protocol mismatch: it is given no scans until it runs the release that matches the
            server.
          </div>
        )}
        {agent.last_error && (
          <div className="mt-1 max-w-64 text-red-400">Last check-in refused: {agent.last_error}</div>
        )}
      </td>
      <td className="whitespace-nowrap px-3 py-2.5">
        <Version agent={agent} onUpdate={onUpdate} />
      </td>
      <td className="px-3 py-2.5">
        <Interfaces interfaces={agent.interfaces} />
      </td>
      <td className="px-3 py-2.5">
        <Covered targets={targets} subnets={subnets} />
      </td>
      <td className="px-3 py-2.5">
        <TokenCell agent={agent} elapsed={elapsed} />
      </td>
      <td className="px-3 py-2.5">
        <div className="flex justify-end gap-1">
          <button
            onClick={() => onToggle(agent)}
            className="flex items-center gap-1 rounded px-2 py-1 text-slate-300 hover:bg-slate-700"
            title={disabled ? 'Accept its token again' : 'Refuse its token without revoking it'}
          >
            {disabled ? <Power size={12} /> : <PowerOff size={12} />}
            {disabled ? 'Enable' : 'Disable'}
          </button>
          <button
            onClick={() => onRevoke(agent)}
            disabled={!revocable}
            className="flex items-center gap-1 rounded px-2 py-1 text-amber-400 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
            title={
              token
                ? 'Revoke its token'
                : enrolment
                  ? 'Revoke its enrolment token'
                  : 'It has no token to revoke'
            }
          >
            <Ban size={12} /> Revoke
          </button>
          <button
            onClick={() => onReEnrol(agent)}
            disabled={disabled}
            className="flex items-center gap-1 rounded px-2 py-1 text-slate-300 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
            title={
              disabled
                ? 'Enable it first: a disabled agent cannot be re-enrolled'
                : 'Issue a new enrolment token for it'
            }
          >
            <KeyRound size={12} /> Re-enrol
          </button>
          <button
            onClick={() => onDelete(agent)}
            className="flex items-center gap-1 rounded px-2 py-1 text-red-400 hover:bg-slate-700"
          >
            <Trash2 size={12} /> Delete
          </button>
        </div>
      </td>
    </tr>
  )
}

// Every enrolment token still open (spec §5.8), listed until it is used or expires by
// the server's clock (`expires_in_s`), never the browser's; a disabled agent's is not
// open. The list and the time left refresh with the page.
function PendingEnrolments({ agents, elapsed }) {
  const pending = pendingEnrolments(agents, elapsed)
  if (!pending.length) return null
  return (
    <section className="mb-4" aria-label="Pending enrolments">
      <h2 className="text-sm font-medium text-slate-200">Pending enrolments</h2>
      <ul className="mt-2 space-y-1 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs">
        {pending.map(({ agent, token }) => (
          <li key={token.id} className="flex flex-wrap items-baseline gap-x-2" data-testid={`pending-${agent.id}`}>
            <span className="font-medium text-slate-100">{agent.name}</span>
            <code className="text-slate-400">{token.token_prefix}…</code>
            <span className="text-slate-400">
              {agent.state === 'pending' ? 'waiting for its first enrolment' : 'being re-enrolled'},
              expires {timeLeft(token, elapsed) ?? 'soon'}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

const methodLabels = (ids) =>
  SCAN_METHODS.filter((m) => ids.includes(m.id))
    .map((m) => m.label)
    .join(', ')

// "Scan <subnet> with this agent?" (spec §5.6, "Suggest, don't assume"): the subnets on
// an agent's link that have no scan target, as the server matches them
// (`scans.suggestions`). Nothing is scanned until the owner accepts one.
function Suggestions({ suggestions, busy, onAccept, onDismiss }) {
  if (!suggestions.length) return null
  return (
    <section className="mb-4" aria-label="Suggested scans">
      <h2 className="text-sm font-medium text-slate-200">Suggested scans</h2>
      <p className="mt-0.5 text-xs text-slate-400">
        Subnets on the link of an agent&apos;s interface that nothing scans yet. Scanning one sets
        it up with the default methods ({methodLabels(NEW_TARGET.methods)}), its DHCP pool left out
        and on request only, then asks for one scan; its subnet page changes the settings.
      </p>
      <ul className="mt-2 space-y-1.5">
        {suggestions.map((s) => {
          const key = `${s.agent_id}:${s.subnet_id}`
          return (
            <li
              key={key}
              data-testid={`suggestion-${key}`}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs"
            >
              <span className="text-slate-300">
                Scan <span className="font-medium text-slate-100">{s.subnet_name}</span>{' '}
                <span className="font-mono text-slate-500">{s.subnet_cidr}</span> with{' '}
                <span className="font-medium text-slate-100">{s.agent_name}</span>?
              </span>
              <span className="flex gap-1">
                <button
                  onClick={() => onAccept(s)}
                  disabled={busy === key}
                  className="flex items-center gap-1 rounded bg-blue-600 px-2 py-1 font-medium text-white hover:bg-blue-500 disabled:opacity-50"
                >
                  <Radar size={12} /> {busy === key ? 'Setting up…' : 'Scan'}
                </button>
                <button
                  onClick={() => onDismiss(s)}
                  className="rounded px-2 py-1 text-slate-400 hover:bg-slate-700"
                >
                  Not now
                </button>
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function Confirm({ title, children, action, onConfirm, onClose }) {
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try {
      await onConfirm()
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={title} onClose={onClose}>
      <div className="text-sm text-slate-300">{children}</div>
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onClose} className={secondaryBtn}>
          Cancel
        </button>
        <button onClick={run} disabled={busy} className={dangerBtn}>
          {action}
        </button>
      </div>
    </Modal>
  )
}

function deleteCoverage(covered) {
  if (!covered) {
    return 'Which subnets it covers could not be loaded: any it covers keep their scan settings and are left unassigned.'
  }
  if (!covered.length) return 'It covers no subnets.'
  return `The ${covered.length} subnet(s) it covers keep their scan settings and are left unassigned.`
}

export default function Agents() {
  const toast = useToast()
  const { subnets } = useTree()
  const agents = useFetch(loadAgents, [])
  const targets = useFetch(listScanTargets, [])
  const suggested = useFetch(listScanSuggestions, [])
  const health = useFetch(getHealth, [])
  const [now, setNow] = useState(() => Date.now())
  const [adding, setAdding] = useState(false)
  const [confirming, setConfirming] = useState(null)
  // offers set aside with "Not now", for as long as the page is open
  const [dismissed, setDismissed] = useState(() => new Set())
  const [accepting, setAccepting] = useState(null)

  const { refresh: refreshAgents } = agents
  const { refresh: refreshTargets } = targets
  const { refresh: refreshSuggested } = suggested

  useEffect(() => {
    const id = setInterval(() => {
      setNow(Date.now())
      refreshAgents()
      refreshTargets()
      refreshSuggested()
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [refreshAgents, refreshTargets, refreshSuggested])

  const reload = () => {
    setNow(Date.now())
    refreshAgents()
    refreshTargets()
    refreshSuggested()
  }

  const offers = (suggested.data?.data || []).filter(
    (s) => !dismissed.has(`${s.agent_id}:${s.subnet_id}`)
  )

  // One click: the scan target with the defaults, then one scan of it. Each is the
  // ordinary owner write, audit-logged by its own service.
  const accept = async (s) => {
    const key = `${s.agent_id}:${s.subnet_id}`
    setAccepting(key)
    try {
      await createScanTarget({ subnet_id: s.subnet_id, agent_id: s.agent_id })
    } catch (err) {
      toast.error(err.message)
      setAccepting(null)
      reload()
      return
    }
    try {
      await requestScan(s.subnet_id)
      toast.success(`${s.agent_name} will scan ${s.subnet_name} at its next check-in`)
    } catch (err) {
      toast.error(`Scanning of ${s.subnet_name} is set up, but the scan was not requested: ${err.message}`)
    } finally {
      setAccepting(null)
      reload()
    }
  }

  // Only a successful load says which subnets each agent covers. Until then, or
  // after a failed refresh, coverage is unknown: never "none".
  const targetsKnown = Boolean(targets.data) && !targets.error
  const targetsByAgent = useMemo(() => {
    const map = {}
    for (const t of targets.data?.data || []) {
      if (t.agent_id) (map[t.agent_id] ||= []).push(t)
    }
    return map
  }, [targets.data])
  const coveredBy = (agentId) => (targetsKnown ? targetsByAgent[agentId] || [] : null)

  const act = async (fn, message) => {
    try {
      await fn()
      toast.success(message)
      setConfirming(null)
      reload()
    } catch (err) {
      toast.error(err.message)
    }
  }

  const toggle = (agent) => {
    const enable = agent.state === 'disabled'
    act(
      () => updateAgent(agent.id, { enabled: enable }),
      `Agent ${agent.name} ${enable ? 'enabled' : 'disabled'}`
    )
  }

  const list = agents.data?.data
  const elapsed = sinceReceived(agents.data?.receivedAt, now)

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-100">Agents</h1>
        <button onClick={() => setAdding(true)} className={primaryBtn}>
          <span className="flex items-center gap-1.5">
            <Plus size={14} /> Add agent
          </span>
        </button>
      </div>
      <p className="mt-1 text-sm text-slate-400">
        Scan agents run on a host in each VLAN and check in every minute for scans to run.
      </p>

      <div className="mt-5">
        <Suggestions
          suggestions={offers}
          busy={accepting}
          onAccept={accept}
          onDismiss={(s) => setDismissed((d) => new Set(d).add(`${s.agent_id}:${s.subnet_id}`))}
        />
        {list && <PendingEnrolments agents={list} elapsed={elapsed} />}
        {agents.error && !list && (
          <p className="text-sm text-red-400">Could not load agents: {agents.error.message}</p>
        )}
        {targets.error && (
          <p className="mb-3 text-sm text-red-400">
            Could not load scan targets, so the subnets each agent covers are unknown:{' '}
            {targets.error.message}
          </p>
        )}
        {list && list.length === 0 && (
          <EmptyState>
            No agents yet. Add one, then run the command it gives you, in a container or as a
            systemd service, on a host in the VLAN to be scanned.
          </EmptyState>
        )}
        {list && list.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-slate-700 bg-slate-800">
            <table className="w-full text-left text-xs" aria-label="Agents">
              <thead className="text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Agent</th>
                  <th className="px-3 py-2 font-medium">Version</th>
                  <th className="px-3 py-2 font-medium">Interfaces</th>
                  <th className="px-3 py-2 font-medium">Subnets covered</th>
                  <th className="px-3 py-2 font-medium">Token</th>
                  <th className="px-3 py-2">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.map((agent) => (
                  <AgentRow
                    key={agent.id}
                    agent={agent}
                    targets={coveredBy(agent.id)}
                    subnets={subnets}
                    now={now}
                    elapsed={elapsed}
                    onToggle={toggle}
                    onRevoke={(a) => setConfirming({ kind: 'revoke', agent: a })}
                    onReEnrol={(a) => setConfirming({ kind: 're-enrol', agent: a })}
                    onUpdate={(a) => setConfirming({ kind: 'update', agent: a })}
                    onDelete={(a) => setConfirming({ kind: 'delete', agent: a })}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {adding && (
        <AddAgentModal
          serverVersion={health.data?.version}
          onClose={() => setAdding(false)}
          onCreated={reload}
        />
      )}

      {confirming?.kind === 're-enrol' && (
        <ReEnrolModal
          agent={confirming.agent}
          serverVersion={health.data?.version}
          onClose={() => setConfirming(null)}
          onIssued={reload}
        />
      )}

      {confirming?.kind === 'update' && (
        <UpdateModal
          agent={confirming.agent}
          serverVersion={health.data?.version}
          onClose={() => setConfirming(null)}
        />
      )}

      {confirming?.kind === 'revoke' && (
        <Confirm
          title={`Revoke the token of ${confirming.agent.name}?`}
          action="Revoke token"
          onClose={() => setConfirming(null)}
          onConfirm={() =>
            act(
              () => revokeAgentTokens(confirming.agent.id),
              `Token of ${confirming.agent.name} revoked`
            )
          }
        >
          <p>
            The agent can no longer check in. It keeps its name and the subnets it covers. A
            revoked token cannot be reissued: to run it again, re-enrol it for a new one.
          </p>
        </Confirm>
      )}

      {confirming?.kind === 'delete' && (
        <Confirm
          title={`Delete ${confirming.agent.name}?`}
          action="Delete agent"
          onClose={() => setConfirming(null)}
          onConfirm={() =>
            act(() => deleteAgent(confirming.agent.id), `Agent ${confirming.agent.name} deleted`)
          }
        >
          <p>
            Its token is revoked and the agent removed.{' '}
            {deleteCoverage(coveredBy(confirming.agent.id))}
          </p>
        </Confirm>
      )}
    </div>
  )
}
