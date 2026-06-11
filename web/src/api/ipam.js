import { api } from './client'

const qs = (params) => {
  const filtered = Object.fromEntries(
    Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== '')
  )
  const s = new URLSearchParams(filtered).toString()
  return s ? `?${s}` : ''
}

// sections
export const listSections = () => api('/api/v1/sections')
export const createSection = (body) => api('/api/v1/sections', { method: 'POST', body })
export const updateSection = (id, body) => api(`/api/v1/sections/${id}`, { method: 'PUT', body })
export const deleteSection = (id) => api(`/api/v1/sections/${id}`, { method: 'DELETE' })

// subnets
export const listSubnets = (params) => api(`/api/v1/subnets${qs(params)}`)
export const getSubnet = (id) => api(`/api/v1/subnets/${id}`)
export const createSubnet = (body) => api('/api/v1/subnets', { method: 'POST', body })
export const updateSubnet = (id, body) => api(`/api/v1/subnets/${id}`, { method: 'PUT', body })
export const deleteSubnet = (id, { force = false } = {}) =>
  api(`/api/v1/subnets/${id}${force ? '?force=true' : ''}`, { method: 'DELETE' })
export const getFreeIps = (id, limit) => api(`/api/v1/subnets/${id}/free${qs({ limit })}`)
export const allocateNextFree = (id, body) =>
  api(`/api/v1/subnets/${id}/next-free`, { method: 'POST', body })
export const bulkCreateHosts = (subnetId, hosts) =>
  api(`/api/v1/subnets/${subnetId}/hosts/bulk`, { method: 'POST', body: { hosts } })
export const createReservedRange = (subnetId, body) =>
  api(`/api/v1/subnets/${subnetId}/reserved`, { method: 'POST', body })
export const deleteReservedRange = (subnetId, rangeId) =>
  api(`/api/v1/subnets/${subnetId}/reserved/${rangeId}`, { method: 'DELETE' })

// hosts
export const listHosts = (params) => api(`/api/v1/hosts${qs(params)}`)
export const createHost = (body) => api('/api/v1/hosts', { method: 'POST', body })
export const updateHost = (id, body) => api(`/api/v1/hosts/${id}`, { method: 'PUT', body })
export const deleteHost = (id) => api(`/api/v1/hosts/${id}`, { method: 'DELETE' })
export const bulkDeleteHosts = (hostIds) =>
  api('/api/v1/hosts/bulk-delete', { method: 'POST', body: { host_ids: hostIds } })

// device types
export const listDeviceTypes = () => api('/api/v1/device-types')
export const createDeviceType = (body) => api('/api/v1/device-types', { method: 'POST', body })
export const updateDeviceType = (id, body) =>
  api(`/api/v1/device-types/${id}`, { method: 'PUT', body })
export const deleteDeviceType = (id) => api(`/api/v1/device-types/${id}`, { method: 'DELETE' })

// changes / misc
export const listChanges = (params) => api(`/api/v1/changes${qs(params)}`)
export const getHealth = () => api('/health')

export const HOST_STATUSES = ['active', 'reserved', 'dhcp', 'offline']
