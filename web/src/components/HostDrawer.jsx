import { useState } from 'react'
import { X } from 'lucide-react'
import { createHost, deleteHost, updateHost, HOST_STATUSES } from '../api/ipam'
import { useToast, useTree } from '../context'
import { dangerBtn, inputCls, labelCls, primaryBtn, secondaryBtn } from './Modal'

export default function HostDrawer({ host, subnetId, suggestedIp, onClose, onSaved }) {
  const toast = useToast()
  const { subnets, deviceTypes, refreshTree } = useTree()
  const isNew = !host
  const inheritedDomain = subnets.find((s) => s.id === (host?.subnet_id || subnetId))?.domain
  const [form, setForm] = useState({
    ip: host?.ip || suggestedIp || '',
    hostname: host?.hostname || '',
    domain: host?.domain || '',
    mac: host?.mac || '',
    device_type: host?.device_type || 'other',
    status: host?.status || 'active',
    owner: host?.owner || '',
    notes: host?.notes || '',
  })
  const [busy, setBusy] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  const set = (field) => (e) => setForm({ ...form, [field]: e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    const body = {
      ip: form.ip.trim(),
      hostname: form.hostname.trim() || null,
      domain: form.domain.trim() || null,
      mac: form.mac.trim() || null,
      device_type: form.device_type,
      status: form.status,
      owner: form.owner.trim() || null,
      notes: form.notes || null,
    }
    try {
      if (isNew) {
        await createHost({ ...body, subnet_id: subnetId })
        toast.success(`Host ${body.hostname || body.ip} created`)
      } else {
        await updateHost(host.id, body)
        toast.success(`Host ${body.hostname || body.ip} updated`)
      }
      await refreshTree()
      onSaved()
      onClose()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)
    try {
      await deleteHost(host.id)
      toast.success(`Host deleted — ${host.ip} freed`)
      await refreshTree()
      onSaved()
      onClose()
    } catch (err) {
      toast.error(err.message)
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-40" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="absolute inset-0 bg-black/50" onMouseDown={onClose} />
      <aside className="absolute right-0 top-0 flex h-full w-96 flex-col border-l border-slate-700 bg-slate-800 shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-700 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-100">
            {isNew ? 'Add host' : form.hostname || form.ip}
          </h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200" aria-label="Close">
            <X size={16} />
          </button>
        </div>

        <form onSubmit={submit} className="flex-1 overflow-y-auto px-5 py-4">
          <label className={labelCls}>IP address</label>
          <input required className={inputCls} value={form.ip} onChange={set('ip')} placeholder="10.10.30.45" />
          <label className={labelCls}>Hostname</label>
          <input autoFocus={isNew} className={inputCls} value={form.hostname} onChange={set('hostname')} placeholder="pbs-01" />
          <label className={labelCls}>Domain suffix</label>
          <input
            className={inputCls}
            value={form.domain}
            onChange={set('domain')}
            placeholder={inheritedDomain ? `${inheritedDomain} (inherited)` : 'none'}
          />
          {inheritedDomain && (
            <p className="mt-1 text-[11px] text-slate-500">
              Leave blank to inherit the subnet default; type to override for this host.
            </p>
          )}
          <label className={labelCls}>MAC address</label>
          <input className={inputCls} value={form.mac} onChange={set('mac')} placeholder="aa:bb:cc:dd:ee:ff" />
          <div className="grid grid-cols-2 gap-x-4">
            <div>
              <label className={labelCls}>Device type</label>
              <select className={inputCls} value={form.device_type} onChange={set('device_type')}>
                {deviceTypes.map((t) => (
                  <option key={t.id} value={t.name}>{t.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelCls}>Status</label>
              <select className={inputCls} value={form.status} onChange={set('status')}>
                {HOST_STATUSES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
          </div>
          <label className={labelCls}>Owner</label>
          <input className={inputCls} value={form.owner} onChange={set('owner')} placeholder="proxmox" />
          <label className={labelCls}>Notes</label>
          <textarea rows={3} className={inputCls} value={form.notes} onChange={set('notes')} />
          {!isNew && host.last_seen && (
            <p className="mt-3 text-xs text-slate-500">
              Last seen {new Date(host.last_seen).toLocaleString()}
            </p>
          )}

          <div className="mt-6 flex items-center justify-between">
            {!isNew ? (
              confirmingDelete ? (
                <span className="flex items-center gap-2">
                  <button type="button" disabled={busy} onClick={remove} className={dangerBtn}>
                    Confirm delete
                  </button>
                  <button type="button" onClick={() => setConfirmingDelete(false)} className={secondaryBtn}>
                    Keep
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirmingDelete(true)}
                  className="text-sm text-red-400 hover:text-red-300"
                >
                  Delete host…
                </button>
              )
            ) : (
              <span />
            )}
            <button type="submit" disabled={busy} className={primaryBtn}>
              {isNew ? 'Create' : 'Save'}
            </button>
          </div>
        </form>
      </aside>
    </div>
  )
}
