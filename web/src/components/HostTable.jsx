import DeviceIcon from './DeviceIcon'
import StatusBadge from './StatusBadge'

export default function HostTable({
  hosts,
  onRowClick,
  showSubnet = false,
  subnetNames = {},
  selected,
  onToggle,
  onToggleAll,
}) {
  const selectable = Boolean(onToggle)
  const allSelected = selectable && hosts.length > 0 && hosts.every((h) => selected.has(h.id))

  return (
    <table className="w-full text-left text-xs">
      <thead>
        <tr className="border-b border-slate-700 text-slate-500">
          {selectable && (
            <th className="w-8 py-2 pr-2">
              <input
                type="checkbox"
                className="tlg-check"
                checked={allSelected}
                onChange={onToggleAll}
                aria-label="Select all hosts"
              />
            </th>
          )}
          <th className="py-2 pr-4 font-medium">IP</th>
          <th className="py-2 pr-4 font-medium">Hostname</th>
          <th className="py-2 pr-4 font-medium">Type</th>
          <th className="py-2 pr-4 font-medium">Status</th>
          <th className="py-2 pr-4 font-medium">MAC</th>
          {showSubnet && <th className="py-2 pr-4 font-medium">Subnet</th>}
          <th className="py-2 font-medium">Last seen</th>
        </tr>
      </thead>
      <tbody>
        {hosts.map((host) => (
          <tr
            key={host.id}
            onClick={() => onRowClick?.(host)}
            className="cursor-pointer border-b border-slate-800 hover:bg-slate-800/60"
          >
            {selectable && (
              <td className="py-2 pr-2" onClick={(e) => e.stopPropagation()}>
                <input
                  type="checkbox"
                  className="tlg-check"
                  checked={selected.has(host.id)}
                  onChange={() => onToggle(host.id)}
                  aria-label={`Select ${host.hostname || host.ip}`}
                />
              </td>
            )}
            <td className="py-2 pr-4 font-mono text-slate-200">{host.ip}</td>
            <td className="py-2 pr-4 text-slate-300">
              {host.hostname ? (
                <>
                  {host.hostname}
                  {host.fqdn && (
                    <span className="text-slate-500">{host.fqdn.slice(host.hostname.length)}</span>
                  )}
                </>
              ) : (
                '—'
              )}
            </td>
            <td className="py-2 pr-4">
              <DeviceIcon type={host.device_type} />
            </td>
            <td className="py-2 pr-4">
              <StatusBadge status={host.status} />
            </td>
            <td className="py-2 pr-4 font-mono text-slate-400">{host.mac || '—'}</td>
            {showSubnet && (
              <td className="py-2 pr-4 text-slate-400">{subnetNames[host.subnet_id] || '—'}</td>
            )}
            <td className="py-2 text-slate-500">
              {host.last_seen ? new Date(host.last_seen).toLocaleDateString() : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
