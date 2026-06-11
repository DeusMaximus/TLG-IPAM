// Minimal CSV parser: handles quoted fields, escaped quotes, and CRLF line endings.
export function parseCsv(text) {
  const rows = []
  let row = []
  let field = ''
  let inQuotes = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"'
          i++
        } else {
          inQuotes = false
        }
      } else {
        field += c
      }
    } else if (c === '"') {
      inQuotes = true
    } else if (c === ',') {
      row.push(field)
      field = ''
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++
      row.push(field)
      rows.push(row)
      row = []
      field = ''
    } else {
      field += c
    }
  }
  if (field !== '' || row.length) {
    row.push(field)
    rows.push(row)
  }
  return rows.filter((r) => r.some((cell) => cell.trim() !== ''))
}

const HOST_COLUMNS = ['ip', 'hostname', 'domain', 'mac', 'device_type', 'status', 'owner', 'notes']

// Returns { hosts, ignoredColumns } or throws with a user-readable message.
export function csvToHosts(text) {
  const rows = parseCsv(text)
  if (rows.length < 2) throw new Error('Need a header row plus at least one data row')
  const headers = rows[0].map((h) => h.trim().toLowerCase().replace(/\s+/g, '_'))
  if (!headers.includes('ip')) throw new Error('Header row must include an "ip" column')
  const ignoredColumns = headers.filter((h) => !HOST_COLUMNS.includes(h))
  const hosts = rows.slice(1).map((row) => {
    const host = {}
    headers.forEach((header, i) => {
      const value = (row[i] || '').trim()
      if (HOST_COLUMNS.includes(header) && value !== '') host[header] = value
    })
    return host
  })
  return { hosts, ignoredColumns }
}
