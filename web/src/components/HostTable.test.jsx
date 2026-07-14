import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import HostTable from './HostTable'

vi.mock('./DeviceIcon', () => ({ default: ({ type }) => <span>{type}</span> }))

const host = (id, ip) => ({
  id: String(id),
  ip,
  ip_int: ip.split('.').reduce((total, octet) => total * 256 + Number(octet), 0),
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
      <HostTable hosts={Array.from({ length: 200 }, (_, index) => host(index, `10.0.0.${index}`))} />,
    )
    expect(screen.getByTestId('host-table-scroll')).toHaveAttribute('data-virtualized', 'false')

    rerender(
      <HostTable hosts={Array.from({ length: 201 }, (_, index) => host(index, `10.0.0.${index}`))} />,
    )
    expect(screen.getByTestId('host-table-scroll')).toHaveAttribute('data-virtualized', 'true')
    expect(screen.getByRole('table', { name: 'Hosts' })).toHaveAttribute('aria-rowcount', '202')
    expect(screen.getAllByRole('row').length).toBeLessThan(202)
  })
})
