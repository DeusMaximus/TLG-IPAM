import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Ban, Plus, Power, PowerOff, Trash2 } from 'lucide-react'
import {
  deleteAgent,
  getHealth,
  listAgents,
  listScanTargets,
  revokeAgentTokens,
  updateAgent,
} from '../api/ipam'
import AddAgentModal from '../components/AddAgentModal'
import EmptyState from '../components/EmptyState'
import Modal, { dangerBtn, primaryBtn, secondaryBtn } from '../components/Modal'
import { useToast, useTree } from '../context'
import { useFetch } from '../hooks/useFetch'
import {
  activeToken,
  agentStatus,
  relativeTime,
  STATUS_LABELS,
} from '../utils/agents'
import { parseServerTime } from '../utils/time'

// How often the list and the status dots refresh while the page is open
const REFRESH_MS = 30 * 1000

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

function AgentRow({ agent, targets, subnets, now, onToggle, onRevoke, onDelete }) {
  const token = activeToken(agent)
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
        {agent.last_error && (
          <div className="mt-1 max-w-64 text-red-400">Last check-in refused: {agent.last_error}</div>
        )}
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 font-mono text-slate-300">{agent.version || '—'}</td>
      <td className="px-3 py-2.5">
        <Interfaces interfaces={agent.interfaces} />
      </td>
      <td className="px-3 py-2.5">
        <Covered targets={targets} subnets={subnets} />
      </td>
      <td className="px-3 py-2.5">
        {token ? (
          <code className="text-slate-400">{token.token_prefix}…</code>
        ) : (
          <span className="text-amber-400">revoked</span>
        )}
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
            disabled={!token}
            className="flex items-center gap-1 rounded px-2 py-1 text-amber-400 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
            title={token ? 'Revoke its token' : 'It has no token to revoke'}
          >
            <Ban size={12} /> Revoke
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
  const agents = useFetch(listAgents, [])
  const targets = useFetch(listScanTargets, [])
  const health = useFetch(getHealth, [])
  const [now, setNow] = useState(() => Date.now())
  const [adding, setAdding] = useState(false)
  const [confirming, setConfirming] = useState(null)

  const { refresh: refreshAgents } = agents
  const { refresh: refreshTargets } = targets

  useEffect(() => {
    const id = setInterval(() => {
      setNow(Date.now())
      refreshAgents()
      refreshTargets()
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [refreshAgents, refreshTargets])

  const reload = () => {
    setNow(Date.now())
    refreshAgents()
    refreshTargets()
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
            No agents yet. Add one, then run the container command it gives you on a host in the
            VLAN to be scanned.
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
                    onToggle={toggle}
                    onRevoke={(a) => setConfirming({ kind: 'revoke', agent: a })}
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
            The agent can no longer check in. It keeps its name and the subnets it covers, but a
            revoked token cannot be reissued: to run it again, delete it and add a new agent.
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
