import { useEffect, useState } from 'react'
import { allocateNextFree, getFreeIps } from '../api/ipam'
import { useToast, useTree } from '../context'
import Modal, { inputCls, labelCls, primaryBtn, secondaryBtn } from './Modal'

export default function AllocateModal({ subnet, onClose, onSaved }) {
  const toast = useToast()
  const { deviceTypes, refreshTree } = useTree()
  const [nextIp, setNextIp] = useState(null)
  const [form, setForm] = useState({ hostname: '', device_type: 'other', owner: '' })
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    getFreeIps(subnet.id, 1)
      .then((res) => setNextIp(res.data[0] || null))
      .catch(() => setNextIp(null))
  }, [subnet.id])

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      const res = await allocateNextFree(subnet.id, {
        hostname: form.hostname.trim(),
        device_type: form.device_type,
        owner: form.owner.trim() || null,
      })
      toast.success(`Allocated ${res.data.ip} to ${res.data.hostname}`)
      await refreshTree()
      onSaved()
      onClose()
    } catch (err) {
      toast.error(err.message)
      setBusy(false)
    }
  }

  return (
    <Modal title={`Allocate next free IP in ${subnet.name}`} onClose={onClose}>
      <form onSubmit={submit}>
        <p className="text-sm text-slate-400">
          Next free address:{' '}
          {nextIp ? (
            <code className="text-slate-100">{nextIp}</code>
          ) : (
            <span className="text-amber-400">none available</span>
          )}
        </p>
        <label className={labelCls}>Hostname</label>
        <input
          autoFocus
          required
          className={inputCls}
          value={form.hostname}
          onChange={(e) => setForm({ ...form, hostname: e.target.value })}
          placeholder="new-lxc-01"
        />
        <div className="grid grid-cols-2 gap-x-4">
          <div>
            <label className={labelCls}>Device type</label>
            <select
              className={inputCls}
              value={form.device_type}
              onChange={(e) => setForm({ ...form, device_type: e.target.value })}
            >
              {deviceTypes.map((t) => (
                <option key={t.id} value={t.name}>{t.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className={labelCls}>Owner</label>
            <input
              className={inputCls}
              value={form.owner}
              onChange={(e) => setForm({ ...form, owner: e.target.value })}
              placeholder="proxmox"
            />
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className={secondaryBtn}>
            Cancel
          </button>
          <button type="submit" disabled={busy || !nextIp} className={primaryBtn}>
            Allocate
          </button>
        </div>
      </form>
    </Modal>
  )
}
