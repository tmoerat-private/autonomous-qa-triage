import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000',
})

export async function getSummary(period = '7d') {
  const res = await api.get('/api/v1/dashboard/summary', { params: { period } })
  return res.data
}

export async function getTrends(days = 30) {
  const res = await api.get('/api/v1/dashboard/trends', { params: { days } })
  return res.data
}

export async function getTopFailing(days = 7) {
  const res = await api.get('/api/v1/dashboard/top-failing', { params: { days } })
  return res.data
}

export async function getFailures(params = {}) {
  const res = await api.get('/api/v1/failures', { params })
  return res.data
}

export async function getFailure(id) {
  const res = await api.get(`/api/v1/failures/${id}`)
  return res.data
}

export async function getHealth() {
  const res = await api.get('/health')
  return res.data
}
