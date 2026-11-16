import { useState } from 'react'
import { Radar } from 'lucide-react'
import {
  createScanTarget,
  listAgents,
  listScanTargets,
  requestScan,
  updateScanTarget,
} from '../api/ipam'
import { useToast } from '../context'
import { useFetch } from '../hooks/useFetch'
import { parseServerTime, relativeTime } from '../utils/agents'
import {
  formatDuration,
  hasDhcpPool,
  macUnavailable,
  SCAN_METHODS,
  scopeRangeText,
  settingsBody,
  settingsChanged,
  settingsForm,
  targetForSubnet,
} from '../utils/scans'
import { inputCls, labelCls, primaryBtn, secondaryBtn } from './Modal'

// Scope ranges listed before the rest are summarised as "+N more"
const SCOPE_SHOWN = 6

function When({ value }) {
  const date = parseServerTime(value)
  if (!date) return <span className="text-slate-500">never</span>
  return <span title={date.toLocaleString()}>{relativeTime(value)}</span>
}

function LastScan({ target }) {
  if (!target.last_scan_at) return <span className="text-slate-500">never</span>
  const duration = formatDuration(target.last_scan_duration_ms)
  return (
    <span>
      <When value={target.last_scan_at} />
      {duration && <span className="text-slate-400"> · took {duration}</span>}
      {target.last_scan_complete === false && (
        <span className="text-amber-400"> · incomplete</span>
      )}
    </span>
  )
}

function Scope({ scope }) {
  if (!scope?.length) return <span className="text-slate-500">no addresses</span>
  const shown = scope.slice(0, SCOPE_SHOWN)
  return (
    <span className="font-mono text-xs">
      {shown.map(scopeRangeText).join(', ')}
      {scope.length > SCOPE_SHOWN && (
        <span className="font-sans text-slate-500"> +{scope.length - SCOPE_SHOWN} more</span>
      )}
    </span>
  )
}

function Status({ target }) {
  const asked = parseServerTime(target.scan_requested_at)
  const last = parseServerTime(target.last_scan_at)
  const pending = asked && (!last || asked > last)
  return (
    <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
      <div>
        <dt className="text-[11px] uppercase tracking-wide text-slate-500">Last scan</dt>
        <dd className="mt-0.5 text-slate-200">
          <LastScan target={target} />
        </dd>
      </div>
      <div>
        <dt className="text-[11px] uppercase tracking-wide text-slate-500">Now</dt>
        <dd className="mt-0.5 text-slate-200">
          {target.current_job_id ? (
            <span>
              job issued <When value={target.job_issued_at} />
            </span>
          ) : pending ? (
            <span>
              scan requested <When value={target.scan_requested_at} />, waiting for the agent
            </span>
          ) : (
            <span className="text-slate-500">idle</span>
          )}
        </dd>
      </div>
      <div className="sm:col-span-2">
        <dt className="text-[11px] uppercase tracking-wide text-slate-500">Scanned addresses</dt>
        <dd className="mt-0.5 text-slate-200">
          <Scope scope={target.scope} />
        </dd>
      </div>
      {macUnavailable(target) && (
        <p className="text-xs text-amber-400 sm:col-span-2" role="status">
          MAC collection unavailable — agent is not on this subnet
        </p>
      )}
    </dl>
  )
}

function SettingsForm({ subnet, target, agents, agentsFailed, onSaved }) {
  const toast = useToast()
  const [form, setForm] = useState(() => settingsForm(target))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const pool = hasDhcpPool(subnet)
  const dirty = !target || settingsChanged(target, form)

  const set = (field, value) => setForm((f) => ({ ...f, [field]: value }))
  const toggleMethod = (id) =>
    setForm((f) => ({
      ...f,
      methods: f.methods.includes(id) ? f.methods.filter((m) => m !== id) : [...f.methods, id],
    }))

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const body = settingsBody(form)
    try {
      if (target) {
        await updateScanTarget(target.id, body)
        toast.success(`Scanning settings for ${subnet.name} saved`)
      } else {
        await createScanTarget({ ...body, subnet_id: subnet.id })
        toast.success(`Scanning set up for ${subnet.name}`)
      }
      await onSaved()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  // an agent the list does not have (it failed to load) still shows as chosen
  const known = agents.some((a) => a.id === form.agent_id)

  return (
    <form onSubmit={submit} className="mt-3 border-t border-slate-700 pt-3">
      <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
        <div>
          <label className={labelCls} htmlFor="scan-agent">
            Agent
          </label>
          <select
            id="scan-agent"
            className={inputCls}
            value={form.agent_id}
            onChange={(e) => set('agent_id', e.target.value)}
          >
            <option value="">Unassigned (nothing is scanned)</option>
            {form.agent_id && !known && <option value={form.agent_id}>Current agent</option>}
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
                {a.state !== 'active' ? ` (${a.state})` : ''}
              </option>
            ))}
          </select>
          {agentsFailed && (
            <p className="mt-1 text-xs text-amber-400">Agents could not be loaded.</p>
          )}

          <label className={labelCls} htmlFor="scan-interval">
            Interval (minutes)
          </label>
          <input
            id="scan-interval"
            type="number"
            min="5"
            max="10080"
            step="1"
            className={inputCls}
            value={form.interval}
            onChange={(e) => set('interval', e.target.value)}
            placeholder="blank = only when you click Scan now"
          />

          <label className="mt-3 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              className="tlg-check"
              checked={form.enabled}
              onChange={(e) => set('enabled', e.target.checked)}
            />
            Scanning enabled
          </label>
        </div>

        <div>
          <span className={labelCls}>Methods</span>
          <div className="space-y-1.5">
            {SCAN_METHODS.map((m) => (
              <label key={m.id} className="flex items-start gap-2 text-sm text-slate-300">
                <input
                  type="checkbox"
                  className="tlg-check mt-0.5"
                  checked={form.methods.includes(m.id)}
                  onChange={() => toggleMethod(m.id)}
                />
                <span>
                  {m.label} <span className="text-xs text-slate-500">— {m.hint}</span>
                </span>
              </label>
            ))}
          </div>

          <label
            className={`mt-3 flex items-start gap-2 text-sm ${pool ? 'text-slate-300' : 'text-slate-500'}`}
          >
            <input
              type="checkbox"
              className="tlg-check mt-0.5"
              checked={form.scan_dhcp_pool}
              disabled={!pool}
              onChange={(e) => set('scan_dhcp_pool', e.target.checked)}
            />
            <span>
              Scan DHCP pool
              <span className="block text-xs text-slate-500">
                {pool
                  ? 'Off by default: pool addresses change hands. Turning it off deletes what scans saw in the pool.'
                  : 'This subnet has no DHCP pool defined.'}
              </span>
            </span>
          </label>
        </div>
      </div>

      {error && (
        <p className="mt-3 text-xs text-red-400" role="alert">
          {error}
        </p>
      )}
      <div className="mt-4 flex justify-end gap-2">
        {target && dirty && (
          <button
            type="button"
            className={secondaryBtn}
            onClick={() => {
              setForm(settingsForm(target))
              setError(null)
            }}
          >
            Discard changes
          </button>
        )}
        <button type="submit" className={primaryBtn} disabled={busy || !dirty}>
          {target ? 'Save scanning settings' : 'Set up scanning'}
        </button>
      </div>
    </form>
  )
}

// The scanning section of the subnet view (spec §5.8). `subnet` is the subnet as the
// server last returned it; a new object (after any refresh) reloads the target, whose
// scope may have changed with it.
export default function ScanSettings({ subnet, onChanged }) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [asking, setAsking] = useState(false)
  const targetsRes = useFetch(() => listScanTargets(), [subnet])
  const agentsRes = useFetch(() => listAgents(), [])

  const loaded = targetsRes.data != null
  const target = targetForSubnet(targetsRes.data?.data, subnet.id)
  const agents = agentsRes.data?.data || []
  const agent = target?.agent_id ? agents.find((a) => a.id === target.agent_id) : null

  const scanNow = async () => {
    setAsking(true)
    try {
      await requestScan(subnet.id)
      toast.success(`Scan of ${subnet.name} requested: the agent picks it up at its next check-in`)
      targetsRes.refresh()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setAsking(false)
    }
  }

  const saved = async () => {
    // the subnet record carries scan_dhcp_pool, so the tree and the view reload too
    await onChanged()
    targetsRes.refresh()
    setOpen(false)
  }

  let summary
  if (targetsRes.error && !loaded) summary = 'Scanning settings could not be loaded.'
  else if (!loaded) summary = 'Loading…'
  else if (!target) summary = 'Not scanned.'
  else if (!target.enabled) summary = 'Scanning disabled.'
  else if (!target.agent_id) summary = 'No agent assigned: nothing is scanned.'
  else
    summary = `Scanned by ${agent ? agent.name : 'an agent'}${
      target.interval_minutes ? `, every ${target.interval_minutes} min` : ', on request only'
    } · ${target.methods.join(', ')}${target.scan_dhcp_pool ? ' · DHCP pool included' : ''}`

  return (
    <section
      aria-label="Scanning"
      className="mt-4 rounded-lg border border-slate-700 bg-slate-800 px-5 py-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Radar size={14} className="text-slate-400" />
          <h2 className="text-sm font-semibold text-slate-200">Scanning</h2>
          <span className="text-xs text-slate-400">{summary}</span>
        </div>
        <div className="flex gap-2">
          {loaded && (
            <button type="button" className={secondaryBtn} onClick={() => setOpen((o) => !o)}>
              {open ? 'Close settings' : target ? 'Settings' : 'Set up scanning'}
            </button>
          )}
          {target && (
            <button type="button" className={primaryBtn} onClick={scanNow} disabled={asking}>
              Scan now
            </button>
          )}
        </div>
      </div>
      {target && <Status target={target} />}
      {open && loaded && (
        <SettingsForm
          // remount with the server's values whenever they change
          key={JSON.stringify(settingsForm(target))}
          subnet={subnet}
          target={target}
          agents={agents}
          agentsFailed={Boolean(agentsRes.error)}
          onSaved={saved}
        />
      )}
    </section>
  )
}
