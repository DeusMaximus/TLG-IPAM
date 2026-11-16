import { useState } from 'react'
import { Lock, Plus, X } from 'lucide-react'
import { createReservedRange, deleteReservedRange, updateReservedRange } from '../api/ipam'
import { useToast, useTree } from '../context'
import Modal, { inputCls, labelCls, primaryBtn, secondaryBtn } from './Modal'

function ReserveModal({ subnet, onClose, onSaved }) {
  const toast = useToast()
  const [form, setForm] = useState({ start_ip: '', end_ip: '', description: '', scan: true })
  const [busy, setBusy] = useState(false)

  const set = (field) => (e) => setForm({ ...form, [field]: e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      await createReservedRange(subnet.id, {
        start_ip: form.start_ip.trim(),
        end_ip: form.end_ip.trim(),
        description: form.description.trim() || null,
        scan_excluded: !form.scan,
      })
      toast.success(`Reserved ${form.start_ip}–${form.end_ip}`)
      onSaved()
      onClose()
    } catch (err) {
      toast.error(err.message)
      setBusy(false)
    }
  }

  return (
    <Modal title={`Reserve a range in ${subnet.name}`} onClose={onClose}>
      <form onSubmit={submit}>
        <p className="text-xs text-slate-400">
          “Allocate next free IP” skips reserved ranges, but you can still add hosts inside
          them manually.
        </p>
        <div className="grid grid-cols-2 gap-x-4">
          <div>
            <label className={labelCls}>Start IP</label>
            <input autoFocus required className={inputCls} value={form.start_ip} onChange={set('start_ip')} placeholder="10.10.30.2" />
          </div>
          <div>
            <label className={labelCls}>End IP</label>
            <input required className={inputCls} value={form.end_ip} onChange={set('end_ip')} placeholder="10.10.30.19" />
          </div>
        </div>
        <label className={labelCls}>Description</label>
        <input className={inputCls} value={form.description} onChange={set('description')} placeholder="network gear" />
        <label className="mt-3 flex items-start gap-2 text-sm text-slate-300">
          <input
            type="checkbox"
            className="tlg-check mt-0.5"
            checked={form.scan}
            onChange={(e) => setForm({ ...form, scan: e.target.checked })}
          />
          <span>
            Scan this range
            <span className="block text-xs text-slate-500">
              Untick for devices that should not be probed, or that someone else manages.
            </span>
          </span>
        </label>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className={secondaryBtn}>
            Cancel
          </button>
          <button type="submit" disabled={busy} className={primaryBtn}>
            Reserve
          </button>
        </div>
      </form>
    </Modal>
  )
}

export default function ReservedRanges({ subnet, onChanged }) {
  const toast = useToast()
  const { refreshTree } = useTree()
  const [adding, setAdding] = useState(false)
  const [saving, setSaving] = useState(null) // id of the range whose Scan box is saving

  // subnets in the tree carry their reserved ranges, scan_excluded included
  const changed = async () => {
    await refreshTree()
    onChanged()
  }

  const remove = async (block) => {
    try {
      await deleteReservedRange(subnet.id, block.id)
      toast.success(`Unreserved ${block.start_ip}–${block.end_ip}`)
      await changed()
    } catch (err) {
      toast.error(err.message)
    }
  }

  const setScanned = async (block, scanned) => {
    setSaving(block.id)
    try {
      await updateReservedRange(subnet.id, block.id, { scan_excluded: !scanned })
      toast.success(
        `${block.start_ip}–${block.end_ip} ${scanned ? 'is scanned' : 'is no longer scanned'}`
      )
      await changed()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <span className="flex items-center gap-1.5 text-xs text-slate-500">
        <Lock size={12} /> Reserved
      </span>
      {subnet.reserved_ranges.length === 0 && (
        <span className="text-xs text-slate-600">none</span>
      )}
      {subnet.reserved_ranges.map((block) => (
        <span
          key={block.id}
          className="group flex items-center gap-1.5 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-xs text-slate-300"
        >
          <code>
            {block.start_ip}–{block.end_ip}
          </code>
          {block.description && <span className="text-slate-500">{block.description}</span>}
          <label
            className="flex items-center gap-1 text-slate-400"
            title={
              block.scan_excluded
                ? 'Not scanned: no packet is sent to these addresses'
                : 'Scanned. Unticking deletes what scans saw in this range'
            }
          >
            <input
              type="checkbox"
              className="tlg-check"
              checked={!block.scan_excluded}
              disabled={saving === block.id}
              onChange={(e) => setScanned(block, e.target.checked)}
              aria-label={`Scan ${block.start_ip}–${block.end_ip}`}
            />
            {block.scan_excluded ? <span className="text-amber-400">not scanned</span> : 'Scan'}
          </label>
          <button
            onClick={() => remove(block)}
            className="text-slate-600 hover:text-red-400"
            title="Remove reservation"
            aria-label="Remove reservation"
          >
            <X size={12} />
          </button>
        </span>
      ))}
      <button
        onClick={() => setAdding(true)}
        className="flex items-center gap-1 rounded-full border border-dashed border-slate-600 px-2.5 py-1 text-xs text-slate-400 hover:border-slate-400 hover:text-slate-200"
      >
        <Plus size={12} /> Reserve range
      </button>
      {adding && (
        <ReserveModal subnet={subnet} onClose={() => setAdding(false)} onSaved={changed} />
      )}
    </div>
  )
}
