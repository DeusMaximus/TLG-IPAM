import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ImportModal from './ImportModal'

const bulkCreateHosts = vi.fn()
vi.mock('../api/ipam', () => ({ bulkCreateHosts: (...args) => bulkCreateHosts(...args) }))
vi.mock('../context', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
  useTree: () => ({ refreshTree: vi.fn() }),
}))

const subnet = { id: 'subnet-1', name: 'lab' }

const csv = (rows, notes) => {
  const lines = Array.from({ length: rows }, (_, i) => `10.0.${i >> 8}.${i & 255},${notes}`)
  return ['ip,notes', ...lines].join('\n')
}

const paste = (text) => fireEvent.change(screen.getByRole('textbox'), { target: { value: text } })

describe('ImportModal', () => {
  it('an import within the server cap can be sent', () => {
    render(<ImportModal subnet={subnet} onClose={() => {}} onSaved={() => {}} />)
    paste(csv(1000, 'n'.repeat(1000)))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Import 1000 host(s)' })).toBeEnabled()
  })

  it('an import over the cap says where to split the file and is not sent', () => {
    render(<ImportModal subnet={subnet} onClose={() => {}} onSaved={() => {}} />)
    // 1,000 rows of 4,096-character notes: about 4.1 MiB as one request
    paste(csv(1000, 'n'.repeat(4096)))
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent("over the server's 2 MiB limit")
    expect(alert).toHaveTextContent(/files of at most \d+ rows/)
    const rows = Number(alert.textContent.match(/at most (\d+) rows/)[1])
    // each row is `{"ip":"10.0.x.y","notes":"…"}`: 4,096 bytes of notes plus 30 or so
    expect(rows).toBeGreaterThan(490)
    expect(rows).toBeLessThan(510)
    const button = screen.getByRole('button', { name: 'Import 1000 host(s)' })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(bulkCreateHosts).not.toHaveBeenCalled()
  })
})
