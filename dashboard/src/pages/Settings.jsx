import React, { useEffect, useState } from 'react'
import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000',
})

const INTEGRATIONS = [
  {
    key: 'jenkins',
    label: 'Jenkins',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" />
        <path d="M12 6v6l4 2" />
      </svg>
    ),
    description: 'CI/CD pipeline automation',
  },
  {
    key: 'github_actions',
    label: 'GitHub Actions',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22" />
      </svg>
    ),
    description: 'GitHub workflow integration',
  },
  {
    key: 'jira',
    label: 'Jira',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <path d="M9 9l6 6M15 9l-6 6" />
      </svg>
    ),
    description: 'Issue and ticket tracking',
  },
  {
    key: 'slack',
    label: 'Slack',
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M22.08 9C19.56 1 10.44 1 7.92 9M2 9c0 2.21 1.79 4 4 4h12c2.21 0 4-1.79 4-4" />
        <circle cx="12" cy="16" r="4" />
      </svg>
    ),
    description: 'Team notifications',
  },
]

function StatusIcon({ connected }) {
  return connected ? (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 24,
        height: 24,
        borderRadius: '50%',
        background: '#0d3530',
        color: 'var(--success)',
        fontSize: 14,
        fontWeight: 700,
        flexShrink: 0,
      }}
      title="Connected"
    >
      ✓
    </span>
  ) : (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 24,
        height: 24,
        borderRadius: '50%',
        background: '#3f1515',
        color: 'var(--danger)',
        fontSize: 14,
        fontWeight: 700,
        flexShrink: 0,
      }}
      title="Disconnected"
    >
      ✗
    </span>
  )
}

function Spinner() {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
      <div
        style={{
          width: 40,
          height: 40,
          borderRadius: '50%',
          border: '4px solid var(--bg-elevated)',
          borderTopColor: 'var(--accent)',
          animation: 'spin 0.8s linear infinite',
        }}
      />
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}

function ErrorBanner({ message }) {
  return (
    <div
      style={{
        background: '#3f1515',
        border: '1px solid var(--danger)',
        color: '#fca5a5',
        padding: '12px 16px',
        borderRadius: 8,
        marginBottom: 16,
      }}
    >
      <strong>Error:</strong> {message}
    </div>
  )
}

function deriveIntegrationStatus(healthData, key) {
  if (!healthData) return { connected: false, detail: 'Health check unavailable' }

  // Try per-integration status blocks
  const integrations = healthData.integrations || healthData.services || {}
  if (integrations[key]) {
    const entry = integrations[key]
    const connected = entry.status === 'ok' || entry.status === 'connected' || entry.connected === true
    return {
      connected,
      detail: entry.detail || entry.message || entry.status || (connected ? 'Connected' : 'Disconnected'),
    }
  }

  // Fall back: presence of key anywhere in the health object
  const hasKey = Object.keys(healthData).some(k => k.toLowerCase().includes(key.toLowerCase()))
  return {
    connected: hasKey,
    detail: hasKey ? 'Service key present in health response' : 'Not reported in health response',
  }
}

export default function Settings() {
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    api.get('/health')
      .then(res => setHealth(res.data))
      .catch(err => setError(err?.response?.data?.detail || err.message || 'Failed to reach health endpoint'))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>
          Settings
        </h1>
        <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-muted)' }}>
          Integration status overview (read-only)
        </p>
      </div>

      {/* Overall health status */}
      {!loading && !error && health && (
        <div
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: '12px 16px',
            marginBottom: 24,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
          }}
        >
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: '50%',
              background: health.status === 'ok' || health.status === 'healthy' ? 'var(--success)' : 'var(--danger)',
              flexShrink: 0,
              display: 'inline-block',
            }}
          />
          <span style={{ fontSize: 13, color: 'var(--text-primary)', fontWeight: 500 }}>
            API status: <strong>{health.status || 'unknown'}</strong>
          </span>
          {health.version && (
            <span style={{ fontSize: 12, color: 'var(--text-muted)', marginLeft: 8 }}>
              v{health.version}
            </span>
          )}
        </div>
      )}

      {error && <ErrorBanner message={error} />}

      {loading ? (
        <Spinner />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 16 }}>
          {INTEGRATIONS.map(integration => {
            const { connected, detail } = deriveIntegrationStatus(health, integration.key)
            return (
              <div
                key={integration.key}
                style={{
                  background: 'var(--bg-surface)',
                  border: `1px solid ${connected ? '#0d3530' : '#3f1515'}`,
                  borderRadius: 10,
                  padding: '20px 20px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 12,
                }}
              >
                {/* Header row */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ color: connected ? 'var(--accent)' : 'var(--text-muted)' }}>
                      {integration.icon}
                    </span>
                    <div>
                      <p style={{ margin: 0, fontSize: 15, fontWeight: 600, color: 'var(--text-primary)' }}>
                        {integration.label}
                      </p>
                      <p style={{ margin: 0, fontSize: 12, color: 'var(--text-muted)' }}>
                        {integration.description}
                      </p>
                    </div>
                  </div>
                  <StatusIcon connected={connected} />
                </div>

                {/* Divider */}
                <div style={{ height: 1, background: 'var(--border)' }} />

                {/* Detail */}
                <p
                  style={{
                    margin: 0,
                    fontSize: 12,
                    color: connected ? 'var(--success)' : 'var(--text-muted)',
                    lineHeight: 1.5,
                  }}
                >
                  {detail}
                </p>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
