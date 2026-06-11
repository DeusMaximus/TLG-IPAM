const STYLES = {
  active: 'bg-emerald-950 text-emerald-300 border-emerald-800',
  reserved: 'bg-amber-950 text-amber-300 border-amber-800',
  dhcp: 'bg-blue-950 text-blue-300 border-blue-800',
  offline: 'bg-slate-800 text-slate-400 border-slate-700',
}

export default function StatusBadge({ status }) {
  return (
    <span
      className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${STYLES[status] || STYLES.offline}`}
    >
      {status}
    </span>
  )
}
