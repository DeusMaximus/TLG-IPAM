import { X } from 'lucide-react'

export default function Modal({ title, onClose, children, wide = false }) {
  return (
    <div
      className="fixed inset-0 z-40 flex items-start justify-center bg-black/60 pt-24"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className={`w-full ${wide ? 'max-w-xl' : 'max-w-md'} rounded-lg border border-slate-700 bg-slate-800 shadow-xl`}
      >
        <div className="flex items-center justify-between border-b border-slate-700 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  )
}

export const inputCls =
  'w-full rounded-md border border-slate-600 bg-slate-900 px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 focus:border-blue-500 focus:outline-none'
export const labelCls = 'block text-xs font-medium text-slate-400 mb-1 mt-3 first:mt-0'
export const primaryBtn =
  'rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50'
export const secondaryBtn =
  'rounded-md border border-slate-600 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-700'
export const dangerBtn =
  'rounded-md bg-red-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-600'
