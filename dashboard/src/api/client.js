import axios from 'axios'

// Default to a SAME-ORIGIN (relative) base so requests go to whatever host
// serves the dashboard (localhost:5173 in dev, the ngrok URL when tunneled).
// Vite's dev-server proxy (see vite.config.js) forwards /api and /health to
// the API on :8000 — so only ONE port needs to be exposed through ngrok.
// Set VITE_API_BASE_URL only if the API is on a separate, directly-reachable origin.
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  headers: {
    // Tells ngrok's edge to skip the browser-warning interstitial for API calls.
    // Safe to include in all environments — non-ngrok servers ignore unknown headers.
    'ngrok-skip-browser-warning': 'true',
  },
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

export async function getRootCause(failureId) {
  const res = await api.get(`/api/v1/failures/${failureId}/root-cause`)
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
  // Returns the URL string for use as an <img> src — not an async call.
  // Same-origin by default so it routes through the Vite proxy / ngrok tunnel.
  const base = import.meta.env.VITE_API_BASE_URL || ''
  return `${base}/api/v1/failures/screenshots/${screenshotId}/file`
}

export async function getAgentRuns(limit = 50) {
  const res = await api.get('/api/v1/agents/runs', { params: { limit } })
  return res.data
}

export async function getAgentRunsForFailure(failureId) {
  const res = await api.get('/api/v1/agents/runs', { params: { failure_id: failureId } })
  return res.data
}

export async function getReleaseScores(limit = 20) {
  const res = await api.get('/api/v1/releases/scores', { params: { limit } })
  return res.data
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
