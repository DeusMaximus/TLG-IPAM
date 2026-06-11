const STORAGE_KEY = 'tlg-ipam-key'

export const getApiKey = () => localStorage.getItem(STORAGE_KEY) || ''
export const setApiKey = (key) => localStorage.setItem(STORAGE_KEY, key)
export const clearApiKey = () => localStorage.removeItem(STORAGE_KEY)

export class ApiError extends Error {
  constructor(code, message, status) {
    super(message)
    this.code = code
    this.status = status
  }
}

export async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: {
      'X-API-Key': getApiKey(),
      'X-TLG-Client': 'ui',
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401 || res.status === 403) {
    window.dispatchEvent(new CustomEvent('tlg-auth-error'))
  }
  if (res.status === 204) return null
  let payload = null
  try {
    payload = await res.json()
  } catch {
    /* non-JSON response */
  }
  if (!res.ok) {
    const err = payload?.error || { code: `HTTP_${res.status}`, message: res.statusText }
    throw new ApiError(err.code, err.message, res.status)
  }
  return payload
}
