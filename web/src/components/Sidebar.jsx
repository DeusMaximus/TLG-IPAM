import { useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { ChevronDown, ChevronRight, LayoutDashboard, Network, Plus, Settings } from 'lucide-react'
import { useTree } from '../context'
import SectionFormModal from './SectionFormModal'
import UtilPill from './UtilPill'

function NavItem({ to, icon: Icon, children, end }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `flex items-center gap-2 rounded-md px-2 py-1.5 text-sm ${
          isActive ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
        }`
      }
    >
      <Icon size={15} />
      {children}
    </NavLink>
  )
}

function SectionNode({ section, subnets }) {
  const [open, setOpen] = useState(true)
  const navigate = useNavigate()

  return (
    <div>
      <div className="group flex items-center gap-1 rounded-md px-1 py-1 hover:bg-slate-800/60">
        <button
          onClick={() => setOpen(!open)}
          className="text-slate-500 hover:text-slate-300"
          aria-label={open ? 'Collapse section' : 'Expand section'}
        >
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <button
          onClick={() => navigate(`/sections/${section.id}`)}
          className="flex flex-1 items-center gap-2 text-left text-sm font-medium text-slate-300 hover:text-slate-100"
        >
          {section.colour && (
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: section.colour }} />
          )}
          <span className="truncate">{section.name}</span>
          <span className="text-xs text-slate-500">{section.subnet_count}</span>
        </button>
      </div>
      {open && (
        <div className="ml-4 border-l border-slate-800 pl-2">
          {subnets.length === 0 && (
            <div className="px-2 py-1 text-xs text-slate-600">no subnets</div>
          )}
          {subnets.map((subnet) => (
            <NavLink
              key={subnet.id}
              to={`/subnets/${subnet.id}`}
              className={({ isActive }) =>
                `flex items-center justify-between gap-2 rounded-md px-2 py-1 text-sm ${
                  isActive
                    ? 'bg-slate-800 text-slate-100'
                    : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
                }`
              }
            >
              <span className="truncate">{subnet.name}</span>
              <UtilPill used={subnet.used} usable={subnet.usable} />
            </NavLink>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Sidebar() {
  const { sections, subnets, loaded } = useTree()
  const [addingSection, setAddingSection] = useState(false)

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-slate-800 bg-slate-950/60">
      <div className="flex items-center gap-2 px-4 py-4">
        <Network size={18} className="text-blue-500" />
        <span className="text-sm font-semibold tracking-wide text-slate-100">TLG-IPAM</span>
      </div>

      <nav className="px-3">
        <NavItem to="/" icon={LayoutDashboard} end>
          Dashboard
        </NavItem>
      </nav>

      <div className="mt-4 flex items-center justify-between px-4">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Sections
        </span>
        <button
          onClick={() => setAddingSection(true)}
          className="rounded p-0.5 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
          title="Add section"
        >
          <Plus size={14} />
        </button>
      </div>

      <div className="mt-1 flex-1 space-y-0.5 overflow-y-auto px-3 pb-4">
        {loaded && sections.length === 0 && (
          <p className="px-2 py-2 text-xs text-slate-500">
            No sections yet — create one to get started.
          </p>
        )}
        {sections.map((section) => (
          <SectionNode
            key={section.id}
            section={section}
            subnets={subnets.filter((s) => s.section_id === section.id)}
          />
        ))}
      </div>

      <nav className="border-t border-slate-800 px-3 py-3">
        <NavItem to="/settings" icon={Settings}>
          Settings
        </NavItem>
      </nav>

      {addingSection && <SectionFormModal onClose={() => setAddingSection(false)} />}
    </aside>
  )
}
