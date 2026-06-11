import { useState } from 'react'
import { createSubnet, updateSubnet } from '../api/ipam'
import { useToast, useTree } from '../context'
import Modal, { inputCls, labelCls, primaryBtn, secondaryBtn } from './Modal'

export default function SubnetFormModal({ sectionId, subnet, onClose, onSaved }) {
  const { refreshTree } = useTree()
  const toast = useToast()
  const [form, setForm] = useState({
    name: subnet?.name || '',
    cidr: subnet?.cidr || '',
    vlan_id: subnet?.vlan_id ?? '',
    domain: subnet?.domain || '',
    gateway: subnet?.gateway || '',
    dns_servers: (subnet?.dns_servers || []).join(', '),
    dhcp_start: subnet?.dhcp_start || '',
    dhcp_end: subnet?.dhcp_end || '',
    notes: subnet?.notes || '',
  })
  const [busy, setBusy] = useState(false)

  const set = (field) => (e) => setForm({ ...form, [field]: e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    const body = {
      name: form.name,
      cidr: form.cidr.trim(),
      vlan_id: form.vlan_id === '' ? null : Number(form.vlan_id),
      domain: form.domain.trim() || null,
      gateway: form.gateway.trim() || null,
      dns_servers: form.dns_servers
        ? form.dns_servers.split(',').map((s) => s.trim()).filter(Boolean)
        : null,
      dhcp_start: form.dhcp_start.trim() || null,
      dhcp_end: form.dhcp_end.trim() || null,
      notes: form.notes || null,
    }
    try {
      if (subnet) {
        await updateSubnet(subnet.id, body)
        toast.success(`Subnet ${form.name} updated`)
      } else {
        await createSubnet({ ...body, section_id: sectionId })
        toast.success(`Subnet ${form.name} created`)
      }
      await refreshTree()
      onSaved?.()
      onClose()
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={subnet ? 'Edit subnet' : 'New subnet'} onClose={onClose} wide>
      <form onSubmit={submit}>
        <div className="grid grid-cols-2 gap-x-4">
          <div>
            <label className={labelCls}>Name</label>
            <input autoFocus required className={inputCls} value={form.name} onChange={set('name')} placeholder="Services" />
          </div>
          <div>
            <label className={labelCls}>CIDR</label>
            <input required className={inputCls} value={form.cidr} onChange={set('cidr')} placeholder="10.10.30.0/24" />
          </div>
          <div>
            <label className={labelCls}>VLAN ID</label>
            <input type="number" min="1" max="4094" className={inputCls} value={form.vlan_id} onChange={set('vlan_id')} placeholder="30" />
          </div>
          <div>
            <label className={labelCls}>Gateway</label>
            <input className={inputCls} value={form.gateway} onChange={set('gateway')} placeholder="10.10.30.1" />
          </div>
          <div className="col-span-2">
            <label className={labelCls}>Default domain suffix</label>
            <input className={inputCls} value={form.domain} onChange={set('domain')} placeholder="lab.home.arpa" />
            <p className="mt-1 text-[11px] text-slate-500">
              New hosts inherit this; each host can override it individually.
            </p>
          </div>
          <div>
            <label className={labelCls}>DHCP pool start</label>
            <input className={inputCls} value={form.dhcp_start} onChange={set('dhcp_start')} placeholder="10.10.30.100" />
          </div>
          <div>
            <label className={labelCls}>DHCP pool end</label>
            <input className={inputCls} value={form.dhcp_end} onChange={set('dhcp_end')} placeholder="10.10.30.199" />
          </div>
        </div>
        <label className={labelCls}>DNS servers (comma-separated)</label>
        <input className={inputCls} value={form.dns_servers} onChange={set('dns_servers')} placeholder="10.10.30.1, 1.1.1.1" />
        <label className={labelCls}>Notes</label>
        <textarea rows={2} className={inputCls} value={form.notes} onChange={set('notes')} />
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className={secondaryBtn}>
            Cancel
          </button>
          <button type="submit" disabled={busy} className={primaryBtn}>
            {subnet ? 'Save' : 'Create'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
