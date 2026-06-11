import { useState } from 'react'
import { Pencil, Plus, Shapes, Trash2 } from 'lucide-react'
import { createDeviceType, deleteDeviceType, updateDeviceType } from '../api/ipam'
import { useToast, useTree } from '../context'
import { ICONS, IconGlyph } from './DeviceIcon'
import Modal, { inputCls, labelCls, primaryBtn, secondaryBtn } from './Modal'

const FALLBACK = 'other'

function DeviceTypeModal({ deviceType, onClose }) {
  const toast = useToast()
  const { refreshTree } = useTree()
  const isFallback = deviceType?.name === FALLBACK
  const [name, setName] = useState(deviceType?.name || '')
  const [icon, setIcon] = useState(deviceType?.icon || 'help-circle')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      if (deviceType) {
        await updateDeviceType(deviceType.id, isFallback ? { icon } : { name: name.trim(), icon })
        toast.success(`Device type ${isFallback ? FALLBACK : name} updated`)
      } else {
        await createDeviceType({ name: name.trim(), icon })
        toast.success(`Device type ${name} added`)
      }
      await refreshTree()
      onClose()
    } catch (err) {
      toast.error(err.message)
      setBusy(false)
    }
  }

  return (
    <Modal title={deviceType ? `Edit ${deviceType.name}` : 'New device type'} onClose={onClose}>
      <form onSubmit={submit}>
        <label className={labelCls}>Name</label>
        <input
          autoFocus={!deviceType}
          required
          disabled={isFallback}
          className={`${inputCls} disabled:opacity-50`}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Raspberry Pi"
          maxLength={40}
        />
        {isFallback && (
          <p className="mt-1 text-[11px] text-slate-500">
            “{FALLBACK}” is the fallback for new hosts — its name can’t change, but its icon can.
          </p>
        )}
        <label className={labelCls}>Icon</label>
        <div className="grid grid-cols-7 gap-1.5">
          {Object.keys(ICONS).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setIcon(key)}
              title={key}
              className={`flex items-center justify-center rounded-md border p-2 transition-colors ${
                icon === key
                  ? 'border-blue-500 bg-blue-500/15'
                  : 'border-slate-700 bg-slate-900 hover:border-slate-500'
              }`}
            >
              <IconGlyph icon={key} size={16} className={icon === key ? 'text-blue-400' : 'text-slate-400'} />
            </button>
          ))}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className={secondaryBtn}>
            Cancel
          </button>
          <button type="submit" disabled={busy} className={primaryBtn}>
            {deviceType ? 'Save' : 'Add'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

export default function DeviceTypesManager() {
  const toast = useToast()
  const { deviceTypes, refreshTree } = useTree()
  const [modal, setModal] = useState(null) // {deviceType} | {new: true}

  const remove = async (deviceType) => {
    try {
      await deleteDeviceType(deviceType.id)
      toast.success(`Device type ${deviceType.name} deleted`)
      await refreshTree()
    } catch (err) {
      toast.error(err.message)
    }
  }

  return (
    <section className="mt-4 rounded-lg border border-slate-700 bg-slate-800 px-5 py-4">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <Shapes size={14} /> Device types
        </h2>
        <button
          onClick={() => setModal({ new: true })}
          className="flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300"
        >
          <Plus size={12} /> Add type
        </button>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        Types in use (or “{FALLBACK}”) can’t be deleted; renaming a type updates every host
        using it.
      </p>
      <div className="mt-3 divide-y divide-slate-700/60">
        {deviceTypes.map((deviceType) => (
          <div key={deviceType.id} className="group flex items-center gap-3 py-1.5">
            <IconGlyph icon={deviceType.icon} size={15} />
            <span className="flex-1 text-sm text-slate-200">{deviceType.name}</span>
            <span className="text-xs tabular-nums text-slate-500">
              {deviceType.host_count} host{deviceType.host_count === 1 ? '' : 's'}
            </span>
            <button
              onClick={() => setModal({ deviceType })}
              className="text-slate-500 hover:text-slate-200"
              title="Edit"
            >
              <Pencil size={13} />
            </button>
            <button
              onClick={() => remove(deviceType)}
              disabled={deviceType.host_count > 0 || deviceType.name === FALLBACK}
              className="text-slate-500 hover:text-red-400 disabled:cursor-not-allowed disabled:opacity-30"
              title={
                deviceType.name === FALLBACK
                  ? 'Fallback type cannot be deleted'
                  : deviceType.host_count > 0
                    ? 'In use — reassign its hosts first'
                    : 'Delete'
              }
            >
              <Trash2 size={13} />
            </button>
          </div>
        ))}
      </div>
      {modal && <DeviceTypeModal deviceType={modal.deviceType} onClose={() => setModal(null)} />}
    </section>
  )
}
