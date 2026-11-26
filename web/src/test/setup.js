import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Node has a Web Storage of its own: on by default in Node 25, behind
// --experimental-webstorage in Node 24. vitest's jsdom environment does not replace
// these two globals when Node already defines them, so point both at jsdom's. A test
// file that runs in the node environment has no jsdom and keeps Node's globals.
if (globalThis.jsdom) {
  for (const name of ['localStorage', 'sessionStorage']) {
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value: globalThis.jsdom.window[name],
    })
  }
}

afterEach(cleanup)
