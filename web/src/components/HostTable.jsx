import { useMemo, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import DeviceIcon from './DeviceIcon'
import StatusBadge from './StatusBadge'

const VIRTUALIZE_ABOVE = 200

// ip_int is the API's numeric sort key — string comparison would mis-sort IPv6
function compareIp(left, right) {
  return left.ip_int - right.ip_int
}

export default function HostTable({
  hosts,
  onRowClick,
  showSubnet = false,
  subnetNames = {},
  selected,
  onToggle,
  onToggleAll,
}) {
  const [sortDirection, setSortDirection] = useState('asc')
  const scrollRef = useRef(null)
  const selectable = Boolean(onToggle)
  const sortedHosts = useMemo(
    () =>
      [...hosts].sort((left, right) => {
        const result = compareIp(left, right)
        return sortDirection === 'asc' ? result : -result
      }),
    [hosts, sortDirection],
  )
  const allSelected =
    selectable && sortedHosts.length > 0 && sortedHosts.every((host) => selected.has(host.id))
  const virtualized = sortedHosts.length > VIRTUALIZE_ABOVE
  const rowVirtualizer = useVirtualizer({
    count: virtualized ? sortedHosts.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 41,
    overscan: 8,
    enabled: virtualized,
    initialRect: { width: 0, height: 512 },
  })
  const visibleRows = virtualized
    ? rowVirtualizer.getVirtualItems()
    : sortedHosts.map((_, index) => ({ index }))
  const columns = [
    ...(selectable ? ['2rem'] : []),
    'minmax(8rem, 1fr)',
    'minmax(10rem, 1.4fr)',
    'minmax(6rem, .7fr)',
    'minmax(6rem, .7fr)',
    'minmax(10rem, 1fr)',
    ...(showSubnet ? ['minmax(8rem, 1fr)'] : []),
    'minmax(7rem, .8fr)',
  ].join(' ')
  const gridStyle = { gridTemplateColumns: columns }

  return (
    <div
      ref={scrollRef}
      className={virtualized ? 'max-h-[32rem] overflow-auto' : 'overflow-x-auto'}
      data-testid="host-table-scroll"
      data-virtualized={virtualized}
    >
      <div
        role="table"
        aria-label="Hosts"
        aria-rowcount={sortedHosts.length + 1}
        className="min-w-[54rem] text-left text-xs"
      >
        <div role="rowgroup" className="sticky top-0 z-10 bg-slate-900">
          <div
            role="row"
            aria-rowindex={1}
            className="grid items-center border-b border-slate-700 text-slate-500"
            style={gridStyle}
          >
            {selectable && (
              <div role="columnheader" className="py-2 pr-2">
                <input
                  type="checkbox"
                  className="tlg-check"
                  checked={allSelected}
                  onChange={onToggleAll}
                  aria-label="Select all hosts"
                />
              </div>
            )}
            <div role="columnheader" aria-sort={sortDirection === 'asc' ? 'ascending' : 'descending'}>
              <button
                type="button"
                className="flex items-center gap-1 py-2 pr-4 font-medium hover:text-slate-300"
                onClick={() => setSortDirection((current) => (current === 'asc' ? 'desc' : 'asc'))}
                aria-label={`Sort IP ${sortDirection === 'asc' ? 'descending' : 'ascending'}`}
              >
                IP <span aria-hidden="true">{sortDirection === 'asc' ? '↑' : '↓'}</span>
              </button>
            </div>
            <div role="columnheader" className="py-2 pr-4 font-medium">Hostname</div>
            <div role="columnheader" className="py-2 pr-4 font-medium">Type</div>
            <div role="columnheader" className="py-2 pr-4 font-medium">Status</div>
            <div role="columnheader" className="py-2 pr-4 font-medium">MAC</div>
            {showSubnet && <div role="columnheader" className="py-2 pr-4 font-medium">Subnet</div>}
            <div role="columnheader" className="py-2 font-medium">Last seen</div>
          </div>
        </div>
        <div
          role="rowgroup"
          className="relative"
          style={virtualized ? { height: `${rowVirtualizer.getTotalSize()}px` } : undefined}
        >
          {visibleRows.map((virtualRow) => {
            const host = sortedHosts[virtualRow.index]
            return (
              <div
                key={host.id}
                ref={virtualized ? rowVirtualizer.measureElement : undefined}
                data-index={virtualized ? virtualRow.index : undefined}
                role="row"
                aria-rowindex={virtualRow.index + 2}
                tabIndex={onRowClick ? 0 : undefined}
                onClick={() => onRowClick?.(host)}
                onKeyDown={(event) => {
                  if (event.target !== event.currentTarget) return
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onRowClick?.(host)
                  }
                }}
                className="grid w-full cursor-pointer items-center border-b border-slate-800 hover:bg-slate-800/60 focus-visible:bg-slate-800/60 focus-visible:outline-none"
                style={
                  virtualized
                    ? {
                        ...gridStyle,
                        position: 'absolute',
                        top: 0,
                        left: 0,
                        transform: `translateY(${virtualRow.start}px)`,
                      }
                    : gridStyle
                }
              >
                {selectable && (
                  <div role="cell" className="py-2 pr-2" onClick={(event) => event.stopPropagation()}>
                    <input
                      type="checkbox"
                      className="tlg-check"
                      checked={selected.has(host.id)}
                      onChange={() => onToggle(host.id)}
                      aria-label={`Select ${host.hostname || host.ip}`}
                    />
                  </div>
                )}
                <div role="cell" className="py-2 pr-4 font-mono text-slate-200">{host.ip}</div>
                <div role="cell" className="py-2 pr-4 text-slate-300">
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
                </div>
                <div role="cell" className="py-2 pr-4"><DeviceIcon type={host.device_type} /></div>
                <div role="cell" className="py-2 pr-4"><StatusBadge status={host.status} /></div>
                <div role="cell" className="py-2 pr-4 font-mono text-slate-400">{host.mac || '—'}</div>
                {showSubnet && (
                  <div role="cell" className="py-2 pr-4 text-slate-400">
                    {subnetNames[host.subnet_id] || '—'}
                  </div>
                )}
                <div role="cell" className="py-2 text-slate-500">
                  {host.last_seen ? new Date(host.last_seen).toLocaleDateString() : '—'}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
