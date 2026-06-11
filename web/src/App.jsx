import { useEffect, useState } from 'react'
import { Route, Routes } from 'react-router-dom'
import { getApiKey, setApiKey } from './api/client'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import SectionView from './pages/SectionView'
import Settings from './pages/Settings'
import SubnetView from './pages/SubnetView'
import { ToastProvider, TreeProvider } from './context'

function ApiKeyGate({ onUnlock }) {
  const [key, setKey] = useState('')

  const submit = (e) => {
    e.preventDefault()
    if (!key.trim()) return
    setApiKey(key.trim())
    onUnlock()
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-900">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-lg border border-slate-700 bg-slate-800 p-6 shadow-xl"
      >
        <h1 className="text-lg font-semibold text-slate-100">TLG-IPAM</h1>
        <p className="mt-1 text-sm text-slate-400">
          Enter the API key (the <code className="text-slate-300">API_KEY</code> the server was
          started with). It is stored in this browser only.
        </p>
        <input
          autoFocus
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="API key"
          className="mt-4 w-full rounded-md border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:border-blue-500 focus:outline-none"
        />
        <button
          type="submit"
          className="mt-4 w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-500"
        >
          Connect
        </button>
      </form>
    </div>
  )
}

export default function App() {
  const [unlocked, setUnlocked] = useState(() => Boolean(getApiKey()))

  useEffect(() => {
    const onAuthError = () => setUnlocked(false)
    window.addEventListener('tlg-auth-error', onAuthError)
    return () => window.removeEventListener('tlg-auth-error', onAuthError)
  }, [])

  if (!unlocked) {
    return <ApiKeyGate onUnlock={() => window.location.reload()} />
  }

  return (
    <ToastProvider>
      <TreeProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/sections/:sectionId" element={<SectionView />} />
            <Route path="/subnets/:subnetId" element={<SubnetView />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
        </Routes>
      </TreeProvider>
    </ToastProvider>
  )
}
