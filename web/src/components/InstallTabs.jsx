import { useState } from 'react'

// Two tabs, Container and systemd (spec §5.6, "Guided install (M2b)"), each a panel
// the caller renders: `children(tab)` gets 'container' or 'systemd'.
export const TABS = [
  { id: 'container', label: 'Container' },
  { id: 'systemd', label: 'systemd' },
]

export default function InstallTabs({ children, initial = 'container' }) {
  const [tab, setTab] = useState(initial)
  return (
    <div className="mt-4">
      <div role="tablist" aria-label="How to run the agent" className="flex gap-1 border-b border-slate-700">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`install-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`install-panel-${t.id}`}
            onClick={() => setTab(t.id)}
            className={`-mb-px rounded-t-md border px-3 py-1.5 text-xs font-medium ${
              tab === t.id
                ? 'border-slate-700 border-b-slate-800 bg-slate-800 text-slate-100'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`install-panel-${tab}`} aria-labelledby={`install-tab-${tab}`}>
        {children(tab)}
      </div>
    </div>
  )
}

export function Prerequisites({ items }) {
  return (
    <div className="mt-3">
      <div className="text-xs font-medium text-slate-400">Prerequisites</div>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-slate-300" data-testid="prerequisites">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  )
}
