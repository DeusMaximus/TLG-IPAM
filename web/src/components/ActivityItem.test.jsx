import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ActivityItem from './ActivityItem'

const change = (extra) => ({
  id: 'c1',
  ts: '2026-11-13T12:00:00Z',
  summary: 'host pbs-01 created at 10.0.0.5',
  source: 'mcp',
  actor: null,
  ...extra,
})

describe('ActivityItem', () => {
  it('shows the actor when it is set', () => {
    render(<ActivityItem change={change({ actor: 'agent:vlan20-scanner' })} />)
    expect(screen.getByText('MCP')).toBeInTheDocument()
    expect(screen.getByTitle('Actor')).toHaveTextContent('agent:vlan20-scanner')
  })

  it('shows no actor when it is null', () => {
    render(<ActivityItem change={change()} />)
    expect(screen.getByText('host pbs-01 created at 10.0.0.5')).toBeInTheDocument()
    expect(screen.queryByTitle('Actor')).not.toBeInTheDocument()
  })
})
