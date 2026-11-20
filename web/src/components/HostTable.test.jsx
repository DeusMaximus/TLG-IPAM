import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import HostTable from './HostTable'

vi.mock('./DeviceIcon', () => ({ default: ({ type }) => <span>{type}</span> }))

// each fixture address's `ip_int`, written out as the server sends it: nothing in web/
// parses an address
const IP_INT = { '10.0.0.1': 167772161, '10.0.0.2': 167772162, '10.0.0.10': 167772170 }

const host = (id, ip, ipInt = IP_INT[ip]) => ({
  id: String(id),
  ip,
  ip_int: ipInt,
  hostname: `host-${id}`,
  fqdn: null,
  mac: null,
  device_type: 'server',
  status: 'active',
  last_seen: null,
  subnet_id: 'subnet-1',
})

describe('HostTable', () => {
  it('sorts IP addresses numerically in both directions', async () => {
    const user = userEvent.setup()
    render(
      <HostTable hosts={[host(10, '10.0.0.10'), host(2, '10.0.0.2'), host(1, '10.0.0.1')]} />,
    )

    let rows = screen.getAllByRole('row')
    expect(within(rows[1]).getByText('10.0.0.1')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /IP/ })).toHaveAttribute('aria-sort', 'ascending')

    await user.click(screen.getByRole('button', { name: 'Sort IP descending' }))

    rows = screen.getAllByRole('row')
    expect(within(rows[1]).getByText('10.0.0.10')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /IP/ })).toHaveAttribute('aria-sort', 'descending')
  })

  it('activates rows with the keyboard', async () => {
    const user = userEvent.setup()
    const onRowClick = vi.fn()
    const rowHost = host(1, '10.0.0.1')
    render(<HostTable hosts={[rowHost]} onRowClick={onRowClick} />)

    const row = screen.getAllByRole('row')[1]
    expect(row).toHaveAttribute('aria-rowindex', '2')
    row.focus()
    await user.keyboard('{Enter}')
    await user.keyboard(' ')

    expect(onRowClick).toHaveBeenCalledTimes(2)
    expect(onRowClick).toHaveBeenCalledWith(rowHost)
  })

  it('only enables virtualization above 200 rows', () => {
    const { rerender } = render(
      <HostTable hosts={Array.from({ length: 200 }, (_, index) => host(index, `10.0.0.${index}`, 167772160 + index))} />,
    )
    expect(screen.getByTestId('host-table-scroll')).toHaveAttribute('data-virtualized', 'false')

    rerender(
      <HostTable hosts={Array.from({ length: 201 }, (_, index) => host(index, `10.0.0.${index}`, 167772160 + index))} />,
    )
    expect(screen.getByTestId('host-table-scroll')).toHaveAttribute('data-virtualized', 'true')
    expect(screen.getByRole('table', { name: 'Hosts' })).toHaveAttribute('aria-rowcount', '202')
    expect(screen.getAllByRole('row').length).toBeLessThan(202)
  })

  it('reads "not scanned" for the hosts scopeOf places outside the scan scope', () => {
    const outside = new Set(['2'])
    render(
      <HostTable
        hosts={[host(1, '10.0.0.1'), host(2, '10.0.0.2')]}
        scopeOf={(h) => (outside.has(h.id) ? 'out' : null)}
      />,
    )
    const rows = screen.getAllByRole('row')
    expect(within(rows[1]).getAllByRole('cell').at(-1)).toHaveTextContent(/^—$/)
    expect(within(rows[2]).getAllByRole('cell').at(-1)).toHaveTextContent(/^not scanned$/)
  })
})
