import { useState } from 'react'
import { Search } from 'lucide-react'
import { listChanges, listHosts } from '../api/ipam'
import { useTree } from '../context'
import EmptyState from '../components/EmptyState'
import HostTable from '../components/HostTable'
import HostDrawer from '../components/HostDrawer'
import StatusBadge from '../components/StatusBadge'
import { inputCls } from '../components/Modal'
import { useFetch } from '../hooks/useFetch'

const SOURCE_LABEL = { ui: 'UI', api: 'API', mcp: 'MCP' }

function Stat({ label, value }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-3">
      <div className="text-2xl font-semibold tabular-nums text-slate-100">{value}</div>
      <div className="mt-0.5 text-xs text-slate-400">{label}</div>
    </div>
  )
}

export default function Dashboard() {
  const { sections, subnets } = useTree()
  const [query, setQuery] = useState('')
  const [selectedHost, setSelectedHost] = useState(null)

  const hostsRes = useFetch(() => listHosts(query ? { q: query } : {}), [query])
  const changesRes = useFetch(() => listChanges({ limit: 15 }), [])

  const hosts = hostsRes.data?.data || []
  const changes = changesRes.data?.data || []
  const byStatus = hosts.reduce((acc, h) => ({ ...acc, [h.status]: (acc[h.status] || 0) + 1 }), {})
  const subnetNames = Object.fromEntries(subnets.map((s) => [s.id, s.name]))

  const refreshAll = () => {
    hostsRes.refresh()
    changesRes.refresh()
  }

  return (
    <div>
      <h1 className="text-lg font-semibold text-slate-100">Dashboard</h1>

      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Sections" value={sections.length} />
        <Stat label="Subnets" value={subnets.length} />
        <Stat label="Hosts" value={query ? '—' : hosts.length} />
        <div className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-3">
          <div className="flex flex-wrap gap-1.5">
            {['active', 'reserved', 'dhcp', 'offline'].map((s) => (
              <span key={s} className="flex items-center gap-1">
                <StatusBadge status={s} />
                <span className="text-xs tabular-nums text-slate-400">{byStatus[s] || 0}</span>
              </span>
            ))}
          </div>
          <div className="mt-1.5 text-xs text-slate-400">Hosts by status</div>
        </div>
      </div>

      <div className="mt-8 grid grid-cols-1 gap-8 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-200">Hosts</h2>
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-2 text-slate-500" />
              <input
                className={`${inputCls} w-64 pl-8`}
                placeholder="Search hostname, IP, MAC…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
          </div>
          <div className="mt-3">
            {hosts.length === 0 && !hostsRes.loading ? (
              <EmptyState>
                {query
                  ? `No hosts matching “${query}”.`
                  : 'No hosts yet — pick a subnet in the sidebar and add one, or allocate the next free IP.'}
              </EmptyState>
            ) : (
              <HostTable
                hosts={hosts}
                showSubnet
                subnetNames={subnetNames}
                onRowClick={setSelectedHost}
              />
            )}
          </div>
        </div>

        <div>
          <h2 className="text-sm font-semibold text-slate-200">Recent activity</h2>
          <div className="mt-3 space-y-2">
            {changes.length === 0 && !changesRes.loading && (
              <EmptyState>No changes yet — activity will appear here.</EmptyState>
            )}
            {changes.map((c) => (
              <div key={c.id} className="rounded-md border border-slate-800 bg-slate-800/50 px-3 py-2">
                <div className="text-xs text-slate-300">{c.summary}</div>
                <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-500">
                  <span className="rounded bg-slate-700 px-1.5 py-0.5 font-medium text-slate-300">
                    {SOURCE_LABEL[c.source] || c.source}
                  </span>
                  {new Date(c.ts).toLocaleString()}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {selectedHost && (
        <HostDrawer
          host={selectedHost}
          onClose={() => setSelectedHost(null)}
          onSaved={refreshAll}
        />
      )}
    </div>
  )
}
