import { useState } from 'react'
import { ScanSearch, X } from 'lucide-react'
import { acceptFindings, dismissFindings } from '../api/ipam'
import { useToast } from '../context'
import {
  chunk,
  domainOverride,
  findingId,
  findingKey,
  groupFindings,
  mergeReports,
  notDone,
  observedText,
  proposedFields,
  recordedText,
} from '../utils/findings'
import { primaryBtn, secondaryBtn } from './Modal'

const smallBtn = 'rounded px-2 py-0.5 text-xs disabled:opacity-50'

// The count badge on the subnet view's title row (spec §5.8)
export function FindingsBadge({ count, onClick }) {
  if (!count) return null
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-full border border-amber-800 bg-amber-950 px-2 py-0.5 text-[11px] font-medium text-amber-300 hover:bg-amber-900"
      aria-label={`${count} scan finding${count === 1 ? '' : 's'}`}
    >
      {count} finding{count === 1 ? '' : 's'}
    </button>
  )
}

// What accepting writes. A proposal that sets a domain override says so, with the
// domain the host follows now, because accepting it stops the host following it.
function Proposal({ finding, subnet, host }) {
  const fields = proposedFields(finding)
  const override = domainOverride(finding, subnet, host)
  return (
    <div>
      <span className="font-mono">
        {fields.map(([label, value], i) => (
          <span key={label}>
            {i > 0 && <span className="font-sans text-slate-600">, </span>}
            <span className="font-sans text-slate-500">{label} </span>
            {value}
          </span>
        ))}
      </span>
      {override && (
        <p className="mt-0.5 text-amber-400" data-testid="domain-override">
          Sets a domain override: {override.domain}, so the FQDN is {override.fqdn}.{' '}
          {override.current
            ? `The host ${override.inherited ? 'follows the subnet’s domain' : 'has its own domain'}, ${override.current}, now.`
            : 'The host has no domain now.'}
        </p>
      )}
    </div>
  )
}

// A 4xx answer refuses the request as a whole before anything is written (a body the
// server will not read, a missing or wrong key). Anything else (a 5xx, or no answer at
// all) leaves what was written unknown.
const refusedWhole = (err) => err?.status >= 400 && err.status < 500

function Outcome({ outcome, onClose }) {
  const { verb, done, lines, failure, unknown, unsent } = outcome
  const past = verb === 'accept' ? 'accepted' : 'dismissed'
  return (
    <div
      role="status"
      className="mt-3 rounded-md border border-slate-600 bg-slate-900 px-3 py-2 text-xs text-slate-300"
    >
      <div className="flex items-start justify-between gap-2">
        <p>
          {done} {past}
          {lines.length > 0 && `, ${lines.length} not ${past}`}
          {unknown > 0 && `, ${unknown} unknown`}
          {unsent > 0 && `, ${unsent} not sent`}.
        </p>
        <button type="button" onClick={onClose} aria-label="Close result" className="text-slate-500 hover:text-slate-300">
          <X size={12} />
        </button>
      </div>
      {lines.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {lines.map((line) => (
            <li key={line.id}>
              <span className="font-mono">{line.ip}</span> ({line.type}):{' '}
              <span className={line.status === 'error' ? 'text-red-400' : 'text-amber-400'}>
                {line.status}, {line.reason}
              </span>
              {line.explanation && <span className="text-slate-400"> — {line.explanation}</span>}
            </li>
          ))}
        </ul>
      )}
      {failure && (
        <p className="mt-1 text-red-400">
          The request failed: {failure}
          {unknown > 0 &&
            ` — ${unknown} finding(s) were sent with no answer, so some may have been ${past}: the view has been reloaded to show what the server has now.`}
          {unsent > 0 && ` ${unsent} finding(s) were not sent.`}
        </p>
      )}
    </div>
  )
}

function Group({ group, subnet, hostsById, busy, onRun }) {
  const [confirming, setConfirming] = useState(false)
  const { type, findings } = group
  const overrides = findings.filter((f) => domainOverride(f, subnet, hostsById[f.host_id])).length
  return (
    <div role="group" aria-label={type.label} className="mt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold text-slate-300">
          {type.label} <span className="font-normal text-slate-500">({findings.length})</span>
          {type.hint && <span className="ml-2 font-normal text-slate-500">{type.hint}</span>}
        </h3>
        {confirming ? (
          <span className="flex items-center gap-2 text-xs text-slate-400">
            {overrides > 0 && (
              <span className="text-amber-400">{overrides} set a domain override.</span>
            )}
            <button
              type="button"
              className={primaryBtn}
              disabled={busy}
              onClick={async () => {
                setConfirming(false)
                await onRun('accept', findings)
              }}
            >
              Confirm: {type.action.toLowerCase()} × {findings.length}
            </button>
            <button type="button" className={secondaryBtn} onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </span>
        ) : (
          findings.length > 1 && (
            <button
              type="button"
              className={secondaryBtn}
              disabled={busy}
              onClick={() => setConfirming(true)}
            >
              Accept all {findings.length}
            </button>
          )
        )}
      </div>
      <table className="mt-2 w-full table-fixed text-left text-xs">
        <thead className="text-slate-500">
          <tr className="border-b border-slate-700">
            <th className="w-32 py-1.5 pr-3 font-medium">IP</th>
            <th className="py-1.5 pr-3 font-medium">Recorded</th>
            <th className="py-1.5 pr-3 font-medium">Seen by the scan</th>
            <th className="py-1.5 pr-3 font-medium">Accepting writes</th>
            <th className="w-44 py-1.5 font-medium">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {findings.map((f) => {
            const host = hostsById[f.host_id]
            return (
              <tr key={findingId(f)} className="border-b border-slate-800 align-top">
                <td className="py-1.5 pr-3 font-mono text-slate-200">
                  {f.ip}
                  {f.dynamic && (
                    <span className="ml-1.5 rounded-full border border-blue-800 bg-blue-950 px-1.5 font-sans text-[10px] text-blue-300">
                      dynamic
                    </span>
                  )}
                </td>
                <td className="break-words py-1.5 pr-3 text-slate-400">{recordedText(f, host)}</td>
                <td className="break-words py-1.5 pr-3 font-mono text-slate-200">{observedText(f)}</td>
                <td className="break-words py-1.5 pr-3 text-slate-300">
                  <Proposal finding={f} subnet={subnet} host={host} />
                </td>
                <td className="py-1.5 text-right">
                  <span className="inline-flex gap-1.5">
                    <button
                      type="button"
                      className={`${smallBtn} bg-blue-600 text-white hover:bg-blue-500`}
                      disabled={busy}
                      onClick={() => onRun('accept', [f])}
                      aria-label={`${type.action} ${f.ip}`}
                    >
                      {type.action}
                    </button>
                    <button
                      type="button"
                      className={`${smallBtn} border border-slate-600 text-slate-300 hover:bg-slate-700`}
                      disabled={busy}
                      onClick={() => onRun('dismiss', [f])}
                      aria-label={`Dismiss ${f.ip}`}
                    >
                      Dismiss
                    </button>
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// The findings panel of the subnet view (spec §5.5, §5.8): what scans saw that differs
// from the records, grouped by type, with accept and dismiss per row and bulk accept per
// type. Nothing changes until the owner accepts. `onWritten(accepted)` reloads what the
// answer changed: the hosts, the subnet and the tree after an accept, else the findings.
export default function FindingsPanel({
  subnet,
  findings,
  loaded,
  error,
  hostsById,
  showDynamic,
  onShowDynamic,
  onWritten,
  panelRef,
}) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState(null)
  const groups = groupFindings(findings)

  const run = async (verb, list) => {
    const done = verb === 'accept' ? 'accepted' : 'dismissed'
    const call = verb === 'accept' ? acceptFindings : dismissFindings
    setBusy(true)
    setOutcome(null)
    const reports = []
    const sizes = []
    let failure = null
    // keys in a request that failed without a refusal: the server may have written some
    // of them (accept commits each record on its own) before it failed, or answered and
    // the answer was lost
    let unknown = 0
    for (const part of chunk(list)) {
      try {
        const res = await call(part.map(findingKey))
        reports.push(res.data)
        sizes.push(part.length)
      } catch (err) {
        failure = err.message
        if (!refusedWhole(err)) unknown = part.length
        break
      }
    }
    const report = mergeReports(reports, sizes, done)
    const answered = sizes.reduce((a, b) => a + b, 0)
    const lines = notDone(report, list, done)
    setOutcome({
      verb,
      done: report[done],
      lines,
      failure,
      unknown,
      unsent: list.length - answered - unknown,
    })
    if (report[done] > 0) toast.success(`${report[done]} finding(s) ${done}`)
    if (failure) toast.error(failure)
    setBusy(false)
    // Reload after anything that may have been written: a skipped finding changed, and
    // the list shows it now; an accept of unknown outcome may have written hosts.
    if (answered + unknown > 0) {
      await onWritten(verb === 'accept' && (report[done] > 0 || unknown > 0))
    }
  }

  return (
    <section
      ref={panelRef}
      aria-label="Findings"
      className="mt-4 rounded-lg border border-slate-700 bg-slate-800 px-5 py-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ScanSearch size={14} className="text-slate-400" />
          <h2 className="text-sm font-semibold text-slate-200">Findings</h2>
          <span className="text-xs text-slate-400">
            {error && !loaded
              ? `Findings could not be loaded: ${error.message}`
              : !loaded
                ? 'Loading…'
                : findings.length
                  ? `${findings.length} where a scan and the records disagree. Nothing changes until you accept.`
                  : 'None: scans agree with the records.'}
          </span>
        </div>
        {subnet.scan_dhcp_pool && (
          <label className="flex items-center gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              className="tlg-check"
              checked={showDynamic}
              onChange={(e) => onShowDynamic(e.target.checked)}
            />
            Show dynamic
            <span className="text-slate-500">(unknown hosts in the DHCP pool)</span>
          </label>
        )}
      </div>
      {error && loaded && (
        // useFetch keeps the last list when a reload fails: say it is not current
        <p className="mt-2 text-xs text-amber-400" role="alert">
          Findings could not be reloaded: {error.message}. The list below may be out of date.
        </p>
      )}
      {outcome && <Outcome outcome={outcome} onClose={() => setOutcome(null)} />}
      {groups.map((group) => (
        <Group
          key={group.type.id}
          group={group}
          subnet={subnet}
          hostsById={hostsById}
          busy={busy}
          onRun={run}
        />
      ))}
    </section>
  )
}
