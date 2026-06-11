import { useState } from 'react'
import { createSection, updateSection } from '../api/ipam'
import { useToast, useTree } from '../context'
import Modal, { inputCls, labelCls, primaryBtn, secondaryBtn } from './Modal'

export default function SectionFormModal({ section, onClose }) {
  const { refreshTree } = useTree()
  const toast = useToast()
  const [form, setForm] = useState({
    name: section?.name || '',
    description: section?.description || '',
    colour: section?.colour || '#3b82f6',
  })
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      if (section) {
        await updateSection(section.id, form)
        toast.success(`Section ${form.name} updated`)
      } else {
        await createSection(form)
        toast.success(`Section ${form.name} created`)
      }
      await refreshTree()
      onClose()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={section ? 'Edit section' : 'New section'} onClose={onClose}>
      <form onSubmit={submit}>
        <label className={labelCls}>Name</label>
        <input
          autoFocus
          required
          className={inputCls}
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          placeholder="Homelab"
        />
        <label className={labelCls}>Description</label>
        <input
          className={inputCls}
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
        />
        <label className={labelCls}>Colour</label>
        <input
          type="color"
          className="h-8 w-16 cursor-pointer rounded border border-slate-600 bg-slate-900"
          value={form.colour}
          onChange={(e) => setForm({ ...form, colour: e.target.value })}
        />
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className={secondaryBtn}>
            Cancel
          </button>
          <button type="submit" disabled={busy} className={primaryBtn}>
            {section ? 'Save' : 'Create'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
