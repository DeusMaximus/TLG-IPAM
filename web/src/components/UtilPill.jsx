export function utilColour(used, usable) {
  const pct = usable ? (used / usable) * 100 : 0
  if (pct > 90) return 'bg-red-500'
  if (pct >= 70) return 'bg-amber-500'
  return 'bg-emerald-500'
}

export default function UtilPill({ used, usable }) {
  const pct = usable ? Math.min(100, (used / usable) * 100) : 0
  return (
    <span className="flex shrink-0 items-center gap-1.5">
      <span className="text-[11px] tabular-nums text-slate-500">
        {used}/{usable}
      </span>
      <span className="h-1 w-10 overflow-hidden rounded-full bg-slate-700">
        <span
          className={`block h-full rounded-full ${utilColour(used, usable)}`}
          style={{ width: `${pct}%` }}
        />
      </span>
    </span>
  )
}
