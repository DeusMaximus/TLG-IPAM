export default function EmptyState({ children }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-700 px-6 py-10 text-center text-sm text-slate-400">
      {children}
    </div>
  )
}
