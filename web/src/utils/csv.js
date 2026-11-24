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

// The server's request-body cap (`body_limit.BODY_MAX`, brief §4.3): an import whose request
// is larger is refused whole with 413, before any row is read
export const BODY_MAX = 2 * 1024 * 1024
// the most rows one import takes (`BulkImportRequest`)
export const IMPORT_ROWS_MAX = 1000

const utf8 = new TextEncoder()
const encodedBytes = (value) => utf8.encode(JSON.stringify(value)).length

// What bulkCreateHosts would send for these rows: `bytes`, the size of its body as the
// server counts it (UTF-8 JSON), and, when that is over the cap, `splitAt`, the most rows
// a file can hold so that every run of that many consecutive rows fits one request (0 when
// a row is over the cap by itself). `splitAt` is null when the import fits.
export function importSize(hosts) {
  const sizes = hosts.map(encodedBytes)
  // `{"hosts":[` and `]}`, then one comma between rows
  const envelope = encodedBytes({ hosts: [] })
  const body = (rowBytes, rows) => envelope + rowBytes + Math.max(rows - 1, 0)
  const bytes = body(sizes.reduce((a, b) => a + b, 0), sizes.length)
  if (bytes <= BODY_MAX) return { bytes, splitAt: null }
  const everyRunFits = (rows) => {
    let sum = 0
    for (let i = 0; i < sizes.length; i++) {
      sum += sizes[i]
      if (i >= rows) sum -= sizes[i - rows]
      if (i >= rows - 1 && body(sum, rows) > BODY_MAX) return false
    }
    return true
  }
  let low = 0
  let high = Math.min(sizes.length, IMPORT_ROWS_MAX)
  while (low < high) {
    const mid = Math.ceil((low + high) / 2)
    if (everyRunFits(mid)) low = mid
    else high = mid - 1
  }
  return { bytes, splitAt: low }
}
