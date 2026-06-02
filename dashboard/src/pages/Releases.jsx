import React, { useEffect, useState, useMemo } from 'react'
import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000',
})

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

function scoreColor(score) {
  if (score == null) return 'var(--text-muted)'
  if (score < 4) return 'var(--success)'
  if (score <= 7) return 'var(--warning)'
  return 'var(--danger)'
}

function scoreLabel(score) {
  if (score == null) return '—'
  if (score < 4) return 'Low'
  if (score <= 7) return 'Medium'
  return 'High'
}

function RecommendationBadge({ recommendation }) {
  const isBlock = recommendation === 'BLOCK' || (recommendation || '').toUpperCase().includes('BLOCK')
  return (
    <span
      style={{
        background: isBlock ? '#3f1515' : '#0d3530',
        color: isBlock ? 'var(--danger)' : 'var(--success)',
        padding: '2px 10px',
        borderRadius: 4,
        fontSize: 12,
        fontWeight: 700,
        letterSpacing: '0.04em',
        display: 'inline-block',
      }}
    >
      {isBlock ? 'BLOCK' : 'PASS'}
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

function SummaryCard({ title, value, valueColor, sub }) {
  return (
    <div
      style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: '20px 24px',
        flex: '1 1 0',
        minWidth: 0,
      }}
    >
      <p style={{ margin: '0 0 6px', fontSize: 13, color: 'var(--text-muted)', fontWeight: 500 }}>{title}</p>
      <p style={{ margin: 0, fontSize: 32, fontWeight: 700, color: valueColor || 'var(--text-primary)', lineHeight: 1 }}>
        {value ?? '—'}
      </p>
      {sub && <p style={{ margin: '6px 0 0', fontSize: 12, color: 'var(--text-muted)' }}>{sub}</p>}
    </div>
  )
}

export default function Releases() {
  const [scores, setScores] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [expandedId, setExpandedId] = useState(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    // Derive a repository from the failures list, then fetch its release scores.
    // The /releases/recent endpoint requires a ?repository= param.
    api.get('/api/v1/failures', { params: { limit: 1 } })
      .then(res => {
        const repo = res.data?.items?.[0]?.repository || null
        if (!repo) return Promise.resolve({ data: [] })
        return api.get('/api/v1/releases/recent', { params: { repository: repo, limit: 20 } })
      })
      .then(res => setScores(Array.isArray(res.data) ? res.data : []))
      .catch(err => setError(err?.response?.data?.detail || err.message || 'Failed to load release scores'))
      .finally(() => setLoading(false))
  }, [])

  const summary = useMemo(() => {
    if (!scores.length) return { latestScore: null, totalFailures: 0, criticalCount: 0 }
    const latest = scores[0]
    const totalFailures = scores.reduce((sum, s) => sum + (s.failure_count ?? s.total_failures ?? 0), 0)
    const criticalCount = scores.filter(s => (s.risk_score ?? s.score ?? 0) > 7).length
    return {
      latestScore: latest.risk_score ?? latest.score ?? null,
      totalFailures,
      criticalCount,
    }
  }, [scores])

  const toggleRow = (id) => setExpandedId(prev => prev === id ? null : id)

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
    background: 'var(--bg-surface)',
    borderBottom: '1px solid var(--border)',
    verticalAlign: 'middle',
  }

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>
          Release Risk
        </h1>
        <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-muted)' }}>
          Risk scores and deployment recommendations per commit
        </p>
      </div>

      {/* Summary cards */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 24, flexWrap: 'wrap' }}>
        <SummaryCard
          title="Latest Score"
          value={summary.latestScore != null ? summary.latestScore.toFixed(1) : '—'}
          valueColor={scoreColor(summary.latestScore)}
          sub={summary.latestScore != null ? scoreLabel(summary.latestScore) + ' risk' : undefined}
        />
        <SummaryCard
          title="Total Failures"
          value={summary.totalFailures}
        />
        <SummaryCard
          title="Critical (score > 7)"
          value={summary.criticalCount}
          valueColor={summary.criticalCount > 0 ? 'var(--danger)' : 'var(--text-primary)'}
        />
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
          {scores.length === 0 ? (
            <p style={{ padding: 24, color: 'var(--text-muted)', margin: 0 }}>
              No release scores found.
            </p>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', background: 'var(--bg-surface)' }}>
                <thead style={{ background: 'var(--bg-surface)' }}>
                  <tr style={{ background: 'var(--bg-surface)' }}>
                    <th style={thStyle}>Commit SHA</th>
                    <th style={thStyle}>Repo</th>
                    <th style={thStyle}>Branch</th>
                    <th style={thStyle}>Score</th>
                    <th style={thStyle}>Recommendation</th>
                    <th style={thStyle}>Date</th>
                  </tr>
                </thead>
                <tbody style={{ background: 'var(--bg-surface)' }}>
                  {scores.map((row, idx) => {
                    const id = row.id ?? row.commit_sha ?? idx
                    const isExpanded = expandedId === id
                    const sha = row.commit_sha || '—'
                    const shaShort = sha.length > 8 ? sha.slice(0, 8) : sha
                    const score = row.risk_score ?? row.score
                    const categories = row.category_breakdown ?? row.classifications ?? {}

                    return (
                      <React.Fragment key={id}>
                        <tr
                          onClick={() => toggleRow(id)}
                          style={{ cursor: 'pointer', transition: 'background 150ms', background: isExpanded ? 'var(--bg-elevated)' : 'var(--bg-surface)' }}
                          onMouseEnter={e => { if (!isExpanded) e.currentTarget.style.background = 'var(--bg-elevated)' }}
                          onMouseLeave={e => { if (!isExpanded) e.currentTarget.style.background = 'var(--bg-surface)' }}
                        >
                          <td style={{ ...tdStyle, fontFamily: 'monospace', fontSize: 12 }}>
                            <span title={sha} style={{ color: 'var(--accent-light)' }}>{shaShort}</span>
                          </td>
                          <td style={{ ...tdStyle, fontSize: 12, color: 'var(--text-muted)', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {row.repository || row.repo || '—'}
                          </td>
                          <td style={{ ...tdStyle, fontSize: 12 }}>
                            {row.branch || '—'}
                          </td>
                          <td style={{ ...tdStyle, fontWeight: 700, color: scoreColor(score) }}>
                            {score != null ? score.toFixed(1) : '—'}
                          </td>
                          <td style={tdStyle}>
                            <RecommendationBadge recommendation={row.recommendation} />
                          </td>
                          <td style={{ ...tdStyle, color: 'var(--text-muted)', fontSize: 12 }}>
                            {relativeDate(row.created_at || row.scored_at)}
                          </td>
                        </tr>
                        {isExpanded && (
                          <tr style={{ background: 'var(--bg-elevated)' }}>
                            <td colSpan={6} style={{ padding: '16px 20px', background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border)' }}>
                              <p style={{ margin: '0 0 10px', fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                                Category Breakdown
                              </p>
                              {Object.keys(categories).length === 0 ? (
                                <p style={{ margin: 0, fontSize: 13, color: 'var(--text-muted)' }}>
                                  No category breakdown available.
                                </p>
                              ) : (
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
                                  {Object.entries(categories).map(([cat, count]) => (
                                    <div
                                      key={cat}
                                      style={{
                                        background: 'var(--bg-surface)',
                                        border: '1px solid var(--border)',
                                        borderRadius: 6,
                                        padding: '6px 12px',
                                        fontSize: 12,
                                      }}
                                    >
                                      <span style={{ color: 'var(--text-muted)', textTransform: 'capitalize' }}>
                                        {cat.replace(/_/g, ' ')}
                                      </span>
                                      <span style={{ marginLeft: 8, fontWeight: 700, color: 'var(--text-primary)' }}>
                                        {count}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              )}
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
