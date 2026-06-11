import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { listDeviceTypes, listSections, listSubnets } from './api/ipam'
import { getApiKey } from './api/client'

// App-wide reference data (sections + subnets for the sidebar tree, device types
// for icons and selects), shared so any mutation can refresh it
const TreeContext = createContext(null)

export function TreeProvider({ children }) {
  const [sections, setSections] = useState([])
  const [subnets, setSubnets] = useState([])
  const [deviceTypes, setDeviceTypes] = useState([])
  const [loaded, setLoaded] = useState(false)

  const refreshTree = useCallback(async () => {
    const [sectionsRes, subnetsRes, typesRes] = await Promise.all([
      listSections(),
      listSubnets(),
      listDeviceTypes(),
    ])
    setSections(sectionsRes.data)
    setSubnets(subnetsRes.data)
    setDeviceTypes(typesRes.data)
    setLoaded(true)
  }, [])

  useEffect(() => {
    if (getApiKey()) refreshTree().catch(() => setLoaded(true))
  }, [refreshTree])

  const value = useMemo(
    () => ({ sections, subnets, deviceTypes, loaded, refreshTree }),
    [sections, subnets, deviceTypes, loaded, refreshTree]
  )
  return <TreeContext.Provider value={value}>{children}</TreeContext.Provider>
}

export const useTree = () => useContext(TreeContext)

// Toasts
const ToastContext = createContext(null)
let toastId = 0

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const push = useCallback((message, type = 'info') => {
    const id = ++toastId
    setToasts((list) => [...list, { id, message, type }])
    setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), 4500)
  }, [])

  const value = useMemo(
    () => ({
      toast: push,
      success: (m) => push(m, 'success'),
      error: (m) => push(m, 'error'),
    }),
    [push]
  )

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`rounded-md px-4 py-2.5 text-sm shadow-lg border ${
              t.type === 'error'
                ? 'bg-red-950 border-red-800 text-red-200'
                : t.type === 'success'
                  ? 'bg-emerald-950 border-emerald-800 text-emerald-200'
                  : 'bg-slate-800 border-slate-700 text-slate-200'
            }`}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export const useToast = () => useContext(ToastContext)
