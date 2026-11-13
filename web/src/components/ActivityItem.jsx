const SOURCE_LABEL = { ui: 'UI', api: 'API', mcp: 'MCP' }

// One change-log entry in the dashboard's recent-activity feed. `actor` is shown
// only when set: it names who acted when that is narrower than the source.
export default function ActivityItem({ change }) {
  return (
    <div className="rounded-md border border-slate-800 bg-slate-800/50 px-3 py-2">
      <div className="text-xs text-slate-300">{change.summary}</div>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
        <span className="rounded bg-slate-700 px-1.5 py-0.5 font-medium text-slate-300">
          {SOURCE_LABEL[change.source] || change.source}
        </span>
        {change.actor && (
          <span className="break-all font-mono text-slate-400" title="Actor">
            {change.actor}
          </span>
        )}
        {new Date(change.ts).toLocaleString()}
      </div>
    </div>
  )
}
