import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { deleteSection } from '../api/ipam'
import EmptyState from '../components/EmptyState'
import { secondaryBtn } from '../components/Modal'
import SectionFormModal from '../components/SectionFormModal'
import SubnetFormModal from '../components/SubnetFormModal'
import UtilPill from '../components/UtilPill'
import { useToast, useTree } from '../context'

export default function SectionView() {
  const { sectionId } = useParams()
  const { sections, subnets, refreshTree } = useTree()
  const toast = useToast()
  const navigate = useNavigate()
  const [editing, setEditing] = useState(false)
  const [addingSubnet, setAddingSubnet] = useState(false)

  const section = sections.find((s) => s.id === sectionId)
  const sectionSubnets = subnets.filter((s) => s.section_id === sectionId)

  if (!section) return null

  const removeSection = async () => {
    try {
      await deleteSection(section.id)
      toast.success(`Section ${section.name} deleted`)
      await refreshTree()
      navigate('/')
    } catch (err) {
      toast.error(err.message)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          {section.colour && (
            <span className="h-3 w-3 rounded-full" style={{ backgroundColor: section.colour }} />
          )}
          <h1 className="text-lg font-semibold text-slate-100">{section.name}</h1>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setEditing(true)} className={secondaryBtn}>
            <span className="flex items-center gap-1.5"><Pencil size={13} /> Edit</span>
          </button>
          <button
            onClick={removeSection}
            className={secondaryBtn}
            title={sectionSubnets.length ? 'Delete its subnets first' : 'Delete section'}
          >
            <span className="flex items-center gap-1.5 text-red-400"><Trash2 size={13} /> Delete</span>
          </button>
        </div>
      </div>
      {section.description && <p className="mt-1 text-sm text-slate-400">{section.description}</p>}

      <div className="mt-6 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-200">Subnets</h2>
        <button
          onClick={() => setAddingSubnet(true)}
          className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-500"
        >
          <Plus size={14} /> New subnet
        </button>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
        {sectionSubnets.length === 0 && (
          <div className="lg:col-span-2">
            <EmptyState>No subnets yet — add the first CIDR block to this section.</EmptyState>
          </div>
        )}
        {sectionSubnets.map((subnet) => (
          <Link
            key={subnet.id}
            to={`/subnets/${subnet.id}`}
            className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-3 hover:border-slate-500"
          >
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-slate-100">{subnet.name}</span>
              <UtilPill used={subnet.used} usable={subnet.usable} />
            </div>
            <div className="mt-1 flex items-center gap-3 text-xs text-slate-400">
              <code>{subnet.cidr}</code>
              {subnet.vlan_id && <span>VLAN {subnet.vlan_id}</span>}
              <span>{subnet.free} free</span>
            </div>
          </Link>
        ))}
      </div>

      {editing && <SectionFormModal section={section} onClose={() => setEditing(false)} />}
      {addingSubnet && (
        <SubnetFormModal sectionId={section.id} onClose={() => setAddingSubnet(false)} />
      )}
    </div>
  )
}
