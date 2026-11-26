import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, getApiKey } from '../api/client'

// setup.js gives the tests jsdom's storage whatever Node does with Web Storage. With
// Node's switched off these pass without it, so they guard it only under
// NODE_OPTIONS=--experimental-webstorage on Node 24, or on a Node that has it on.
describe('Web Storage under test', () => {
  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it.each(['localStorage', 'sessionStorage'])('%s is jsdom’s, not Node’s', (name) => {
    expect(globalThis[name]).toBe(globalThis.jsdom.window[name])
  })

  it('api/client.js reads and sends the key a test stores', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetch)

    localStorage.setItem('tlg-ipam-key', 'test-key')
    await api('/api/v1/sections')

    expect(getApiKey()).toBe('test-key')
    expect(fetch.mock.calls[0][1].headers['X-API-Key']).toBe('test-key')
  })
})
