import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000',
})

export async function getFailure(id) {
  const res = await api.get(`/api/v1/failures/${id}`)
  return res.data
}

export async function getFailures(params = {}) {
  const res = await api.get('/api/v1/failures', { params })
  return res.data
}

export async function getHealSuggestion(failureId) {
  const res = await api.get(`/api/v1/failures/${failureId}/suggestion`)
  return res.data
}

export async function getHealth() {
  const res = await api.get('/health')
  return res.data
}

export async function getRecentReleaseScores(repository, limit = 5) {
  const res = await api.get('/api/v1/releases/recent', { params: { repository, limit } })
  return res.data
}

export async function getReleaseScore(commitSha, repository) {
  const params = repository ? { repository } : {}
  const res = await api.get(`/api/v1/releases/${commitSha}/score`, { params })
  return res.data
}

export async function getScreenshots(failureId) {
  const res = await api.get(`/api/v1/failures/${failureId}/screenshots`)
  return res.data
}

export function getScreenshotFileUrl(screenshotId) {
  // Returns the URL string for use as an <img> src — not an async call
  const base = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
  return `${base}/api/v1/failures/screenshots/${screenshotId}/file`
}

export async function getSummary(period = '7d') {
  const res = await api.get('/api/v1/dashboard/summary', { params: { period } })
  return res.data
}

export async function getTopFailing(days = 7) {
  const res = await api.get('/api/v1/dashboard/top-failing', { params: { days } })
  return res.data
}

export async function getTrends(days = 30) {
  const res = await api.get('/api/v1/dashboard/trends', { params: { days } })
  return res.data
}
