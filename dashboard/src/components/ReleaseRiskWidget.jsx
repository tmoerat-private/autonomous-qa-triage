import React from 'react'

// Risk levels map to theme-aware badge tones (defined in index.css). The score
// bar keeps a solid semantic color, which reads well on both light and dark.
const RISK_STYLES = {
  critical: { tone: 'red',    bar: '#ef4444' },
  high:     { tone: 'orange', bar: '#f97316' },
  medium:   { tone: 'yellow', bar: '#eab308' },
  low:      { tone: 'green',  bar: '#22c55e' },
}

export default function ReleaseRiskWidget({ scores = [] }) {
  const cardStyle = {
    background: 'var(--bg-surface)',
    border: '1px solid var(--border)',
    borderRadius: 8,
    padding: 24,
  }

  const headerStyle = {
    margin: '0 0 16px',
    fontSize: 14,
    fontWeight: 600,
    color: 'var(--text-primary)',
  }

  if (scores.length === 0) {
    return (
      <div style={cardStyle}>
        <h2 style={headerStyle}>Release Risk</h2>
        <p style={{ color: 'var(--text-muted)', fontSize: 13, margin: 0 }}>No releases scored yet</p>
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
                  fontFamily: 'var(--font-mono)',
                  fontSize: '0.75rem',
                  color: 'var(--text-primary)',
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
                  backgroundColor: `var(--badge-${style.tone}-bg)`,
                  color: `var(--badge-${style.tone}-fg)`,
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
                  color: 'var(--text-muted)',
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
