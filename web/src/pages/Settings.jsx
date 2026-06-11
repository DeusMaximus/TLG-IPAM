import { useState } from 'react'
import { Download, KeyRound } from 'lucide-react'
import { clearApiKey, getApiKey } from '../api/client'
import { getHealth } from '../api/ipam'
import DeviceTypesManager from '../components/DeviceTypesManager'
import { primaryBtn, secondaryBtn } from '../components/Modal'
import { useToast } from '../context'
import { useFetch } from '../hooks/useFetch'

function mask(key) {
  if (!key) return '—'
  if (key.length <= 8) return '••••••••'
  return `${key.slice(0, 4)}${'•'.repeat(12)}${key.slice(-4)}`
}

export default function Settings() {
  const toast = useToast()
  const health = useFetch(getHealth, [])
  const [revealed, setRevealed] = useState(false)

  const downloadBackup = async () => {
    try {
      const res = await fetch('/api/v1/backup', { headers: { 'X-API-Key': getApiKey() } })
      if (!res.ok) throw new Error(`Backup failed (HTTP ${res.status})`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `ipam-backup-${new Date().toISOString().slice(0, 10)}.db`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      toast.error(err.message)
    }
  }

  const changeKey = () => {
    clearApiKey()
    window.location.reload()
  }

  return (
    <div className="max-w-xl">
      <h1 className="text-lg font-semibold text-slate-100">Settings</h1>

      <section className="mt-5 rounded-lg border border-slate-700 bg-slate-800 px-5 py-4">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <KeyRound size={14} /> API key
        </h2>
        <p className="mt-1 text-xs text-slate-400">
          Used for the REST API and the MCP endpoint. Stored in this browser only.
        </p>
        <div className="mt-3 flex items-center gap-3">
          <code
            className="cursor-pointer rounded bg-slate-900 px-3 py-1.5 text-sm text-slate-300"
            onClick={() => setRevealed(!revealed)}
            title="Click to toggle"
          >
            {revealed ? getApiKey() : mask(getApiKey())}
          </code>
          <button onClick={changeKey} className={secondaryBtn}>
            Change key
          </button>
        </div>
      </section>

      <DeviceTypesManager />

      <section className="mt-4 rounded-lg border border-slate-700 bg-slate-800 px-5 py-4">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <Download size={14} /> Backup
        </h2>
        <p className="mt-1 text-xs text-slate-400">
          Download a copy of the SQLite database. Restoring = replacing the file in the data
          volume.
        </p>
        <button onClick={downloadBackup} className={`mt-3 ${primaryBtn}`}>
          Download backup
        </button>
      </section>

      <p className="mt-5 text-xs text-slate-500">
        TLG-IPAM {health.data?.version ? `v${health.data.version}` : ''} · MCP endpoint at{' '}
        <code>/mcp</code> · API docs at <code>/api/v1/docs</code>
      </p>
    </div>
  )
}
