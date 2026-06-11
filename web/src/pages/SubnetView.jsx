import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Pencil, Plus, Trash2, Upload, Zap } from 'lucide-react'
import { bulkDeleteHosts, deleteSubnet, getSubnet, listHosts } from '../api/ipam'
import AllocateModal from '../components/AllocateModal'
import EmptyState from '../components/EmptyState'
import HostDrawer from '../components/HostDrawer'
import HostTable from '../components/HostTable'
import ImportModal from '../components/ImportModal'
import Modal, { dangerBtn, primaryBtn, secondaryBtn } from '../components/Modal'
import ReservedRanges from '../components/ReservedRanges'
import SubnetFormModal from '../components/SubnetFormModal'
import UtilPill from '../components/UtilPill'
import { useToast, useTree } from '../context'
import { useFetch } from '../hooks/useFetch'

function Fact({ label, children }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-200">{children}</dd>
    </div>
  )
}

export default function SubnetView() {
  const { subnetId } = useParams()
  const toast = useToast()
  const navigate = useNavigate()
  const { refreshTree } = useTree()
  const [editing, setEditing] = useState(false)
  const [allocating, setAllocating] = useState(false)
  const [importing, setImporting] = useState(false)
  const [drawer, setDrawer] = useState(null) // {host} | {new: true}
  const [selected, setSelected] = useState(new Set())
  const [confirmingBulkDelete, setConfirmingBulkDelete] = useState(false)
  const [confirmingSubnetDelete, setConfirmingSubnetDelete] = useState(false)

  useEffect(() => {
    setSelected(new Set())
    setConfirmingBulkDelete(false)
  }, [subnetId])

  const subnetRes = useFetch(() => getSubnet(subnetId), [subnetId])
  const hostsRes = useFetch(() => listHosts({ subnet_id: subnetId }), [subnetId])

  const subnet = subnetRes.data?.data
  const hosts = hostsRes.data?.data || []

  if (subnetRes.error) {
    return <EmptyState>Subnet not found.</EmptyState>
  }
  if (!subnet) return null

  const refreshAll = () => {
    subnetRes.refresh()
    hostsRes.refresh()
  }

  const removeSubnet = async (force) => {
    try {
      await deleteSubnet(subnet.id, { force })
      toast.success(`Subnet ${subnet.name} deleted`)
      await refreshTree()
      navigate(`/sections/${subnet.section_id}`)
    } catch (err) {
      toast.error(err.message)
    }
  }

  const toggleHost = (id) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const toggleAllHosts = () =>
    setSelected((prev) =>
      prev.size === hosts.length ? new Set() : new Set(hosts.map((h) => h.id))
    )

  const deleteSelected = async () => {
    try {
      const res = await bulkDeleteHosts([...selected])
      toast.success(`Deleted ${res.data.deleted} host(s)`)
      setSelected(new Set())
      setConfirmingBulkDelete(false)
      await refreshTree()
      refreshAll()
    } catch (err) {
      toast.error(err.message)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold text-slate-100">{subnet.name}</h1>
          <UtilPill used={subnet.used} usable={subnet.usable} />
        </div>
        <div className="flex gap-2">
          <button onClick={() => setEditing(true)} className={secondaryBtn}>
            <span className="flex items-center gap-1.5"><Pencil size={13} /> Edit</span>
          </button>
          <button
            onClick={() => (hosts.length ? setConfirmingSubnetDelete(true) : removeSubnet(false))}
            className={secondaryBtn}
            title="Delete subnet"
          >
            <span className="flex items-center gap-1.5 text-red-400"><Trash2 size={13} /> Delete</span>
          </button>
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-4 rounded-lg border border-slate-700 bg-slate-800 px-5 py-4 sm:grid-cols-3 lg:grid-cols-4">
        <Fact label="CIDR"><code>{subnet.cidr}</code></Fact>
        <Fact label="VLAN">{subnet.vlan_id ?? '—'}</Fact>
        <Fact label="Gateway">{subnet.gateway ? <code>{subnet.gateway}</code> : '—'}</Fact>
        <Fact label="Domain">{subnet.domain || '—'}</Fact>
        <Fact label="DNS">
          {subnet.dns_servers?.length ? subnet.dns_servers.join(', ') : '—'}
        </Fact>
        <Fact label="DHCP pool">
          {subnet.dhcp_start ? (
            <code>
              {subnet.dhcp_start}–{subnet.dhcp_end}
            </code>
          ) : (
            '—'
          )}
        </Fact>
        <Fact label="Free IPs">{subnet.free}</Fact>
      </dl>
      <ReservedRanges subnet={subnet} onChanged={refreshAll} />
      {subnet.notes && <p className="mt-2 text-sm text-slate-400">{subnet.notes}</p>}

      <div className="mt-6 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-200">
          Hosts <span className="font-normal text-slate-500">({hosts.length})</span>
        </h2>
        <div className="flex gap-2">
          {selected.size > 0 &&
            (confirmingBulkDelete ? (
              <span className="flex items-center gap-2">
                <button onClick={deleteSelected} className={dangerBtn}>
                  Confirm delete {selected.size}
                </button>
                <button onClick={() => setConfirmingBulkDelete(false)} className={secondaryBtn}>
                  Keep
                </button>
              </span>
            ) : (
              <button onClick={() => setConfirmingBulkDelete(true)} className={secondaryBtn}>
                <span className="flex items-center gap-1.5 text-red-400">
                  <Trash2 size={13} /> Delete {selected.size} selected
                </span>
              </button>
            ))}
          <button onClick={() => setImporting(true)} className={secondaryBtn}>
            <span className="flex items-center gap-1.5"><Upload size={13} /> Import CSV</span>
          </button>
          <button onClick={() => setDrawer({ new: true })} className={secondaryBtn}>
            <span className="flex items-center gap-1.5"><Plus size={13} /> Add host</span>
          </button>
          <button onClick={() => setAllocating(true)} className={primaryBtn}>
            <span className="flex items-center gap-1.5"><Zap size={13} /> Allocate next free IP</span>
          </button>
        </div>
      </div>

      <div className="mt-3">
        {hosts.length === 0 && !hostsRes.loading ? (
          <EmptyState>No hosts yet — add one or allocate the next free IP.</EmptyState>
        ) : (
          <HostTable
            hosts={hosts}
            onRowClick={(host) => setDrawer({ host })}
            selected={selected}
            onToggle={toggleHost}
            onToggleAll={toggleAllHosts}
          />
        )}
      </div>

      {editing && (
        <SubnetFormModal subnet={subnet} onClose={() => setEditing(false)} onSaved={refreshAll} />
      )}
      {allocating && (
        <AllocateModal subnet={subnet} onClose={() => setAllocating(false)} onSaved={refreshAll} />
      )}
      {importing && (
        <ImportModal subnet={subnet} onClose={() => setImporting(false)} onSaved={refreshAll} />
      )}
      {confirmingSubnetDelete && (
        <Modal title={`Delete ${subnet.name}?`} onClose={() => setConfirmingSubnetDelete(false)}>
          <p className="text-sm text-slate-300">
            This subnet still has <strong>{hosts.length} host record(s)</strong>. Deleting it
            will remove the subnet, all of its hosts, and its reserved ranges. This cannot be
            undone.
          </p>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setConfirmingSubnetDelete(false)} className={secondaryBtn}>
              Cancel
            </button>
            <button onClick={() => removeSubnet(true)} className={dangerBtn}>
              Delete subnet and {hosts.length} host(s)
            </button>
          </div>
        </Modal>
      )}
      {drawer && (
        <HostDrawer
          host={drawer.host}
          subnetId={subnet.id}
          onClose={() => setDrawer(null)}
          onSaved={refreshAll}
        />
      )}
    </div>
  )
}
