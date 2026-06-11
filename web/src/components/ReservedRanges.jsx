import { useState } from 'react'
import { Lock, Plus, X } from 'lucide-react'
import { createReservedRange, deleteReservedRange } from '../api/ipam'
import { useToast } from '../context'
import Modal, { inputCls, labelCls, primaryBtn, secondaryBtn } from './Modal'

function ReserveModal({ subnet, onClose, onSaved }) {
  const toast = useToast()
  const [form, setForm] = useState({ start_ip: '', end_ip: '', description: '' })
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
  const [adding, setAdding] = useState(false)

  const remove = async (block) => {
    try {
      await deleteReservedRange(subnet.id, block.id)
      toast.success(`Unreserved ${block.start_ip}–${block.end_ip}`)
      onChanged()
    } catch (err) {
      toast.error(err.message)
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
        <ReserveModal subnet={subnet} onClose={() => setAdding(false)} onSaved={onChanged} />
      )}
    </div>
  )
}
