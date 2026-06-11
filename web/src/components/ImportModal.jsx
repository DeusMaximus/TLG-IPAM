import { useMemo, useState } from 'react'
import { Download } from 'lucide-react'
import { bulkCreateHosts } from '../api/ipam'
import { useToast, useTree } from '../context'
import { csvToHosts } from '../utils/csv'
import Modal, { inputCls, primaryBtn, secondaryBtn } from './Modal'

// Only ip is required; blank cells and omitted columns are simply left unset.
const EXAMPLE_CSV = [
  'ip,hostname,domain,mac,device_type,status,owner,notes',
  '10.10.30.21,sw-core,,aa:bb:cc:00:11:22,network,active,infra,core switch',
  '10.10.30.40,pve-01,,bc:24:11:aa:bb:cc,server,active,proxmox,',
  '10.10.30.55,sensor-kitchen,iot.example.net,,iot,dhcp,,no MAC recorded yet',
].join('\n')

const EXAMPLE_HREF = `data:text/csv;charset=utf-8,${encodeURIComponent(EXAMPLE_CSV)}`

export default function ImportModal({ subnet, onClose, onSaved }) {
  const toast = useToast()
  const { refreshTree } = useTree()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  const parsed = useMemo(() => {
    if (!text.trim()) return null
    try {
      return csvToHosts(text)
    } catch (err) {
      return { error: err.message }
    }
  }, [text])

  const pickFile = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => setText(String(reader.result))
    reader.readAsText(file)
  }

  const runImport = async () => {
    setBusy(true)
    try {
      const res = await bulkCreateHosts(subnet.id, parsed.hosts)
      setResult(res.data)
      toast.success(`Imported ${res.data.created} host(s) into ${subnet.name}`)
      await refreshTree()
      onSaved()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy(false)
    }
  }

  const problems = result?.results.filter((r) => r.status !== 'created') || []

  return (
    <Modal title={`Import hosts into ${subnet.name}`} onClose={onClose} wide>
      {!result ? (
        <>
          <p className="text-xs text-slate-400">
            CSV with a header row. Columns: <code>ip</code> (required), <code>hostname</code>,{' '}
            <code>domain</code>, <code>mac</code>, <code>device_type</code>, <code>status</code>,{' '}
            <code>owner</code>, <code>notes</code> — all optional columns and blank cells are
            fine. Duplicates are skipped, bad rows are reported — the rest still import.
          </p>
          <div className="mt-3 flex items-center justify-between gap-3">
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={pickFile}
              className="block text-xs text-slate-400 file:mr-3 file:rounded-md file:border-0 file:bg-slate-700 file:px-3 file:py-1.5 file:text-xs file:text-slate-200 hover:file:bg-slate-600"
            />
            <a
              href={EXAMPLE_HREF}
              download="tlg-ipam-import-example.csv"
              className="flex shrink-0 items-center gap-1 text-xs text-blue-400 hover:text-blue-300"
            >
              <Download size={12} /> example.csv
            </a>
          </div>
          <textarea
            rows={8}
            className={`${inputCls} mt-3 font-mono text-xs`}
            placeholder={'ip,hostname,mac,device_type\n10.10.30.21,sw-core,aa:bb:cc:00:11:22,network'}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          {parsed?.error && <p className="mt-2 text-xs text-red-400">{parsed.error}</p>}
          {parsed && !parsed.error && (
            <p className="mt-2 text-xs text-slate-400">
              {parsed.hosts.length} row(s) ready to import
              {parsed.ignoredColumns.length > 0 && (
                <span className="text-slate-500">
                  {' '}
                  — ignoring column(s): {parsed.ignoredColumns.join(', ')}
                </span>
              )}
            </p>
          )}
          <div className="mt-5 flex justify-end gap-2">
            <button type="button" onClick={onClose} className={secondaryBtn}>
              Cancel
            </button>
            <button
              type="button"
              onClick={runImport}
              disabled={busy || !parsed || parsed.error || parsed.hosts.length === 0}
              className={primaryBtn}
            >
              Import {parsed && !parsed.error ? parsed.hosts.length : ''} host(s)
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="text-sm text-slate-200">
            <span className="text-emerald-400">{result.created} created</span>
            {result.skipped > 0 && (
              <span className="text-amber-400">, {result.skipped} skipped</span>
            )}
            {result.errors > 0 && <span className="text-red-400">, {result.errors} failed</span>}
          </p>
          {problems.length > 0 && (
            <div className="mt-3 max-h-48 space-y-1 overflow-y-auto">
              {problems.map((r) => (
                <div key={r.index} className="rounded bg-slate-900 px-3 py-1.5 text-xs">
                  <code className="text-slate-300">{r.ip || `row ${r.index + 2}`}</code>{' '}
                  <span className={r.status === 'skipped' ? 'text-amber-400' : 'text-red-400'}>
                    {r.status}
                  </span>{' '}
                  <span className="text-slate-500">{r.reason}</span>
                </div>
              ))}
            </div>
          )}
          <div className="mt-5 flex justify-end">
            <button type="button" onClick={onClose} className={primaryBtn}>
              Done
            </button>
          </div>
        </>
      )}
    </Modal>
  )
}
