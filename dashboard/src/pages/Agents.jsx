import React, { useEffect, useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { getAgentRuns } from '../api/client.js'

function relativeDate(isoString) {
  if (!isoString) return '—'
  const diffMs = Date.now() - new Date(isoString).getTime()
  const mins = Math.floor(diffMs / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function durationSec(ms) {
  if (ms == null) return '—'
  return `${(ms / 1000).toFixed(2)}s`
}

const STATUS_STYLES = {
  running:   { bg: '#1e3a5f', color: '#60a5fa', label: 'Running' },
  completed: { bg: '#0d3530', color: '#0D9488', label: 'Completed' },
  failed:    { bg: '#3f1515', color: '#EF4444', label: 'Failed' },
  skipped:   { bg: '#1e293b', color: '#94A3B8', label: 'Skipped' },
}

function StatusBadge({ status }) {
  const style = STATUS_STYLES[status] || STATUS_STYLES.skipped
  return (
    <span
      style={{
        backgroundColor: style.bg,
        color: style.color,
        padding: '2px 8px',
        borderRadius: 4,
        fontSize: 12,
        fontWeight: 600,
        display: 'inline-block',
        textTransform: 'capitalize',
      }}
    >
      {style.label}
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

export default function Agents() {
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [filterName, setFilterName] = useState('')
  const [filterStatus, setFilterStatus] = useState('')

  useEffect(() => {
    setLoading(true)
    setError(null)
    getAgentRuns(50)
      .then(setRuns)
      .catch(err => setError(err?.response?.data?.detail || err.message || 'Failed to load agent runs'))
      .finally(() => setLoading(false))
  }, [])

  const agentNames = useMemo(() => {
    const names = [...new Set(runs.map(r => r.agent_name).filter(Boolean))]
    return names.sort()
  }, [runs])

  const filtered = useMemo(() => {
    return runs.filter(r => {
      if (filterName && r.agent_name !== filterName) return false
      if (filterStatus && r.status !== filterStatus) return false
      return true
    })
  }, [runs, filterName, filterStatus])

  const toggleRow = (id) => setExpandedId(prev => prev === id ? null : id)

  const inputStyle = {
    background: 'var(--bg-elevated)',
    color: 'var(--text-primary)',
    border: '1px solid var(--border)',
    borderRadius: 6,
    padding: '6px 10px',
    fontSize: 13,
    outline: 'none',
    cursor: 'pointer',
  }

  const thStyle = {
    textAlign: 'left',
    padding: '10px 12px',
    fontSize: 12,
    fontWeight: 600,
    color: 'var(--text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    borderBottom: '1px solid var(--border)',
    whiteSpace: 'nowrap',
  }

  const tdStyle = {
    padding: '10px 12px',
    fontSize: 13,
    color: 'var(--text-primary)',
    borderBottom: '1px solid var(--border)',
    verticalAlign: 'middle',
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>
          Agent Runs
        </h1>
        <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
          {filtered.length} of {runs.length} runs
        </span>
      </div>

      {/* Filter bar */}
      <div
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border)',
          borderRadius: 8,
          padding: '12px 16px',
          display: 'flex',
          gap: 12,
          alignItems: 'center',
          marginBottom: 16,
          flexWrap: 'wrap',
        }}
      >
        <label style={{ fontSize: 13, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 8 }}>
          Agent
          <select value={filterName} onChange={e => setFilterName(e.target.value)} style={inputStyle}>
            <option value="">All agents</option>
            {agentNames.map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 13, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 8 }}>
          Status
          <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} style={inputStyle}>
            <option value="">All statuses</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="skipped">Skipped</option>
          </select>
        </label>
        {(filterName || filterStatus) && (
          <button
            onClick={() => { setFilterName(''); setFilterStatus('') }}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--accent-light)',
              fontSize: 13,
              cursor: 'pointer',
              padding: '4px 8px',
              borderRadius: 4,
            }}
          >
            Clear filters
          </button>
        )}
      </div>

      {error && <ErrorBanner message={error} />}

      {loading ? (
        <Spinner />
      ) : (
        <div
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border)',
            borderRadius: 8,
            overflow: 'hidden',
          }}
        >
          {filtered.length === 0 ? (
            <p style={{ padding: 24, color: 'var(--text-muted)', margin: 0 }}>
              No agent runs found.
            </p>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={thStyle}>Agent Name</th>
                    <th style={thStyle}>Status</th>
                    <th style={thStyle}>Duration</th>
                    <th style={thStyle}>Tokens Used</th>
                    <th style={thStyle}>Failure ID</th>
                    <th style={thStyle}>Timestamp</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map(run => {
                    const isExpanded = expandedId === run.id
                    return (
                      <React.Fragment key={run.id}>
                        <tr
                          onClick={() => toggleRow(run.id)}
                          style={{ cursor: 'pointer', transition: 'background 150ms' }}
                          onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-elevated)'}
                          onMouseLeave={e => e.currentTarget.style.background = isExpanded ? 'var(--bg-elevated)' : ''}
                        >
                          <td style={{ ...tdStyle, fontFamily: 'monospace', fontSize: 12 }}>
                            {run.agent_name || '—'}
                          </td>
                          <td style={tdStyle}>
                            <StatusBadge status={run.status} />
                          </td>
                          <td style={{ ...tdStyle, fontVariantNumeric: 'tabular-nums' }}>
                            {durationSec(run.duration_ms)}
                          </td>
                          <td style={{ ...tdStyle, fontVariantNumeric: 'tabular-nums' }}>
                            {run.tokens_used != null ? run.tokens_used.toLocaleString() : '—'}
                          </td>
                          <td style={tdStyle}>
                            {run.test_failure_id ? (
                              <Link
                                to={`/failures/${run.test_failure_id}`}
                                onClick={e => e.stopPropagation()}
                                style={{ color: 'var(--accent-light)', fontFamily: 'monospace', fontSize: 11 }}
                              >
                                {String(run.test_failure_id).slice(0, 8)}…
                              </Link>
                            ) : '—'}
                          </td>
                          <td style={{ ...tdStyle, color: 'var(--text-muted)', fontSize: 12 }}>
                            {relativeDate(run.created_at)}
                          </td>
                        </tr>
                        {isExpanded && (
                          <tr style={{ background: 'var(--bg-elevated)' }}>
                            <td colSpan={6} style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)' }}>
                              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                                <div>
                                  <p style={{ margin: '0 0 6px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                                    Input Summary
                                  </p>
                                  <p style={{ margin: 0, fontSize: 13, color: 'var(--text-primary)', lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                                    {run.input_summary || 'No input summary available.'}
                                  </p>
                                </div>
                                <div>
                                  <p style={{ margin: '0 0 6px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                                    Output Summary
                                  </p>
                                  <p style={{ margin: 0, fontSize: 13, color: 'var(--text-primary)', lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                                    {run.output_summary || 'No output summary available.'}
                                  </p>
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
