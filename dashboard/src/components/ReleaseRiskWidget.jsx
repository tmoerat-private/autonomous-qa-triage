import React from 'react'

const RISK_STYLES = {
  critical: { bg: '#fee2e2', text: '#991b1b', bar: '#ef4444' },
  high:     { bg: '#ffedd5', text: '#9a3412', bar: '#f97316' },
  medium:   { bg: '#fef9c3', text: '#854d0e', bar: '#eab308' },
  low:      { bg: '#dcfce7', text: '#166534', bar: '#22c55e' },
}

export default function ReleaseRiskWidget({ scores = [] }) {
  const cardStyle = {
    backgroundColor: '#ffffff',
    borderRadius: '0.5rem',
    boxShadow: '0 1px 3px 0 rgba(0,0,0,0.1), 0 1px 2px 0 rgba(0,0,0,0.06)',
    padding: '1.5rem',
  }

  const headerStyle = {
    fontSize: '0.875rem',
    fontWeight: 600,
    color: '#374151',
    marginBottom: '1rem',
  }

  if (scores.length === 0) {
    return (
      <div style={cardStyle}>
        <h2 style={headerStyle}>Release Risk</h2>
        <p style={{ color: '#9ca3af', fontSize: '0.875rem' }}>No releases scored yet</p>
      </div>
    )
  }

  const recent = scores.slice(0, 5)

  return (
    <div style={cardStyle}>
      <h2 style={headerStyle}>Release Risk</h2>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
        {recent.map((score, i) => {
          const level = (score.risk_level || 'low').toLowerCase()
          const style = RISK_STYLES[level] || RISK_STYLES.low
          const barWidth = Math.min(Math.round(score.score), 100)

          return (
            <div
              key={i}
              title={score.risk_summary || ''}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.625rem',
              }}
            >
              {/* Commit SHA */}
              <span
                style={{
                  fontFamily: 'monospace',
                  fontSize: '0.75rem',
                  color: '#374151',
                  minWidth: '4rem',
                }}
              >
                {(score.commit_sha || '').slice(0, 7)}
              </span>

              {/* Risk level badge */}
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  padding: '0.125rem 0.5rem',
                  borderRadius: '9999px',
                  fontSize: '0.6875rem',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.03em',
                  backgroundColor: style.bg,
                  color: style.text,
                  minWidth: '4.5rem',
                  justifyContent: 'center',
                }}
              >
                {level}
              </span>

              {/* Score bar */}
              <div
                style={{
                  width: `${barWidth}px`,
                  maxWidth: '100px',
                  height: '6px',
                  borderRadius: '3px',
                  backgroundColor: style.bar,
                  flexShrink: 0,
                }}
              />

              {/* Numeric score */}
              <span
                style={{
                  fontSize: '0.75rem',
                  color: '#6b7280',
                  minWidth: '1.5rem',
                }}
              >
                {Math.round(score.score)}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
